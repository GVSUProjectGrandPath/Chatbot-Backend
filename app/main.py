import uuid
from typing import Annotated

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, StringConstraints

from app.services.guardrails import ferpa_sanitizer, FERPA_RESPONSE, aguard_input, aguard_output
from app.services.chain import build_chain
from app.services.logger import logger, request_id_var, get_extra
# Tracing helpers: setup_tracing() wires up the Azure AI Foundry OTel exporter at
# startup; get_tracer() returns the app-wide tracer the rest of the app uses.
from app.services.tracing import setup_tracing, get_tracer

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
        message = await aguard_output(body.message, message, session_id=body.session_id)

        # Record the final response so the full input->output pair sits on the root span.
        span.set_attribute("gen_ai.output.message", message)
        logger.info("chat_request_ended", extra=get_extra(session_id=body.session_id))
        return {"message": message, "ferpa_blocked": False}