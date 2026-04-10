import os
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
from dotenv import load_dotenv


class LLM:
    def __init__(self):
        load_dotenv()
        self.azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.azure_openai_api_key = os.getenv("AZURE_OPENAI_API_KEY")
        self.chat_model = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
        self.embedding_model = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
        self.azure_index_name = os.getenv("AZURE_SEARCH_INDEX_NAME", "finlit-modules")
        self.azure_search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        self.azure_search_api_key = AzureKeyCredential(os.getenv("AZURE_SEARCH_API_KEY"))
        self.api_version = "2024-02-01"
        self.temperature = 0.7

    def chat_llm(self):
        chat_llm = AzureChatOpenAI(
            azure_endpoint=self.azure_endpoint,
            api_key=self.azure_openai_api_key,
            azure_deployment=self.chat_model,
            api_version=self.api_version,
            temperature=self.temperature,
        )
        return chat_llm

    def embedding_llm(self):
        embedding_llm = AzureOpenAIEmbeddings(
            azure_endpoint=self.azure_endpoint,
            api_key=self.azure_openai_api_key,
            azure_deployment=self.embedding_model,
            api_version=self.api_version
        )
        return embedding_llm

    def search_client(self):
        search_client = SearchClient(
            endpoint=self.azure_search_endpoint,
            index_name=self.azure_index_name,
            credential=self.azure_search_api_key
        )
        return search_client
