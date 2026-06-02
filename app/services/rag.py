from azure.search.documents.models import VectorizedQuery
from app.services.llm import OPENAI_CLIENT, SEARCH_CLIENT, EMBED_DEPLOYMENT


def embed(text: str) -> list[float]:
    # Reuses the shared Azure OpenAI client from llm.py instead of building one per call
    response = OPENAI_CLIENT.embeddings.create(input=text, model=EMBED_DEPLOYMENT)
    return response.data[0].embedding


def retrieve(query: str, top_k: int = 5) -> list[dict]:
    vector = embed(query)

    # Hybrid search: keyword (search_text) + vector, against the shared SearchClient from llm.py
    results = SEARCH_CLIENT.search(
        search_text=query,
        vector_queries=[
            VectorizedQuery(
                vector=vector,
                k_nearest_neighbors=top_k,
                fields="text_vector",
            )
        ],
        select=["text", "lesson", "module", "source_url"],
        top=top_k,
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