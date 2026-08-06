import json
import time
import uuid
from typing import Annotated

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.callbacks import UsageMetadataCallbackHandler
from pydantic import BaseModel, StringConstraints

from app.services.chain import build_chain, sync_guarded_history
from app.services.guardrails import (
    FERPA_RESPONSE,
    INJECTION_RESPONSE,
    JUDGE_UNAVAILABLE_RESPONSE,
    SAFETY_RESPONSE,
    aguard_input,
    aguard_output,
    ferpa_sanitizer,
)
from app.services.logger import (
    BLOCK_FERPA_REGEX,
    BLOCK_INPUT_JUDGE,
    BLOCK_JUDGE_ERROR,
    BLOCK_OUTPUT_JUDGE,
    BLOCK_PII_PRESIDIO,
    elapsed_ms,
    get_extra,
    logger,
    request_id_var,
)

# LearnWorlds widget is the only caller of /chat
ALLOWED_ORIGINS = ["https://www.rep4finlit.org"]

app = FastAPI(title="FinLit-Backend-API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST"],
    allow_headers=["*"],
)

MAX_MESSAGE_CHARS = 2000


class ChatRequest(BaseModel):
    message: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS),
    ]
    session_id: str
    avatar: str
    # tool: str = ""


# The guardrails return a canned message, not a reason code, so map the message back to a reason
INPUT_BLOCK_REASONS: dict[str, str] = {
    FERPA_RESPONSE: BLOCK_PII_PRESIDIO,
    INJECTION_RESPONSE: BLOCK_INPUT_JUDGE,
    SAFETY_RESPONSE: BLOCK_INPUT_JUDGE,
}


def input_block_reason(block_message: str) -> str:
    return INPUT_BLOCK_REASONS.get(block_message, BLOCK_INPUT_JUDGE)


def output_block_reason(guarded_message: str) -> str:
    # A judge error is a fail-closed block, so it must not be counted as a real policy block
    if guarded_message == JUDGE_UNAVAILABLE_RESPONSE:
        return BLOCK_JUDGE_ERROR
    return BLOCK_OUTPUT_JUDGE


def token_counts(usage: UsageMetadataCallbackHandler) -> dict:
    # Handler keys usage by model name; sum them for the whole request
    prompt = completion = total = 0
    for counts in usage.usage_metadata.values():
        prompt += counts.get("input_tokens", 0)
        completion += counts.get("output_tokens", 0)
        total += counts.get("total_tokens", 0)
    if not total:
        return {}
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(body: ChatRequest):
    # Each request gets its own id so log lines can be traced individually
    request_id_var.set(str(uuid.uuid4()))
    started = time.perf_counter()

    # FERPA guard runs first - blocked messages never reach Azure OpenAI or the logs.
    # Returned with HTTP 200 so the widget renders it like a normal bot message.
    if ferpa_sanitizer(body.message) == "Yes":
        logger.warning(
            "ferpa_blocked",
            extra=get_extra(
                session_id=body.session_id,
                avatar=body.avatar,
                block_reason=BLOCK_FERPA_REGEX,
                latency_ms=elapsed_ms(started),
            ),
        )
        return {"message": FERPA_RESPONSE, "ferpa_blocked": True}

    # Model-based input backstop - catches PII / injection the regex can't pattern-match.
    # Returns a ready HTML message when it blocks; None means proceed.
    input_block = await aguard_input(body.message, session_id=body.session_id)
    if input_block is not None:
        logger.warning(
            "input_guard_blocked",
            extra=get_extra(
                session_id=body.session_id,
                avatar=body.avatar,
                block_reason=input_block_reason(input_block),
                latency_ms=elapsed_ms(started),
            ),
        )
        return {"message": input_block, "ferpa_blocked": True}

    # avatar is a required field the frontend always sends; build_chain raises if it's ever
    # an unrecognized key, which the try/except below turns into a 502
    logger.info(
        "chat_request_started", extra=get_extra(session_id=body.session_id, avatar=body.avatar)
    )

    # Aggregates token usage across both LLM calls (query rewrite + final answer)
    usage = UsageMetadataCallbackHandler()

    # session_id (frontend-owned) is the conversation/history key for the chain
    try:
        message = await build_chain(body.avatar).ainvoke(
            {"question": body.message, "session_id": body.session_id},
            config={"configurable": {"session_id": body.session_id}, "callbacks": [usage]},
        )
    except Exception as exc:
        logger.exception(
            "chat_request_failed",
            extra=get_extra(
                session_id=body.session_id, avatar=body.avatar, latency_ms=elapsed_ms(started)
            ),
        )
        raise HTTPException(
            status_code=502, detail="The assistant is temporarily unavailable. Please try again."
        ) from exc

    # Output guardrail - catches personalized advice / off-scope answers before they reach the student.
    # Deterministic-first, so a clean answer adds no extra LLM call.
    raw_message = message
    guard_started = time.perf_counter()
    message = await aguard_output(body.message, raw_message, session_id=body.session_id)
    guard_ms = elapsed_ms(guard_started)

    # If the guardrail rewrote the reply, sync history so a follow-up can't reference the ungated original.
    blocked = message != raw_message
    if blocked:
        sync_guarded_history(body.session_id, message)

    logger.info(
        "chat_request_ended",
        extra=get_extra(
            session_id=body.session_id,
            avatar=body.avatar,
            latency_ms=elapsed_ms(started),
            guard_ms=guard_ms,
            block_reason=output_block_reason(message) if blocked else None,
            **token_counts(usage),
        ),
    )
    return {"message": message, "ferpa_blocked": False}


