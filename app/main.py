import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.avatars import AVATARS, AVATAR_NAME_MAP
from app.services.ferpa import ferpa_sanitizer, FERPA_RESPONSE
from app.services.chain import build_chain
from app.services.logger import logger, request_id_var, get_extra

FERPA_BANNER = (
    "Welcome to the FinLit Assistant. This tool is here to support your financial education. "
    "To protect your privacy under FERPA and GVSU policy, please do not share your name, "
    "student ID, GVSU email, grades, financial aid details, or account numbers. "
    "Your conversation is not saved after this session ends. "
    "This assistant provides general financial education only and does not give personalized financial advice."
)

# Tracks which avatar each session chose — no message content stored here (chain owns history)
sessions: dict[str, dict] = {}

app = FastAPI(title="FinLit-Backend-API")


class SessionStartResponse(BaseModel):
    session_id: str
    message: str
    show_avatar_picker: bool


class AvatarSelectRequest(BaseModel):
    session_id: str
    avatar: str


class AvatarSelectResponse(BaseModel):
    session_id: str
    avatar: str
    welcome_message: str


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/session/start", response_model=SessionStartResponse)
def session_start():
    session_id = str(uuid.uuid4())
    sessions[session_id] = {"avatar": None}
    logger.info("session_started", extra=get_extra(session_id=session_id))
    return SessionStartResponse(
        session_id=session_id,
        message=FERPA_BANNER,
        show_avatar_picker=True,
    )


@app.post("/session/avatar", response_model=AvatarSelectResponse)
def select_avatar(body: AvatarSelectRequest):
    session = sessions.get(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found. Call /session/start first.")

    avatar_key = AVATAR_NAME_MAP.get(body.avatar.lower().strip())
    if not avatar_key:
        valid = ", ".join(v["display_name"] for v in AVATARS.values())
        raise HTTPException(status_code=400, detail=f"Unknown avatar. Choose one of: {valid}")

    session["avatar"] = avatar_key
    persona = AVATARS[avatar_key]

    welcome = (
        f"Great choice! You're a **{persona['display_name']}** — {persona['tagline']}. "
        "I'm here to help you build your financial skills. What would you like to explore today?"
    )
    logger.info("avatar_selected", extra=get_extra(session_id=body.session_id, avatar=avatar_key))
    return AvatarSelectResponse(
        session_id=body.session_id,
        avatar=avatar_key,
        welcome_message=welcome,
    )


@app.post("/chat")
async def chat(body: ChatRequest):
    session = sessions.get(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found. Call /session/start first.")
    if not session.get("avatar"):
        raise HTTPException(status_code=400, detail="Avatar not selected. Call /session/avatar first.")

    # Stamp every log line in this request with a unique request_id
    request_id_var.set(str(uuid.uuid4()))

    # FERPA guard runs before the chain — blocked messages never reach Azure
    if ferpa_sanitizer(body.message):
        logger.warning("ferpa_blocked", extra=get_extra(session_id=body.session_id))
        return {"session_id": body.session_id, "response": FERPA_RESPONSE, "ferpa_blocked": True}

    avatar_key = session["avatar"]
    logger.info("chat_request_started", extra=get_extra(session_id=body.session_id, avatar=avatar_key))

    async def generate():
        async for chunk in build_chain(avatar_key).astream(
            {"question": body.message},
            config={"configurable": {"session_id": body.session_id}},
        ):
            yield chunk
        logger.info("chat_request_ended", extra=get_extra(session_id=body.session_id))

    return StreamingResponse(generate(), media_type="text/plain")

