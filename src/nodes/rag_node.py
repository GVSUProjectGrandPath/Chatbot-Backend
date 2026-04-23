import os
from dotenv import load_dotenv
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.core.credentials import AzureKeyCredential
from openai import AzureOpenAI
from src.llms.llm import LLM

llm_class= LLM()
chat_model= llm_class.chat_llm()
embedding_llm = llm_class.embedding_llm()
search_client= llm_class.search_client()
client= llm_class.client()


def embed(text: str) -> list[float]:
    response = client.embeddings.create(
        input = text,
        model = llm_class.embedding_model
    )
    return response.data[0].embedding


def retrieve(query:str , top_k: int = 5) -> list[dict]:
    vector = embed(query)
    results = search_client.search(
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
            "score": r["@search.score"],
        }
        for r in results 
    ]


if __name__=="__main__":
    print(retrieve("how to be financially independent"))