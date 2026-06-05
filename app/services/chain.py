from operator import itemgetter

from azure.search.documents.models import VectorizedQuery
from langchain_core.runnables import RunnableParallel, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import trim_messages, SystemMessage, HumanMessage
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory

from app.services.llm import CHAT_LLM, OPENAI_CLIENT, SEARCH_CLIENT, EMBED_DEPLOYMENT
from app.services.avatars import AVATARS
from app.services.logger import logger, get_extra

# In-memory session history store — keyed by session_id, lives until process restart (Phase 1)
store: dict[str, ChatMessageHistory] = {}


def get_session_history(session_id: str) -> ChatMessageHistory:
    logger.info("Fetching session history", extra=get_extra())
    if session_id not in store:
        logger.info("Creating new ChatMessageHistory", extra=get_extra())
        store[session_id] = ChatMessageHistory()
    return store[session_id]


def embed(text: str) -> list[float]:
    response = OPENAI_CLIENT.embeddings.create(input=text, model=EMBED_DEPLOYMENT)
    return response.data[0].embedding


def retrieve(query: str, top_k: int = 5) -> list[dict]:
    vector = embed(query)
    results = SEARCH_CLIENT.search(
        search_text=query,
        vector_queries=[VectorizedQuery(vector=vector, k_nearest_neighbors=top_k, fields="text_vector")],
        select=["text", "lesson", "module", "source_url"],
        top=top_k,
    )
    return [
        {"text": r["text"], "lesson": r["lesson"], "module": r["module"], "score": r["@search.score"]}
        for r in results
    ]


def format_chunks(chunks: list[dict]) -> str:
    # Turns Azure AI Search results into a numbered reference block for the LLM
    logger.info(f"Formatting {len(chunks)} retrieved chunks", extra=get_extra())
    if not chunks:
        return "No specific course material was retrieved for this query."
    parts = [
        f"[{i}] {c.get('module', '')} — {c.get('lesson', '')}\n{c.get('text', '')}"
        for i, c in enumerate(chunks, 1)
    ]
    return "\n\n".join(parts)


def build_chain(avatar_key: str):
    """
    Build a streaming-capable RunnableWithMessageHistory for the given avatar.
    Avatar persona (system prompt + tone) is baked in at build time — one chain per avatar.
    """
    persona = AVATARS.get(avatar_key) or AVATARS["panda"]

    # Trims conversation history to ≤1000 tokens before passing to LLM
    trimmer = trim_messages(
        max_tokens=1000,
        strategy="last",
        token_counter=CHAT_LLM,
        include_system=True,
        allow_partial=False,
    )

    # Retrieve course chunks from Azure AI Search in parallel with trimming history
    parallel = RunnableParallel(
        question=itemgetter("question"),
        context=itemgetter("question") | RunnableLambda(retrieve) | RunnableLambda(format_chunks),
        chat_history=itemgetter("chat_history") | trimmer,
    )

    # Assemble final message list: avatar system prompt + trimmed history + user question
    def assemble_messages(inputs: dict) -> list:
        system_content = (
            f"{persona['system_prompt']}\n\n"
            "Use the following course material to ground your response. "
            "Cite the module/lesson when it adds clarity, but don't force it.\n\n"
            f"{inputs['context']}\n\n"
            "Format ALL of your responses using HTML. "
            "Use tags like <p>, <strong>, <em>, <ul>, <ol>, <li>, <br>, and <h4> where appropriate. "
            "When using <ul> or <ol>, add padding-left:20px to the list via a style attribute "
            "(e.g. <ul style=\"padding-left:20px\">). "
            "Do NOT include <html>, <head>, or <body> tags — only the inner content HTML. "
            "Keep responses clear, well-structured, and concise and. - 3 to 4 sentences maximum."
        )
        return (
            [SystemMessage(content=system_content)]
            + list(inputs["chat_history"])
            + [HumanMessage(content=inputs["question"])]
        )

    chain = parallel | RunnableLambda(assemble_messages) | CHAT_LLM | StrOutputParser()

    return RunnableWithMessageHistory(
        chain,
        get_session_history,
        input_messages_key="question",
        history_messages_key="chat_history",
    )


if __name__ == "__main__":
    import uuid

    print("Available avatars:", ", ".join(AVATARS.keys()))
    avatar = input("Pick an avatar: ").strip().lower() or "panda"
    session_id = str(uuid.uuid4())
    chain = build_chain(avatar)

    print(f"\nChatting as [{avatar}] — type 'exit' to quit\n")
    while True:
        question = input("You: ").strip()
        if question.lower() == "exit":
            break

        print("Bot: ", end="", flush=True)
        for chunk in chain.stream(
            {"question": question},
            config={"configurable": {"session_id": session_id}},
        ):
            print(chunk, end="", flush=True)
        print("\n")
