import logging
from pythonjsonlogger import jsonlogger
from contextvars import ContextVar
from pathlib import Path

# ContextVar lets each async request carry its own request_id through log calls
request_id_var: ContextVar[str] = ContextVar("request_id")

logger = logging.getLogger("FinLit-Logger")
logger.setLevel(logging.INFO)

log_dir = Path(__file__).parent.parent.parent / "logs"
log_dir.mkdir(parents=True, exist_ok=True)

file_handler = logging.FileHandler(filename=f"{log_dir}/log.json")
formatter = jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(message)s")
file_handler.setFormatter(formatter)

logger.addHandler(file_handler)


def get_extra(**kwargs) -> dict:
    # Pulls request_id from the ContextVar so every log line carries it
    return {"request_id": request_id_var.get("no-request"), **kwargs}
