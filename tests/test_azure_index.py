"""
Integration tests for the Azure AI Search index.
These hit the live Azure index so they require a configured .env file.
Run from project root: uv run pytest test/test_azure_index.py -v

Run AFTER resetting the index with:
  uv run python -m src.preprocessing.index_modules --upload --reset
"""

import json
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

CHUNKS_JSON = os.path.join(os.path.dirname(__file__), "..", "data", "chunks", "chunks.json")
EXPECTED_VECTOR_DIMS = 1536
EXPECTED_CHUNK_COUNT = 111

MODULE_EXPECTED_COUNTS = {
    "Money Mindset": 22,
    "Building Healthy Habits": 20,
    "Money Management": 19,
    "Navigating Credit": 26,
    "Planning for the Future": 8,
    "Financial Independence": 16,
}

REQUIRED_FIELDS = ["id", "module_number", "module", "lesson", "lesson_number",
                   "source_url", "chunk_index", "chunk_count", "text"]


@pytest.fixture(scope="module")
def search_client():
    from azure.search.documents import SearchClient
    from azure.core.credentials import AzureKeyCredential
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    key = os.getenv("AZURE_SEARCH_API_KEY")
    index_name = os.getenv("AZURE_SEARCH_INDEX_NAME", "finlit-modules")
    if not endpoint or not key:
        pytest.skip("Azure credentials not configured in .env")
    return SearchClient(endpoint=endpoint, index_name=index_name, credential=AzureKeyCredential(key))


@pytest.fixture(scope="module")
def index_client():
    from azure.search.documents.indexes import SearchIndexClient
    from azure.core.credentials import AzureKeyCredential
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    key = os.getenv("AZURE_SEARCH_API_KEY")
    if not endpoint or not key:
        pytest.skip("Azure credentials not configured in .env")
    return SearchIndexClient(endpoint=endpoint, credential=AzureKeyCredential(key))


@pytest.fixture(scope="module")
def all_azure_docs(search_client):
    # Pull every document from Azure (paginate through all results)
    # Note: text_vector is not retrievable via select — checked separately via index schema
    docs = []
    results = search_client.search(
        search_text="*",
        select=["id", "module", "lesson", "lesson_number", "module_number",
                "chunk_index", "chunk_count", "source_url", "text"],
        top=500,
    )
    for r in results:
        docs.append(dict(r))
    return docs


@pytest.fixture(scope="module")
def local_chunks():
    with open(CHUNKS_JSON, encoding="utf-8") as f:
        return json.load(f)


# Document count tests

class TestDocumentCount:
    def test_total_count_matches_local(self, search_client):
        count = search_client.get_document_count()
        assert count == EXPECTED_CHUNK_COUNT, (
            f"Azure has {count} documents but local chunks.json has {EXPECTED_CHUNK_COUNT}. "
            f"If count is a multiple of {EXPECTED_CHUNK_COUNT}, the index was uploaded multiple times — "
            f"run: uv run python -m src.preprocessing.index_modules --upload --reset"
        )

    def test_per_module_counts_match_expected(self, all_azure_docs):
        actual = Counter(d["module"] for d in all_azure_docs)
        for module, expected_count in MODULE_EXPECTED_COUNTS.items():
            assert actual[module] == expected_count, (
                f"Module '{module}': expected {expected_count} chunks, found {actual[module]}"
            )


# Duplicate detection tests

class TestDuplicates:
    def test_no_duplicate_text_content(self, all_azure_docs):
        texts = [d["text"] for d in all_azure_docs]
        unique = set(texts)
        duplicates = len(texts) - len(unique)
        assert duplicates == 0, (
            f"{duplicates} duplicate text chunks in Azure index. "
            f"Reset and re-upload: uv run python -m src.preprocessing.index_modules --upload --reset"
        )

    def test_no_duplicate_ids(self, all_azure_docs):
        ids = [d["id"] for d in all_azure_docs]
        assert len(ids) == len(set(ids)), "Duplicate document IDs found in Azure index"


# Field integrity tests

class TestFieldIntegrity:
    def test_all_required_fields_present(self, all_azure_docs):
        for doc in all_azure_docs:
            for field in REQUIRED_FIELDS:
                assert field in doc and doc[field] is not None, (
                    f"Document {doc.get('id', '?')} missing or null field: '{field}'"
                )

    def test_no_empty_text_fields(self, all_azure_docs):
        empty = [d for d in all_azure_docs if not d.get("text", "").strip()]
        assert not empty, f"{len(empty)} documents have empty text in Azure"

    def test_no_empty_source_urls(self, all_azure_docs):
        empty = [d for d in all_azure_docs if not d.get("source_url", "").strip()]
        assert not empty, f"{len(empty)} documents have empty source_url in Azure"

    def test_module_numbers_are_valid(self, all_azure_docs):
        valid_numbers = {1, 2, 3, 4, 5, 6}
        invalid = [d for d in all_azure_docs if d.get("module_number") not in valid_numbers]
        assert not invalid, (
            f"{len(invalid)} documents have invalid module_number: "
            f"{set(d['module_number'] for d in invalid)}"
        )


# Vector field tests
# text_vector is not retrievable via select in Azure AI Search
# Verify dimensions and existence via index schema definition instead

class TestVectorField:
    def test_vector_field_has_correct_dimensions(self, index_client):
        index_name = os.getenv("AZURE_SEARCH_INDEX_NAME", "finlit-modules")
        index = index_client.get_index(index_name)
        vector_field = next((f for f in index.fields if f.name == "text_vector"), None)
        assert vector_field is not None, "text_vector field missing from index schema"
        assert vector_field.vector_search_dimensions == EXPECTED_VECTOR_DIMS, (
            f"Expected {EXPECTED_VECTOR_DIMS} dims, got {vector_field.vector_search_dimensions}"
        )

    def test_vector_field_is_searchable(self, index_client):
        index_name = os.getenv("AZURE_SEARCH_INDEX_NAME", "finlit-modules")
        index = index_client.get_index(index_name)
        vector_field = next((f for f in index.fields if f.name == "text_vector"), None)
        assert vector_field is not None, "text_vector field missing from index schema"
        assert vector_field.searchable, "text_vector must be searchable for vector queries to work"

    def test_vector_search_returns_results(self, search_client):
        # Verify vector search actually works end-to-end by running a real embedding query
        from src.nodes.rag_node import retrieve
        results = retrieve("how to save money", top_k=3)
        assert len(results) > 0, "Vector search returned no results"
        for r in results:
            assert r.get("score", 0) > 0, "Search scores should be positive"


# Sync between local chunks.json and Azure

class TestLocalAzureSync:
    def test_azure_ids_match_local_chunks(self, all_azure_docs, local_chunks):
        azure_ids = sorted(d["id"] for d in all_azure_docs)
        local_ids = sorted(c["id"] for c in local_chunks)
        assert azure_ids == local_ids, (
            "Azure index IDs don't match local chunks.json. "
            "Local may have been regenerated without re-uploading to Azure."
        )

    def test_azure_text_matches_local_text(self, all_azure_docs, local_chunks):
        azure_by_id = {d["id"]: d["text"] for d in all_azure_docs}
        local_by_id = {c["id"]: c["text"] for c in local_chunks}
        mismatches = [
            doc_id for doc_id in local_by_id
            if azure_by_id.get(doc_id) != local_by_id[doc_id]
        ]
        assert not mismatches, (
            f"{len(mismatches)} documents have different text in Azure vs local. "
            f"Re-upload with --reset to sync."
        )