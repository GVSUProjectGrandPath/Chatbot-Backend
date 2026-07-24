import uuid
import time
import json
from typing import Annotated

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, StringConstraints

from app.services.guardrails import ferpa_sanitizer, FERPA_RESPONSE, aguard_input, aguard_output
from app.services.chain import build_chain, sync_guarded_history
from app.services.logger import logger, request_id_var, get_extra
# Tracing helpers: setup_tracing() wires up the Azure AI Foundry OTel exporter at
# startup; get_tracer() returns the app-wide tracer the rest of the app uses.
from app.services.tracing import setup_tracing, get_tracer
# trace.use_span keeps one span current across the streaming generator's lifetime.
from opentelemetry import trace

# Initialize tracing at import time so the exporter is ready before the first
# request arrives. No-op when AZURE_AIPROJECT_ENDPOINT is unset (e.g. local dev).
setup_tracing()

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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(body: ChatRequest):
    # Each request gets its own id so log lines can be traced individually
    request_id_var.set(str(uuid.uuid4()))

    # Root span for the whole /chat pipeline. Every child span created in
    # guardrails.py and chain.py nests under this automatically, so each request
    # appears as a single trace tree in the AI Foundry UI.
    with get_tracer().start_as_current_span("chat_request") as span:
        span.set_attribute("session_id", body.session_id)
        span.set_attribute("avatar", body.avatar)
        # Raw user message recorded at the root so it's visible at the top level.
        span.set_attribute("gen_ai.input.message", body.message)

        # FERPA guard runs first - blocked messages never reach Azure OpenAI or the logs.
        # Returned with HTTP 200 so the widget renders it like a normal bot message.
        if ferpa_sanitizer(body.message) == "Yes":
            logger.warning("ferpa_blocked", extra=get_extra(session_id=body.session_id))
            # Record which pipeline stage stopped the request, for block auditing.
            span.set_attribute("pipeline.stage_blocked", "ferpa_regex")
            return {"message": FERPA_RESPONSE, "ferpa_blocked": True}

        # Model-based input backstop - catches PII / injection the regex can't pattern-match.
        # Returns a ready HTML message when it blocks; None means proceed.
        input_block = await aguard_input(body.message, session_id=body.session_id)
        if input_block is not None:
            span.set_attribute("pipeline.stage_blocked", "input_guardrail")
            return {"message": input_block, "ferpa_blocked": True}

        # avatar is a required field the frontend always sends; build_chain raises if it's ever
        # an unrecognized key, which the try/except below turns into a 502
        logger.info("chat_request_started", extra=get_extra(session_id=body.session_id, avatar=body.avatar))

        # session_id (frontend-owned) is the conversation/history key for the chain
        try:
            message = await build_chain(body.avatar).ainvoke(
                {"question": body.message, "session_id": body.session_id},
                config={"configurable": {"session_id": body.session_id}},
            )
        except Exception:
            logger.exception("chat_request_failed", extra=get_extra(session_id=body.session_id))
            raise HTTPException(status_code=502, detail="The assistant is temporarily unavailable. Please try again.")

        # Output guardrail - catches personalized advice / off-scope answers before they reach the student.
        # Deterministic-first, so a clean answer adds no extra LLM call.
        raw_message = message
        message = await aguard_output(body.message, raw_message, session_id=body.session_id)

        # If the guardrail rewrote the reply, sync history so a follow-up can't reference the ungated original.
        if message != raw_message:
            sync_guarded_history(body.session_id, message)

        # Record the final response so the full input->output pair sits on the root span.
        span.set_attribute("gen_ai.output.message", message)
        logger.info("chat_request_ended", extra=get_extra(session_id=body.session_id))
        return {"message": message, "ferpa_blocked": False}

