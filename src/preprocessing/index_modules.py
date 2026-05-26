import argparse
import csv
import json
import os
import re
import uuid

import tiktoken

# paths
CLEANED_DIR = "data/cleaned"
MANIFEST = "data/video_manifest.csv"
CHUNKS_OUT = "data/chunks/chunks.json"

#configs
CHUNK_TOKENS = 400
OVERLAP_TOKENS = 50  # overlap so we don't cut off mid-thought between chunks
ENCODING = "cl100k_base"  # same tokenizer used by text-embedding-3-small

# used to tag each chunk with a module number for filtering later
MODULE_NUMBER = {
    "Money Mindset": 1,
    "Building Healthy Habits": 2,
    "Money Management": 3,
    "Navigating Credit": 4,
    "Planning for the Future": 5,
    "Financial Independence": 6,
}


def chunk_text(text,enc):
    tokens = enc.encode(text)
    chunks = []
    start = 0 
    while start < len(tokens):
        end = start + CHUNK_TOKENS
        chunks.append(enc.decode(tokens[start:end]))
        if end >= len(tokens): break
        start += CHUNK_TOKENS - OVERLAP_TOKENS
    return chunks 


def strip_header(text):
    # clean_data.py prepends a Module/Lesson/separator header to every file
    # we don't want that in the chunks  strip it before chunking
    lines = text.splitlines()
    header_done = False
    stripped = []
    for line in lines:
        if not header_done:
            if re.match(r"^─+\s*$", line):
                header_done = True
            continue
        stripped.append(line)
    return "\n".join(stripped).strip()


def build_chunks():
    enc = tiktoken.get_encoding(ENCODING)

    # load manifest so we can tag each chunk with module/lesson metadata
    manifest = {}
    with open(MANIFEST, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            manifest[row["file_name"].strip()] = {
                "module": row["module"].strip(),
                "lesson": row["lesson"].strip(),
                "lesson_number": row["lesson_number"].strip(),
                "source_url": row["url"].strip(),
            }

    all_chunks = []
    ok, skipped = 0, 0

    for file_name, meta in manifest.items():
        path = os.path.join(CLEANED_DIR, os.path.splitext(file_name)[0] + ".txt")

        if not os.path.exists(path):
            print(f"  MISSING  {file_name}")
            skipped += 1
            continue

        with open(path, encoding="utf-8") as f:
            body = strip_header(f.read())

        if not body:
            print(f"  EMPTY    {file_name}")
            skipped += 1
            continue

        windows = chunk_text(body, enc)
        module_num = MODULE_NUMBER.get(meta["module"], 0)

        for i, text in enumerate(windows):
            all_chunks.append({
                "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{file_name}:{i}")),
                "module_number": module_num,
                "module": meta["module"],
                "lesson": meta["lesson"],
                "lesson_number": meta["lesson_number"],
                "source_url": meta["source_url"],  # video link from manifest
                "chunk_index": i,
                "chunk_count": len(windows),
                "text": text,
            })

        print(f"  OK  {len(windows):>3} chunks  {file_name}")
        ok += 1

    print(f"\nDone. {ok} lessons → {len(all_chunks)} chunks total. {skipped} skipped.")
    return all_chunks


def save_chunks(chunks):
    os.makedirs(os.path.dirname(CHUNKS_OUT), exist_ok=True)
    with open(CHUNKS_OUT, "w", encoding="utf-8") as f:
        json.dump(chunks, f, indent=2, ensure_ascii=False)
    print(f"Saved → {CHUNKS_OUT}")


def delete_index_if_exists(endpoint, key, index_name):
    from azure.search.documents.indexes import SearchIndexClient
    from azure.core.credentials import AzureKeyCredential

    client = SearchIndexClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    existing = [idx.name for idx in client.list_indexes()]
    if index_name in existing:
        client.delete_index(index_name)
        print(f"  Deleted existing index '{index_name}'.")


def create_index_if_missing(endpoint, key, index_name):
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import (
        SearchIndex, SearchField, SearchFieldDataType,
        SimpleField, SearchableField,
        VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
    )
    from azure.core.credentials import AzureKeyCredential

    client = SearchIndexClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    existing = [idx.name for idx in client.list_indexes()]
    if index_name in existing:
        print(f"  Index '{index_name}' already exists, skipping creation.")
        return

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="module_number", type=SearchFieldDataType.Int32, filterable=True),
        SearchableField(name="module", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="lesson", type=SearchFieldDataType.String),
        SimpleField(name="lesson_number", type=SearchFieldDataType.String),
        SimpleField(name="source_url", type=SearchFieldDataType.String),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32),
        SimpleField(name="chunk_count", type=SearchFieldDataType.Int32),
        SearchableField(name="text", type=SearchFieldDataType.String),
        # vector field  1536 dims matches text-embedding-3-small output
        SearchField(
            name="text_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=1536,
            vector_search_profile_name="hnsw-profile",
        ),
    ]

    # HNSW is the standard algorithm for approximate nearest neighbor search
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
        profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw")],
    )

    index = SearchIndex(name=index_name, fields=fields, vector_search=vector_search)
    client.create_index(index)
    print(f"  Created index '{index_name}'.")


def upload_to_azure(chunks, reset=False):
    from dotenv import load_dotenv
    load_dotenv()

    from openai import AzureOpenAI
    from azure.search.documents import SearchClient
    from azure.core.credentials import AzureKeyCredential

    search_endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    search_key = os.environ["AZURE_SEARCH_API_KEY"]
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "finlit-modules")

    # wipe index first to avoid stale chunks from previous runs accumulating
    if reset:
        delete_index_if_exists(search_endpoint, search_key, index_name)

    # create the index schema if this is the first run (or after a reset)
    create_index_if_missing(search_endpoint, search_key, index_name)

    openai_client = AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version="2024-02-01",
    )
    embedding_deployment = os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"]

    search_client = SearchClient(
        endpoint=search_endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(search_key),
    )

    # batch 100 at a time  Azure embedding API handles up to 2048 inputs but 100 feels safe
    BATCH = 100
    uploaded = 0

    for i in range(0, len(chunks), BATCH):
        batch = chunks[i : i + BATCH]
        response = openai_client.embeddings.create(
            input=[c["text"] for c in batch],
            model=embedding_deployment,
        )
        # attach the embedding vector to each chunk doc before uploading
        docs = [{**c, "text_vector": r.embedding} for c, r in zip(batch, response.data)]
        search_client.upload_documents(documents=docs)
        uploaded += len(docs)
        print(f"  Uploaded {uploaded}/{len(chunks)} chunks...")

    print(f"Done. {uploaded} documents uploaded to Azure AI Search.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upload", action="store_true", help="Upload chunks to Azure AI Search")
    parser.add_argument("--reset", action="store_true", help="Delete and recreate the index before uploading (clears stale docs)")
    args = parser.parse_args()

    chunks = build_chunks()
    save_chunks(chunks)

    if args.upload:
        upload_to_azure(chunks, reset=args.reset)
