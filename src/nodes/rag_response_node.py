from src.llms.llm import LLM
from src.nodes.rag_node import retrieve
from src.nodes.avatar_personas_node import AVATARS

llm_class = LLM()
chat_model = llm_class.chat_llm()


def respond(query: str, avatar: str = "panda") -> str:
    # Retrieve relevant chunks for the query
    chunks = retrieve(query)

    # Build numbered context block from retrieved chunks
    context_parts = [
        f"[{i}] {chunk.get('module', '')}  {chunk.get('lesson', '')}\n{chunk.get('text', '')}"
        for i, chunk in enumerate(chunks, 1)
    ]
    context_block = (
        "\n\n".join(context_parts)
        if context_parts
        else "No specific course material was retrieved for this query."
    )

    # Get avatar persona system prompt (fall back to panda if unknown)
    persona = AVATARS.get(avatar) or AVATARS["panda"]
    system_content = (
        f"{persona['system_prompt']}\n\n"
        "Use the following course material to ground your response. "
        "Cite the module/lesson when it adds clarity, but don't force it.\n\n"
        f"{context_block}\n\n"
        "Keep your response concise — 3 to 5 sentences maximum. "
        "Focus on the single most useful insight for this student. No bullet lists, no headers."
    )

    # Call the LLM with system context + user query
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": query},
    ]
    result = chat_model.invoke(messages)
    return result.content


if __name__ == "__main__":
    print('\n')
    print(f'Question : How is my FICO score calculated?')
    print('\n')
    print(f'response: {respond("How is my FICO score calculated?", avatar="panda")}')