@app.post("/chat/stream")
async def chat_stream(body: ChatRequest):
    # Each request gets its own id so log lines can be traced individually
    request_id_var.set(str(uuid.uuid4()))
    session_key = body.session_id
    user_role = body.avatar

    # Root span for the streaming pipeline. The real work runs inside generate()
    # AFTER this function returns the StreamingResponse, so a plain `with` block here
    # would end the span before streaming even starts. Instead we start it manually
    # and end it exactly when the stream finishes (or when a guardrail blocks early).
    # trace.use_span makes it the current span so child spans (guardrails, RAG, LLM
    # calls) nest under it — matching the /chat trace shape.
    tracer = get_tracer()
    span = tracer.start_span("chat_request")
    span.set_attribute("session_id", session_key)
    span.set_attribute("avatar", user_role)
    span.set_attribute("gen_ai.input.message", body.message)
    span.set_attribute("stream", True)

    # Current for the synchronous guardrail checks; end_on_exit=False keeps the span
    # open so generate() can continue it below.
    with trace.use_span(span, end_on_exit=False):
        # 1. FERPA Guardrail (Runs instantly before streaming)
        if ferpa_sanitizer(body.message) == "Yes":
            logger.warning("ferpa_blocked", extra=get_extra(session_id=session_key))
            # Blocked before streaming — record the stage and end the span now.
            span.set_attribute("pipeline.stage_blocked", "ferpa_regex")
            span.end()
            async def early_block():
                yield json.dumps({"type": "token", "content": FERPA_RESPONSE}) + "\n"
                yield json.dumps({"type": "done", "ferpa_blocked": True}) + "\n"
            return StreamingResponse(early_block(), media_type="application/x-ndjson")

        # 2. Presidio Input Guardrail
        input_block = await aguard_input(body.message, session_id=session_key)
        if input_block is not None:
            span.set_attribute("pipeline.stage_blocked", "input_guardrail")
            span.end()
            async def early_block():
                yield json.dumps({"type": "token", "content": input_block}) + "\n"
                yield json.dumps({"type": "done", "ferpa_blocked": True}) + "\n"
            return StreamingResponse(early_block(), media_type="application/x-ndjson")

    logger.info("chat_stream_started", extra=get_extra(session_id=session_key, avatar=user_role))

    # 3. The Generator Function
    async def generate():
        start_time = time.time()
        total_tokens = 0
        full_response = ""

        # Re-activate the root span inside the generator and end it when the generator
        # finishes (end_on_exit=True), so the span covers the entire stream lifetime.
        with trace.use_span(span, end_on_exit=True):
            try:
                async for ev in build_chain(user_role).astream_events(
                    {'question': body.message, 'session_id': session_key},
                    config={"configurable": {"session_id": session_key}},
                    version="v2",
                ):
                    kind = ev["event"]

                    # Only the final-answer call is tagged; skip the untagged query-rewrite tokens.
                    is_final = "final_response" in ev.get("tags", [])

                    if kind == "on_chat_model_stream" and is_final:
                        token = ev['data']['chunk'].content
                        if token:
                            full_response += token
                            yield json.dumps({"type": "token", "content": token}) + "\n"

                    elif kind == "on_chat_model_end" and is_final:
                        try:
                            msg = ev['data'].get('output')
                            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                                total_tokens = msg.usage_metadata.get("total_tokens", 0)
                        except Exception:
                            pass
            except Exception as exc:
                logger.exception("chat_stream_failed", extra=get_extra(session_id=session_key))
                # Record the failure on the root span so errored streams are visible in tracing.
                span.record_exception(exc)
                span.set_attribute("pipeline.stage_blocked", "stream_error")
                yield json.dumps({"type": "error", "content": "The assistant is temporarily unavailable. Please try again."}) + "\n"
                # Emit the terminal "done" so a frontend that finalizes on "done" doesn't hang on errors.
                yield json.dumps({"type": "done", "ferpa_blocked": False}) + "\n"
                return

            # 4. Output Guardrail (Runs after stream finishes)
            final_guarded = await aguard_output(body.message, full_response, session_id=session_key)

            if final_guarded != full_response:
                # Blocked by the judge: replace the streamed text and sync history so follow-ups can't reference the original.
                sync_guarded_history(session_key, final_guarded)
                yield json.dumps({"type": "replace", "content": final_guarded}) + "\n"

            yield json.dumps({"type": "done", "ferpa_blocked": False}) + "\n"

            # Record the final response so the full input->output pair sits on the root span.
            span.set_attribute("gen_ai.output.message", final_guarded)

            end_time = time.time()
            latency = end_time - start_time
            logger.info(
                "chat_stream_ended",
                extra=get_extra(
                    session_id=session_key,
                    avatar=user_role,
                    latency_seconds=round(latency, 2),
                    total_tokens=total_tokens
                )
            )

    return StreamingResponse(generate(), media_type="application/x-ndjson")
