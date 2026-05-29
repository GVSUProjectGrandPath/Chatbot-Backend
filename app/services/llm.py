import os
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()

ENDPOINT         = os.getenv("AZURE_OPENAI_ENDPOINT")
API_KEY          = os.getenv("AZURE_OPENAI_API_KEY")
API_VERSION      = "2024-02-01"
CHAT_DEPLOYMENT  = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
EMBED_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
SEARCH_ENDPOINT  = os.getenv("AZURE_SEARCH_ENDPOINT")
SEARCH_KEY       = AzureKeyCredential(os.getenv("AZURE_SEARCH_API_KEY") or "")
SEARCH_INDEX     = os.getenv("AZURE_SEARCH_INDEX_NAME", "finlit-modules")

# Azure OpenAI raw client — used for generating embeddings
OPENAI_CLIENT = AzureOpenAI(
    azure_endpoint=ENDPOINT or "",
    api_key=API_KEY,
    api_version=API_VERSION,
)

# LangChain chat model — used by chain.py
CHAT_LLM = AzureChatOpenAI(
    azure_endpoint=ENDPOINT,
    api_key=API_KEY,
    azure_deployment=CHAT_DEPLOYMENT,
    api_version=API_VERSION,
    temperature=0.7,
    # max_tokens=300,
    model="gpt-4o-mini",  # needed so tiktoken picks the right encoding for trim_messages
)

# LangChain embedding model — available if needed for LangChain retrievers
EMBEDDING_LLM = AzureOpenAIEmbeddings(
    azure_endpoint=ENDPOINT,
    api_key=API_KEY,
    azure_deployment=EMBED_DEPLOYMENT,
    api_version=API_VERSION,
)

# Azure AI Search client — used by rag_node.py
SEARCH_CLIENT = SearchClient(
    endpoint=SEARCH_ENDPOINT or "",
    index_name=SEARCH_INDEX,
    credential=SEARCH_KEY,
)
