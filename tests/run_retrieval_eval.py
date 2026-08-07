"""Retrieval eval runner — replays the golden questions through retrieve() and dumps raw results.

This script measures nothing. It only collects. Scoring lives in score_retrieval_eval.py so that
adding a metric or changing a rubric never means re-paying Azure for retrieval.

Retrieval runs once at k=10 (not 5) so the scorer can compute the whole k sweep — hit@1/3/5/10 —
from a single dump.

CAVEAT, measured 2026-08-06: hybrid RRF fusion is depth-dependent. Requesting 10 candidates and
truncating to 5 is NOT the same result set as requesting 5 (lesson hit@5 was 25/27 retrieving at
k=5 vs 24/27 truncating a k=10 dump). So hit@k off a k=10 dump answers "is the right chunk in the
k=10 candidate pool at all, and how high" — which is what the reranker-vs-re-chunk decision needs
— but it is not a prediction of the live top_k=5 config. Compare dumps only against dumps taken at
the same --k; the scorer's config fingerprint enforces that.

Usage:
    uv run python tests/run_retrieval_eval.py                    # writes a timestamped dump
    uv run python tests/run_retrieval_eval.py --k 10 --concurrency 4

Output: resources/data/eval-runs/retrieval_<UTC timestamp>.json (gitignored), containing a config
fingerprint plus one record per golden question with all k results (rank, score, lesson, module).

Note on query rewriting: retrieve() does not rewrite, and rewrite_query() is a no-op on a fresh
session anyway (it returns the question unchanged when there is no history). Every golden row is
single-turn, so this dump is unambiguously the no-rewrite arm. See RETRIEVAL_EVAL_PLAN.md.
"""

import argparse
import asyncio
import csv
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Run as a script (not pytest), so the repo root has to be importable for `app.*`.
sys.path.insert(0, str(REPO_ROOT))

GOLDEN_CSV = REPO_ROOT / "resources" / "data" / "golden-data" / "golden_dataset_curriculum.csv"
CHUNKS_JSON = REPO_ROOT / "resources" / "data" / "chunks" / "chunks.json"
RUNS_DIR = REPO_ROOT / "resources" / "data" / "eval-runs"


def load_golden(path: Path) -> list[dict]:
    """Read the golden CSV — the single source of truth for questions, modules and lessons.

    Headers are stripped because the sibling edge-case CSV ships a leading-space ' question'.
    """
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = [{k.strip(): (v or "").strip() for k, v in row.items()} for row in reader]

    missing = [r["question"] for r in rows if not r.get("lesson") or not r.get("module")]
    if missing:
        raise SystemExit(
            f"{len(missing)} golden rows are missing module/lesson labels: {missing[:3]}"
        )
    return rows


def git_sha() -> str:
    # A score without the commit it was produced on is not reproducible.
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def build_fingerprint(k: int, rows: list[dict]) -> dict:
    """Everything that has to match for two runs to be comparable."""
    from app.services.llm import EMBED_DEPLOYMENT, SEARCH_INDEX

    try:
        chunk_count = len(json.loads(CHUNKS_JSON.read_text(encoding="utf-8")))
    except Exception:
        chunk_count = None

    return {
        "run_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "top_k": k,
        "search_mode": "hybrid (keyword search_text + vector text_vector, default RRF fusion)",
        "hybrid_weights": "none set — Azure AI Search default RRF",
        "semantic_reranker": False,
        "query_rewrite": False,
        "index_name": SEARCH_INDEX,
        "embedding_model": EMBED_DEPLOYMENT,
        "chunk_count": chunk_count,
        "golden_source": str(GOLDEN_CSV.relative_to(REPO_ROOT)),
        "question_count": len(rows),
    }


async def retrieve_one(row: dict, k: int, sem: asyncio.Semaphore, attempts: int = 4) -> dict:
    """Retrieve for one golden question.

    retrieve() is a blocking SDK call, so it runs in a thread. Azure OpenAI embeddings share a
    rate-limited deployment — a 429 partway through would otherwise corrupt a whole run, so back
    off exponentially before giving up on the row. Same pattern as run_golden_dataset.py.
    """
    from app.services.chain import retrieve

    started = time.perf_counter()
    last_error = ""
    for attempt in range(attempts):
        try:
            async with sem:
                chunks = await asyncio.to_thread(retrieve, row["question"], k)
            return {
                "question": row["question"],
                "expected_module": row["module"],
                "expected_lesson": row["lesson"],
                "error": None,
                "latency_s": round(time.perf_counter() - started, 2),
                "results": [
                    {
                        "rank": i,
                        "score": r.get("score"),
                        "lesson": r.get("lesson", ""),
                        "module": r.get("module", ""),
                        "text": r.get("text", ""),
                    }
                    for i, r in enumerate(chunks, 1)
                ],
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts - 1:
                await asyncio.sleep(5 * 2**attempt)

    # Recorded rather than raised, so one bad row does not discard the other 26.
    return {
        "question": row["question"],
        "expected_module": row["module"],
        "expected_lesson": row["lesson"],
        "error": last_error,
        "latency_s": round(time.perf_counter() - started, 2),
        "results": [],
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Collect raw retrieval results for the golden set")
    parser.add_argument(
        "--k",
        type=int,
        default=10,
        help="results to retrieve per question (>=10 enables the full k sweep)",
    )
    parser.add_argument("--concurrency", type=int, default=4, help="parallel in-flight retrievals")
    parser.add_argument("--csv", type=Path, default=GOLDEN_CSV, help="golden question source")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output JSON path (default: timestamped file in resources/data/eval-runs/)",
    )
    args = parser.parse_args()

    rows = load_golden(args.csv)
    fingerprint = build_fingerprint(args.k, rows)

    print(f"\nRetrieval run — {len(rows)} questions at k={args.k}, concurrency={args.concurrency}")
    print(
        f"index={fingerprint['index_name']}  embed={fingerprint['embedding_model']}  sha={fingerprint['git_sha']}\n"
    )

    sem = asyncio.Semaphore(args.concurrency)
    done = 0

    async def worker(row: dict) -> dict:
        nonlocal done
        record = await retrieve_one(row, args.k, sem)
        done += 1
        flag = "ERR " if record["error"] else "    "
        print(f"  [{done}/{len(rows)}] {flag}{row['question'][:66]}")
        return record

    # gather preserves input order, so the dump stays row-aligned with the CSV.
    records = await asyncio.gather(*(worker(r) for r in rows))

    errors = [r for r in records if r["error"]]
    out_path = (
        args.out or RUNS_DIR / f"retrieval_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"config": fingerprint, "records": records}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\n{len(records) - len(errors)}/{len(records)} questions retrieved -> {out_path}")
    if errors:
        print(f"WARNING: {len(errors)} rows errored; scoring will treat them as misses:")
        for r in errors:
            print(f"  {r['question'][:60]} — {r['error'][:100]}")
    print(f"\nNext: uv run python tests/score_retrieval_eval.py {out_path}\n")


if __name__ == "__main__":
    asyncio.run(main())
