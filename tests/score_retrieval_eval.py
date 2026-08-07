"""Retrieval eval scorer — turns a run dump into metrics and diffs them against the baseline.

Makes no Azure calls. Every metric here is computed from the k=10 dump written by
run_retrieval_eval.py, so metrics can be added or rubrics changed and re-run for free.

Usage:
    uv run python tests/score_retrieval_eval.py                          # newest dump
    uv run python tests/score_retrieval_eval.py <dump.json>
    uv run python tests/score_retrieval_eval.py <dump.json> --promote    # accept as new baseline

hit@k here means "the correct lesson is within the first k results of the dump". Because RRF fusion
is depth-dependent, a k=10 dump truncated to 5 is not identical to retrieving at top_k=5 (see the
caveat in run_retrieval_eval.py) — so read the sweep as candidate-pool recall, and only compare
runs whose config fingerprint (including top_k) matches.

Primary metric is LESSON-level matching. Module-level is reported alongside it only so the June
2026 baseline (27/27 hit@5, 24/27 top-1) stays comparable — with 6 modules, any chunk from the
right module counts as a hit, which is why that number saturated at 100%.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "resources" / "data" / "eval-runs"
CHUNKS_JSON = REPO_ROOT / "resources" / "data" / "chunks" / "chunks.json"
BASELINE_PATH = Path(__file__).resolve().parent / "retrieval_baselines.json"

K_VALUES = (1, 3, 5, 10)

# Movement smaller than this many questions is noise on a set this small, not signal.
NOISE_QUESTIONS = 2

# Config keys that must match for two runs to be comparable at all.
FINGERPRINT_KEYS = (
    "top_k",
    "search_mode",
    "semantic_reranker",
    "query_rewrite",
    "index_name",
    "embedding_model",
    "chunk_count",
    "question_count",
)


def norm(s: str | None) -> str:
    return (s or "").strip().lower()


def module_name(label: str) -> str:
    """'Module 1: Money Mindset' -> 'money mindset'. Result rows carry the bare name."""
    return norm(label.split(":", 1)[-1])


def first_hit_rank(results: list[dict], expected: str, level: str) -> int | None:
    """1-indexed rank of the first correct result, or None if it never appears."""
    for r in results:
        if level == "lesson":
            ok = norm(r.get("lesson")) == norm(expected)
        else:
            ok = module_name(expected) == norm(r.get("module"))
        if ok:
            return r["rank"]
    return None


def rate(hits: int, n: int) -> dict:
    return {"hits": hits, "n": n, "pct": round(100 * hits / n, 1) if n else 0.0}


def score_level(records: list[dict], level: str) -> dict:
    """hit@k for each k in the sweep plus MRR, at either lesson or module granularity."""
    n = len(records)
    key = "expected_lesson" if level == "lesson" else "expected_module"
    # An errored row has no results, so first_hit_rank returns None and it counts as a miss.
    ranks = [first_hit_rank(r["results"], r[key], level) for r in records]

    out: dict = {
        f"hit@{k}": rate(sum(1 for rk in ranks if rk is not None and rk <= k), n) for k in K_VALUES
    }
    out["mrr"] = round(sum(1 / rk for rk in ranks if rk) / n, 4) if n else 0.0
    return out


def score_stats(records: list[dict]) -> dict:
    """Hybrid search returns RRF scores, not cosine — read these as relative, not absolute.

    The margins matter more than the absolute top-1 score: a flat top1-top5 margin means the
    ranking is close to arbitrary and reordering it (a reranker) has something to work with.
    """
    top1, m12, m15 = [], [], []
    for rec in records:
        scores = [r["score"] for r in rec["results"] if r.get("score") is not None]
        if not scores:
            continue
        top1.append(scores[0])
        if len(scores) >= 2:
            m12.append(scores[0] - scores[1])
        if len(scores) >= 5:
            m15.append(scores[0] - scores[4])

    def pct(values: list[float], q: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        # Nearest-rank percentile — fine at n=27 and avoids interpolating over so few points.
        idx = min(len(ordered) - 1, round(q * (len(ordered) - 1)))
        return round(ordered[idx], 4)

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    return {
        "mean_top1": mean(top1),
        "min_top1": round(min(top1), 4) if top1 else None,
        "max_top1": round(max(top1), 4) if top1 else None,
        "p10_top1": pct(top1, 0.10),
        "p50_top1": pct(top1, 0.50),
        "p90_top1": pct(top1, 0.90),
        "mean_margin_top1_top2": mean(m12),
        "mean_margin_top1_top5": mean(m15),
    }


def per_module(records: list[dict]) -> dict:
    """~4-5 questions per module, so treat these as directional, not precise."""
    groups: dict[str, list[dict]] = {}
    for rec in records:
        groups.setdefault(rec["expected_module"], []).append(rec)

    return {
        mod: {
            "n": len(rows),
            "lesson_hit@5": score_level(rows, "lesson")["hit@5"],
            "lesson_hit@1": score_level(rows, "lesson")["hit@1"],
            "module_hit@5": score_level(rows, "module")["hit@5"],
            "module_hit@1": score_level(rows, "module")["hit@1"],
        }
        for mod, rows in sorted(groups.items())
    }


def lesson_coverage(records: list[dict]) -> dict:
    """Dataset health, not retrieval quality: which indexed lessons have no golden question.

    This is what justifies growing the golden set (Phase 6 of the plan).
    """
    try:
        chunks = json.loads(CHUNKS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "chunks.json unavailable — coverage not computed"}

    indexed = {(c["module"], c["lesson"]) for c in chunks}
    tested = {(module_name(r["expected_module"]), norm(r["expected_lesson"])) for r in records}
    uncovered = sorted(f"{m} / {les}" for m, les in indexed if (norm(m), norm(les)) not in tested)

    return {
        "lessons_indexed": len(indexed),
        "lessons_covered": len(indexed) - len(uncovered),
        "lessons_uncovered": len(uncovered),
        "uncovered": uncovered,
    }


def score(dump: dict) -> dict:
    records = dump["records"]
    return {
        "scored_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "n": len(records),
        "errors": sum(1 for r in records if r.get("error")),
        "lesson": score_level(records, "lesson"),
        "module": score_level(records, "module"),
        "scores": score_stats(records),
        "per_module": per_module(records),
        "coverage": lesson_coverage(records),
    }


def flat_metrics(metrics: dict) -> dict[str, tuple[float, int | None, int]]:
    """Flatten the diffable metrics to name -> (value, hits_or_None, n) for the diff table."""
    flat: dict[str, tuple[float, int | None, int]] = {}
    for level in ("lesson", "module"):
        block = metrics.get(level, {})
        for k in K_VALUES:
            r = block.get(f"hit@{k}")
            if r:
                flat[f"{level}.hit@{k}"] = (r["pct"], r["hits"], r["n"])
        if "mrr" in block:
            flat[f"{level}.mrr"] = (block["mrr"], None, metrics["n"])
    return flat


def print_report(metrics: dict, config: dict) -> None:
    n = metrics["n"]
    print("\n" + "=" * 74)
    print("RETRIEVAL EVAL")
    print("=" * 74)
    print(f"run     {config.get('run_utc')}  sha={config.get('git_sha')}")
    print(
        f"config  k={config.get('top_k')}  reranker={config.get('semantic_reranker')}  "
        f"rewrite={config.get('query_rewrite')}  index={config.get('index_name')}  "
        f"chunks={config.get('chunk_count')}"
    )
    print(
        f"noise   n={n}, 1 question = {100 / n:.1f}pp — treat any move under "
        f"{NOISE_QUESTIONS} questions as noise"
    )
    if metrics["errors"]:
        print(f"WARNING {metrics['errors']} rows errored during retrieval and score as misses")

    print("\nAccuracy (primary = lesson-level)")
    print(f"  {'metric':<14} {'lesson':>16}   {'module (legacy)':>16}")
    for k in K_VALUES:
        les, mod = metrics["lesson"][f"hit@{k}"], metrics["module"][f"hit@{k}"]
        print(
            f"  {'hit@' + str(k):<14} {les['hits']:>3}/{les['n']} ({les['pct']:>5.1f}%)   "
            f"{mod['hits']:>3}/{mod['n']} ({mod['pct']:>5.1f}%)"
        )
    print(f"  {'MRR':<14} {metrics['lesson']['mrr']:>16}   {metrics['module']['mrr']:>16}")

    print("\nScore distribution (RRF — relative, not cosine)")
    s = metrics["scores"]
    print(
        f"  top-1 mean {s['mean_top1']}   p10 {s['p10_top1']}  p50 {s['p50_top1']}  p90 {s['p90_top1']}"
        f"   range {s['min_top1']}–{s['max_top1']}"
    )
    print(
        f"  margins    top1-top2 {s['mean_margin_top1_top2']}   top1-top5 {s['mean_margin_top1_top5']}"
    )

    print("\nPer-module (directional only — ~4-5 questions each)")
    print(f"  {'module':<34} {'n':>2}  {'lesson@1':>8} {'lesson@5':>8} {'module@1':>8}")
    for mod, b in metrics["per_module"].items():
        print(
            f"  {mod[:34]:<34} {b['n']:>2}  {b['lesson_hit@1']['hits']:>8} "
            f"{b['lesson_hit@5']['hits']:>8} {b['module_hit@1']['hits']:>8}"
        )

    cov = metrics["coverage"]
    if "error" not in cov:
        print(
            f"\nCoverage  {cov['lessons_covered']}/{cov['lessons_indexed']} indexed lessons have a "
            f"golden question ({cov['lessons_uncovered']} untested)"
        )


def print_misses(dump: dict) -> None:
    print("\nLesson-level misses (correct lesson not in top 5)")
    any_miss = False
    for rec in dump["records"]:
        rank = first_hit_rank(rec["results"], rec["expected_lesson"], "lesson")
        if rank is None or rank > 5:
            any_miss = True
            got = ", ".join(f"{r['lesson']}" for r in rec["results"][:3])
            where = "not in top 10" if rank is None else f"rank {rank}"
            print(f"  {rec['question'][:62]}")
            print(f"    want: {rec['expected_lesson']}  ({where})")
            print(f"    got:  {got}")
    if not any_miss:
        print("  none")


def print_diff(metrics: dict, config: dict, baseline: dict | None) -> None:
    print("\n" + "-" * 74)
    if not baseline:
        print("No baseline recorded yet. Accept this run with --promote to create one.")
        return

    base_cfg, base_metrics = baseline.get("config", {}), baseline.get("metrics", {})
    mismatched = [
        f"{key}: baseline={base_cfg.get(key)!r} now={config.get(key)!r}"
        for key in FINGERPRINT_KEYS
        if base_cfg.get(key) != config.get(key)
    ]
    print(f"Diff vs baseline ({base_cfg.get('run_utc')}, sha={base_cfg.get('git_sha')})")
    if mismatched:
        # An intended retrieval change lands here too — the point is that it is never silent.
        print("  CONFIG CHANGED — these numbers are not a like-for-like comparison:")
        for m in mismatched:
            print(f"    {m}")

    now, base = flat_metrics(metrics), flat_metrics(base_metrics)
    print(f"\n  {'metric':<18} {'baseline':>10} {'now':>10} {'delta':>10}  verdict")
    for name, (value, hits, n) in now.items():
        if name not in base:
            print(f"  {name:<18} {'—':>10} {value:>10} {'new':>10}  new metric")
            continue
        base_value, base_hits, _ = base[name]
        delta = round(value - base_value, 4)

        # Noise is defined in questions, not percentage points, so hit rates compare counts and
        # MRR falls back to the equivalent of NOISE_QUESTIONS worth of perfect-rank movement.
        if hits is not None and base_hits is not None:
            moved = abs(hits - base_hits) >= NOISE_QUESTIONS
        else:
            moved = abs(delta) >= NOISE_QUESTIONS / max(n, 1)

        if not moved:
            verdict = "within noise"
        elif delta > 0:
            verdict = "IMPROVED"
        else:
            verdict = "REGRESSION"
        print(f"  {name:<18} {base_value:>10} {value:>10} {delta:>+10}  {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a retrieval eval dump")
    parser.add_argument(
        "dump",
        nargs="?",
        type=Path,
        default=None,
        help="run dump JSON (default: newest in resources/data/eval-runs/)",
    )
    parser.add_argument(
        "--promote", action="store_true", help="accept this run as the new baseline"
    )
    parser.add_argument("--no-misses", action="store_true", help="skip the per-question miss list")
    args = parser.parse_args()

    dump_path = args.dump
    if dump_path is None:
        # Exclude the scorer's own *_metrics.json output, which also matches retrieval_*.json.
        candidates = sorted(
            p for p in RUNS_DIR.glob("retrieval_*.json") if not p.stem.endswith("_metrics")
        )
        if not candidates:
            raise SystemExit(f"No dumps in {RUNS_DIR} — run tests/run_retrieval_eval.py first")
        dump_path = candidates[-1]

    dump = json.loads(dump_path.read_text(encoding="utf-8"))
    config, metrics = dump["config"], score(dump)

    print_report(metrics, config)
    if not args.no_misses:
        print_misses(dump)

    baseline = (
        json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.exists() else None
    )
    print_diff(metrics, config, baseline)

    # Metrics land next to the dump (gitignored). Promotion is a separate, explicit act so a bad
    # run cannot quietly become the reference.
    metrics_path = dump_path.with_name(dump_path.stem + "_metrics.json")
    metrics_path.write_text(
        json.dumps({"config": config, "metrics": metrics}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nmetrics -> {metrics_path}")

    if args.promote:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps({"config": config, "metrics": metrics}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"PROMOTED to baseline -> {BASELINE_PATH.relative_to(REPO_ROOT)} (commit this file)")
    print()


if __name__ == "__main__":
    main()
