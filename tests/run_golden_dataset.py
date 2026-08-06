"""Ask every golden-dataset question to the live chat pipeline and record the answers.

Runs the real FastAPI app in-process (httpx ASGITransport) so the request goes through the
exact same path a student hits: FERPA regex -> Presidio/LLM input guard -> chain -> output guard.
Azure is NOT mocked here; this makes real Azure OpenAI + AI Search calls using the local .env.

Usage:
    uv run python tests/run_golden_dataset.py [--avatar panda] [--concurrency 4]

Output: <input>_with_actual.csv next to each source CSV, with the original columns plus
actual_answer, blocked (guardrail/FERPA block flag), and latency_s.
"""

import argparse
import asyncio
import csv
import time
import uuid
from pathlib import Path

import httpx

from app.main import app

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "resources" / "data" / "golden-data"
SOURCES = ["golden_dataset_curriculum.csv", "golden_dataset_edge_cases.csv"]


def load_rows(path: Path) -> tuple[list[dict], list[str]]:
    """Read a golden CSV. Headers are stripped because one file ships a leading-space ' question'."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = [f.strip() for f in reader.fieldnames or []]
        rows = [{k.strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    return rows, fields


async def ask(client: httpx.AsyncClient, question: str, avatar: str, attempts: int = 4) -> dict:
    """One question = one fresh session_id, so no conversation history bleeds between rows.

    A 502 here is usually an Azure OpenAI 429 (the shared deployment rate-limits easily),
    so retry with exponential backoff before giving up on the row.
    """
    started = time.perf_counter()
    last = ""
    for attempt in range(attempts):
        try:
            resp = await client.post(
                "/chat",
                json={"message": question, "session_id": f"golden-{uuid.uuid4()}", "avatar": avatar},
                timeout=120.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "actual_answer": data.get("message", ""),
                    "blocked": str(bool(data.get("ferpa_blocked"))),
                    "latency_s": round(time.perf_counter() - started, 2),
                }
            last = f"[HTTP {resp.status_code}] {resp.text[:300]}"
        except Exception as exc:  # network/timeout — recorded rather than aborting the whole run
            last = f"[EXCEPTION] {type(exc).__name__}: {exc}"
        if attempt < attempts - 1:
            await asyncio.sleep(5 * 2**attempt)
    return {"actual_answer": last, "blocked": "error", "latency_s": round(time.perf_counter() - started, 2)}


async def run_file(client: httpx.AsyncClient, path: Path, avatar: str, sem: asyncio.Semaphore, resume: bool) -> Path:
    out_path = path.with_name(path.stem + "_with_actual.csv")

    # Resume mode: keep answers already collected and only re-ask the rows that errored out.
    if resume and out_path.exists():
        rows, fields = load_rows(out_path)
        fields = [f for f in fields if f not in ("actual_answer", "blocked", "latency_s")]
        pending = [r for r in rows if r.get("blocked") == "error"]
    else:
        rows, fields = load_rows(path)
        pending = rows

    done = 0

    async def worker(row: dict) -> dict:
        nonlocal done
        async with sem:
            result = await ask(client, row["question"], avatar)
        done += 1
        print(f"  [{done}/{len(pending)}] {row['question'][:70]}")
        row.update(result)
        return row

    await asyncio.gather(*(worker(r) for r in pending))
    answered = rows

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields + ["actual_answer", "blocked", "latency_s"])
        writer.writeheader()
        writer.writerows(answered)
    return out_path


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--avatar", default="panda", help="avatar persona key used for every question")
    parser.add_argument("--concurrency", type=int, default=4, help="parallel in-flight requests")
    parser.add_argument("--files", nargs="*", default=SOURCES)
    parser.add_argument("--resume", action="store_true", help="re-ask only the rows that errored in a previous run")
    args = parser.parse_args()

    sem = asyncio.Semaphore(args.concurrency)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for name in args.files:
            path = GOLDEN_DIR / name
            print(f"\n{name} (avatar={args.avatar})")
            out = await run_file(client, path, args.avatar, sem, args.resume)
            print(f"  -> {out}")


if __name__ == "__main__":
    asyncio.run(main())