@app.post("/chat/stream")
async def chat_stream(body: ChatRequest):
    # Each request gets its own id so log lines can be traced individually
    request_id_var.set(str(uuid.uuid4()))
    started = time.perf_counter()
    session_key = body.session_id
    user_role = body.avatar

    # 1. FERPA Guardrail (Runs instantly before streaming)
    if ferpa_sanitizer(body.message) == "Yes":
        logger.warning(
            "ferpa_blocked",
            extra=get_extra(
                session_id=session_key,
                avatar=user_role,
                block_reason=BLOCK_FERPA_REGEX,
                latency_ms=elapsed_ms(started),
            ),
        )

        async def early_block():
            yield json.dumps({"type": "token", "content": FERPA_RESPONSE}) + "\n"
            yield json.dumps({"type": "done", "ferpa_blocked": True}) + "\n"

        return StreamingResponse(early_block(), media_type="application/x-ndjson")

    # 2. Presidio Input Guardrail
    input_block = await aguard_input(body.message, session_id=session_key)
    if input_block is not None:
        logger.warning(
            "input_guard_blocked",
            extra=get_extra(
                session_id=session_key,
                avatar=user_role,
                block_reason=input_block_reason(input_block),
                latency_ms=elapsed_ms(started),
            ),
        )

        async def early_block():
            yield json.dumps({"type": "token", "content": input_block}) + "\n"
            yield json.dumps({"type": "done", "ferpa_blocked": True}) + "\n"

        return StreamingResponse(early_block(), media_type="application/x-ndjson")

    logger.info("chat_stream_started", extra=get_extra(session_id=session_key, avatar=user_role))

    # 3. The Generator Function
    async def generate():
        full_response = ""
        # Aggregates token usage across both LLM calls (query rewrite + final answer)
        usage = UsageMetadataCallbackHandler()

        try:
            async for ev in build_chain(user_role).astream_events(
                {"question": body.message, "session_id": session_key},
                config={"configurable": {"session_id": session_key}, "callbacks": [usage]},
                version="v2",
            ):
                kind = ev["event"]

                # Only the final-answer call is tagged; skip the untagged query-rewrite tokens.
                is_final = "final_response" in ev.get("tags", [])

                if kind == "on_chat_model_stream" and is_final:
                    token = ev["data"]["chunk"].content
                    if token:
                        full_response += token
                        yield json.dumps({"type": "token", "content": token}) + "\n"

        except Exception:
            logger.exception(
                "chat_stream_failed",
                extra=get_extra(
                    session_id=session_key, avatar=user_role, latency_ms=elapsed_ms(started)
                ),
            )
            yield (
                json.dumps(
                    {
                        "type": "error",
                        "content": "The assistant is temporarily unavailable. Please try again.",
                    }
                )
                + "\n"
            )
            # Emit the terminal "done" so a frontend that finalizes on "done" doesn't hang on errors.
            yield json.dumps({"type": "done", "ferpa_blocked": False}) + "\n"
            return

        # 4. Output Guardrail (Runs after stream finishes)
        guard_started = time.perf_counter()
        final_guarded = await aguard_output(body.message, full_response, session_id=session_key)
        guard_ms = elapsed_ms(guard_started)

        blocked = final_guarded != full_response
        if blocked:
            # Blocked by the judge: replace the streamed text and sync history so follow-ups can't reference the original.
            sync_guarded_history(session_key, final_guarded)
            yield json.dumps({"type": "replace", "content": final_guarded}) + "\n"

        yield json.dumps({"type": "done", "ferpa_blocked": False}) + "\n"

        logger.info(
            "chat_stream_ended",
            extra=get_extra(
                session_id=session_key,
                avatar=user_role,
                latency_ms=elapsed_ms(started),
                guard_ms=guard_ms,
                block_reason=output_block_reason(final_guarded) if blocked else None,
                **token_counts(usage),
            ),
        )

    return StreamingResponse(generate(), media_type="application/x-ndjson")
