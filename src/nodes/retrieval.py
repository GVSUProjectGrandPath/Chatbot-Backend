import os
from dotenv import load_dotenv
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.core.credentials import AzureKeyCredential
from openai import AzureOpenAI

load_dotenv()

def _embed(text: str) -> list[float]:
    client = AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version="2024-02-01",
    )
    response = client.embeddings.create(
        input = text,
        model = os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"]
    )
    return response.data[0].embedding


def retrieve(query:str , top_k: int = 5) -> list[dict]:
    vector = _embed(query)

    client = SearchClient(
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        index_name=os.environ.get("AZURE_SEARCH_INDEX_NAME", "finlit-modules"),
        credential=AzureKeyCredential(os.environ["AZURE_SEARCH_API_KEY"]),
    )

    results = client.search(
        search_text = query,    # hybrid: keyword + vector
        vector_queries = [
            VectorizedQuery(
                vector=vector,
                k_nearest_neighbors=top_k,
                fields="text_vector",
            )
        ],
        select=["text", "lesson", "module", "source_url"],
        top = top_k,
    )

    return [
        {
            "text": r["text"],
            "lesson": r["lesson"],
            "module": r["module"],
            "source_url": r["source_url"],
        }
        for r in results 
    ]

