import logging
import time
from contextvars import ContextVar
from pathlib import Path

from pythonjsonlogger import jsonlogger

# ContextVar lets each async request carry its own request_id through log calls
request_id_var: ContextVar[str] = ContextVar("request_id")

logger = logging.getLogger("FinLit-Logger")
logger.setLevel(logging.INFO)

log_dir = Path(__file__).parent.parent.parent / "resources" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)

file_handler = logging.FileHandler(filename=f"{log_dir}/log.json")
formatter = jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(message)s")
file_handler.setFormatter(formatter)

logger.addHandler(file_handler)


def elapsed_ms(started: float) -> int:
    # Takes a time.perf_counter() value
    return int((time.perf_counter() - started) * 1000)


# Block reasons, so block rate can be split by which guardrail layer fired
BLOCK_FERPA_REGEX = "ferpa_regex"
BLOCK_PII_PRESIDIO = "pii_presidio"
BLOCK_INPUT_JUDGE = "input_judge"
BLOCK_OUTPUT_JUDGE = "output_judge"
BLOCK_JUDGE_ERROR = "judge_error"  # fail-closed block caused by a broken judge call


def get_extra(
    *,
    session_id: str | None = None,
    avatar: str | None = None,
    latency_ms: int | None = None,
    retrieval_ms: int | None = None,
    guard_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    top1_score: float | None = None,
    mean_score: float | None = None,
    block_reason: str | None = None,
    **kwargs,
) -> dict:
    # Fields are named here so call sites can't drift on naming
    fields = {
        "session_id": session_id,
        "avatar": avatar,
        "latency_ms": latency_ms,
        "retrieval_ms": retrieval_ms,
        "guard_ms": guard_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "top1_score": top1_score,
        "mean_score": mean_score,
        "block_reason": block_reason,
    }

    # request_id comes from the ContextVar so every line traces to one request
    extra: dict = {"request_id": request_id_var.get("no-request")}
    # Unset fields are dropped so each line only carries what it measured
    extra.update({k: v for k, v in fields.items() if v is not None})
    extra.update(kwargs)
    return extra
