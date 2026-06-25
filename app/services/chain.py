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

REWRITE_PROMPT = (
    "Given the conversation history and a follow-up question, rewrite the follow-up "
    "into a single complete standalone question that captures the full intent. "
    "If the follow-up is already self-contained, return it unchanged. "
    "Output only the rewritten question, nothing else.\n\n"
    "Conversation:\n{history}\n\n"
    "Follow-up: {question}\n\n"
    "Standalone question:"
)

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


def rewrite_query(question: str, session_id: str) -> str:
    # If no history yet, nothing to rewrite
    history = get_session_history(session_id).messages
    if not history:
        return question

    # Only use the last 4 messages (2 turns) — enough context, cheap to process
    recent = history[-4:]
    history_text = "\n".join(
        f"{'Student' if isinstance(m, HumanMessage) else 'Bot'}: {m.content}"
        for m in recent
    )
    prompt = REWRITE_PROMPT.format(history=history_text, question=question)
    response = CHAT_LLM.invoke([HumanMessage(content=prompt)])
    rewritten = response.content.strip()
    logger.info(f"Query rewrite: '{question}' -> '{rewritten}'", extra=get_extra())
    return rewritten


def retrieve(query: str, top_k: int = 5) -> list[dict]:
    vector = embed(query)
    results = SEARCH_CLIENT.search(
        search_text=query,
        vector_queries=[VectorizedQuery(vector=vector, k_nearest_neighbors=top_k, fields="text_vector")],
        select=["text", "lesson", "module", "source_url"],
        top=top_k,
    )
    return [
        {
            "text": r["text"],
            "lesson": r["lesson"],
            "module": r["module"],
            "source_url": r.get("source_url", ""),
            "score": r["@search.score"],
        }
        for r in results
    ]


def format_chunks(chunks: list[dict]) -> str:
    # Turns Azure AI Search results into a numbered reference block for the LLM
    logger.info(f"Formatting {len(chunks)} retrieved chunks", extra=get_extra())
    if not chunks:
        return "No specific course material was retrieved for this query."

    # Chunk text blocks — no URLs here to avoid repeating the same URL for chunks from the same lesson
    parts = [
        f"[{i}] {c.get('module', '')} — {c.get('lesson', '')}\n{c.get('text', '')}"
        for i, c in enumerate(chunks, 1)
    ]

    # Deduplicated sources: one URL per unique lesson, preserving first-seen order
    seen = {}
    for c in chunks:
        key = (c.get("module", ""), c.get("lesson", ""))
        if key not in seen and c.get("source_url"):
            seen[key] = c["source_url"]

    source_lines = [f"- {mod} / {les}: {url}" for (mod, les), url in seen.items()]
    sources_block = "Sources:\n" + "\n".join(source_lines)

    return "\n\n".join(parts) + "\n\n" + sources_block


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

    # Rewrite follow-up questions into standalone queries before retrieving
    # e.g. "what about the fees?" -> "What fees are associated with credit cards?"
    # session_id is passed in inputs so rewrite_query can look up the conversation history
    def rewrite_and_retrieve(inputs: dict) -> str:
        rewritten = rewrite_query(inputs["question"], inputs.get("session_id", ""))
        chunks = retrieve(rewritten)
        return format_chunks(chunks)

    # Retrieve course chunks from Azure AI Search in parallel with trimming history
    parallel = RunnableParallel(
        question=itemgetter("question"),
        context=RunnableLambda(rewrite_and_retrieve),
        chat_history=itemgetter("chat_history") | trimmer,
    )

    # Assemble final message list: avatar system prompt + trimmed history + user question
    def assemble_messages(inputs: dict) -> list:
        system_content = (
            f"{persona['system_prompt']}\n\n"
            "Use the following course material to ground your response. "
            "Cite the module/lesson when it adds clarity, but don't force it.\n\n"
            f"{inputs['context']}\n\n"
            "Format all responses using Markdown. "
            "Use **bold** for key terms or emphasis, *italics* sparingly, "
            "headers (## or ###) only when organizing multi-section responses, "
            "and bullet or numbered lists for any enumerated items. "
            "Use line breaks between sections for readability. "
            "Do not use code blocks. "
            "When referencing course material, use the URLs from the Sources list at the end of the context to create Markdown hyperlinks — format them as [Lesson Name](url). "
            "Keep responses clear, well-structured, and concise — 3 to 4 sentences maximum."
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
