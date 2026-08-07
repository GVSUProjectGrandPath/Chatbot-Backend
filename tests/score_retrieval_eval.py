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

A golden row may list several acceptable labels separated by "|" — some student questions are
genuinely answered by more than one lesson, and forcing a single label grades a correct retrieval
as a miss. Any listed label counts as a hit.

Lesson-level matching compares the (module, lesson) PAIR, not the lesson name alone, because
"Know Your Rights" is two different lessons — Module 3 (workplace discrimination, pay transparency)
and Module 4 (FCRA, credit report disputes). Matching on the name alone scored a Module 3 question
as a hit when retrieval returned the Module 4 lesson. A golden row whose module label does not
match its lesson can therefore never hit, so every pair is validated against chunks.json at score
time and mismatches are reported loudly rather than silently counting as misses.

Labels are re-read from the golden CSV at score time rather than trusted from the dump, so
correcting a label is a rubric change that costs nothing (see the module docstring rationale in
run_retrieval_eval.py). Pass --dump-labels to score against the labels as they were when the run
was taken instead.

The same applies to the CSV's `cohort` and `type` columns, also read at score time:

  cohort  core27 = the original 27 questions the accepted baseline was measured on; phase6 = the
          173 added in Phase 6. The core27 subset is scored separately and diffed against the
          baseline as a continuity check, so growing the set from 27 to 200 does not throw away
          every historical comparison.
  type    standard | vague | typo. Vague one-liners and misspellings are reported separately so
          they do not drag the headline number for reasons that have nothing to do with retrieval.
"""

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "resources" / "data" / "eval-runs"
CHUNKS_JSON = REPO_ROOT / "resources" / "data" / "chunks" / "chunks.json"
GOLDEN_CSV = REPO_ROOT / "resources" / "data" / "golden-data" / "golden_dataset_curriculum.csv"
BASELINE_PATH = Path(__file__).resolve().parent / "retrieval_baselines.json"

# Separates alternative acceptable labels within one golden cell.
LABEL_SEP = "|"

K_VALUES = (1, 3, 5, 10)

# Floor for the noise threshold. Below ~2 questions nothing is ever a finding regardless of n.
NOISE_QUESTIONS = 2

# The cohort the accepted baseline was measured on, kept scoreable as a subset after the set grew.
BASELINE_COHORT = "core27"

# Reported separately from the headline: these move for reasons unrelated to retrieval quality.
HARD_TYPES = ("vague", "typo")


def noise_questions(n: int, pct: float) -> int:
    """How many questions a metric must move before it counts as signal, at this n and rate.

    A fixed 2-question floor was right at n=27 (7.4pp) but far too tight at n=200, where 2
    questions is 1pp — well inside sampling noise. This scales with one standard error of a
    binomial at the observed rate: 2 questions at n=27/p=0.815, 5 at n=200/p=0.815.

    Deliberately conservative and unpaired — a real before/after on the SAME questions is a paired
    comparison, for which McNemar (see the sample-size table in RETRIEVAL_EVAL_PLAN.md) is the
    honest test. This only decides whether a diff line is worth reading, not whether a change ships.
    """
    if n <= 0:
        return NOISE_QUESTIONS
    p = min(max(pct / 100, 0.0), 1.0)
    return max(NOISE_QUESTIONS, round((n * p * (1 - p)) ** 0.5))


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


def accepted(expected: str, level: str) -> set[str]:
    """Split a golden cell into the set of labels that count as a hit, normalised for comparison."""
    parts = [p for p in (expected or "").split(LABEL_SEP) if p.strip()]
    return {norm(p) if level == "lesson" else module_name(p) for p in parts}


def accepted_pairs(expected_module: str, expected_lesson: str) -> set[tuple[str, str]]:
    """(module, lesson) pairs that count as a lesson-level hit, normalised for comparison.

    The cross product of the row's acceptable modules and lessons. For a multi-label row this can
    include pairs that do not exist in the index (module 1's lesson crossed with module 3) — those
    are inert, because no result can carry them.
    """
    return {
        (mod, les)
        for mod in accepted(expected_module, "module")
        for les in accepted(expected_lesson, "lesson")
    }


def first_hit_rank(
    results: list[dict], expected: str, level: str, expected_module: str | None = None
) -> int | None:
    """1-indexed rank of the first result matching ANY acceptable label, or None if never.

    At lesson level, pass expected_module to match on the (module, lesson) pair — required to tell
    the two "Know Your Rights" lessons apart. Without it, matching falls back to the lesson name
    alone, which is what the pre-200-row scorer did.
    """
    if level == "lesson" and expected_module is not None:
        ok_pairs = accepted_pairs(expected_module, expected)
        for r in results:
            if (norm(r.get("module")), norm(r.get("lesson"))) in ok_pairs:
                return r["rank"]
        return None

    ok_labels = accepted(expected, level)
    field = "lesson" if level == "lesson" else "module"
    for r in results:
        if norm(r.get(field)) in ok_labels:
            return r["rank"]
    return None


def check_label_pairs(records: list[dict]) -> list[str]:
    """Golden rows whose (module, lesson) pair does not exist in the index.

    Pair matching makes a wrong module label an automatic miss for that row, which would otherwise
    look like a retrieval regression. Surfacing it is the guard against that.
    """
    try:
        chunks = json.loads(CHUNKS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []

    indexed = {(norm(c["module"]), norm(c["lesson"])) for c in chunks}
    bad = []
    for rec in records:
        pairs = accepted_pairs(rec["expected_module"], rec["expected_lesson"])
        if pairs and not (pairs & indexed):
            bad.append(
                f"{rec['question'][:58]!r} -> {rec['expected_module']} / {rec['expected_lesson']}"
            )
    return bad


def load_golden_labels(csv_path: Path) -> dict[str, tuple[str, str]]:
    """question -> (module, lesson) from the golden CSV, so label fixes need no new run."""
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        return {
            row["question"].strip(): (row["module"], row["lesson"])
            for row in csv.DictReader(fh)
            if row.get("question")
        }


def load_golden_meta(csv_path: Path) -> dict[str, tuple[str, str]]:
    """question -> (cohort, type) from the golden CSV.

    Read at score time for the same reason labels are: how a run is partitioned for reporting is
    rubric, not measurement, so re-partitioning an old dump must never cost another Azure run.
    """
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        return {
            row["question"].strip(): (
                (row.get("cohort") or "unknown").strip() or "unknown",
                (row.get("type") or "standard").strip() or "standard",
            )
            for row in csv.DictReader(fh)
            if row.get("question")
        }


def annotate(records: list[dict], csv_path: Path) -> None:
    """Attach cohort/type to each record. Rows absent from the CSV are unknown/standard."""
    meta = load_golden_meta(csv_path)
    for rec in records:
        cohort, qtype = meta.get(rec["question"].strip(), ("unknown", "standard"))
        rec.setdefault("cohort", cohort)
        rec.setdefault("type", qtype)


def relabel(records: list[dict], csv_path: Path) -> int:
    """Overwrite each record's expected labels from the CSV. Returns how many rows changed.

    A question missing from the CSV keeps the labels captured in the dump — that way a dump taken
    before a question was renamed still scores rather than silently counting as a miss.
    """
    labels = load_golden_labels(csv_path)
    changed = 0
    for rec in records:
        current = labels.get(rec["question"].strip())
        if current is None:
            continue
        if (rec["expected_module"], rec["expected_lesson"]) != current:
            rec["expected_module"], rec["expected_lesson"] = current
            changed += 1
    return changed


def rate(hits: int, n: int) -> dict:
    return {"hits": hits, "n": n, "pct": round(100 * hits / n, 1) if n else 0.0}


def score_level(records: list[dict], level: str) -> dict:
    """hit@k for each k in the sweep plus MRR, at either lesson or module granularity."""
    n = len(records)
    key = "expected_lesson" if level == "lesson" else "expected_module"
    # An errored row has no results, so first_hit_rank returns None and it counts as a miss.
    # expected_module is passed at lesson level so the two "Know Your Rights" lessons stay distinct.
    ranks = [
        first_hit_rank(
            r["results"],
            r[key],
            level,
            expected_module=r["expected_module"] if level == "lesson" else None,
        )
        for r in records
    ]

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


def subset_block(records: list[dict]) -> dict:
    """A scoreable metric block for a slice of the run, shaped like the top-level one.

    Same shape matters: flat_metrics() runs over it unchanged, which is what lets the core27 slice
    be diffed against the old 27-row baseline.
    """
    return {
        "n": len(records),
        "lesson": score_level(records, "lesson"),
        "module": score_level(records, "module"),
    }


def subsets(records: list[dict]) -> dict:
    """Break the run down by cohort and by question type.

    by_cohort keeps the pre-Phase-6 baseline comparable; by_type stops 4 vague one-liners and 5
    misspellings from moving the headline for reasons that are not about retrieval.
    """
    by_cohort: dict[str, list[dict]] = {}
    by_type: dict[str, list[dict]] = {}
    for rec in records:
        by_cohort.setdefault(rec.get("cohort", "unknown"), []).append(rec)
        by_type.setdefault(rec.get("type", "standard"), []).append(rec)

    return {
        "by_cohort": {name: subset_block(rows) for name, rows in sorted(by_cohort.items())},
        "by_type": {name: subset_block(rows) for name, rows in sorted(by_type.items())},
    }


def per_module(records: list[dict]) -> dict:
    """Directional only — the per-module counts are small relative to the whole set."""
    groups: dict[str, list[dict]] = {}
    for rec in records:
        # A multi-label row is filed under its first module so the breakdown stays one row per
        # real module; scoring inside the group still accepts every alternative.
        primary = rec["expected_module"].split(LABEL_SEP)[0].strip()
        groups.setdefault(primary, []).append(rec)

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
    # A multi-label row exercises every lesson it lists, so each one counts as covered. Pairs are
    # used rather than bare lesson names so a Module 3 question does not mark Module 4's
    # same-named "Know Your Rights" as covered.
    tested = {
        pair for r in records for pair in accepted_pairs(r["expected_module"], r["expected_lesson"])
    }
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
        "subsets": subsets(records),
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
    floor = noise_questions(n, metrics["lesson"]["hit@1"]["pct"])
    print(
        f"noise   n={n}, 1 question = {100 / n:.1f}pp — treat any move under "
        f"{floor} questions ({100 * floor / n:.1f}pp) as noise"
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

    subs = metrics.get("subsets", {})
    if len(subs.get("by_cohort", {})) > 1:
        print("\nBy cohort (core27 = the questions the baseline was measured on)")
        print(f"  {'cohort':<12} {'n':>4}  {'lesson@1':>9} {'lesson@5':>9} {'lesson MRR':>10}")
        for name, b in subs["by_cohort"].items():
            print(
                f"  {name:<12} {b['n']:>4}  {b['lesson']['hit@1']['pct']:>8.1f}% "
                f"{b['lesson']['hit@5']['pct']:>8.1f}% {b['lesson']['mrr']:>10}"
            )

    by_type = subs.get("by_type", {})
    if any(t in by_type for t in HARD_TYPES):
        print("\nBy question type (vague/typo are reported apart from the headline)")
        print(f"  {'type':<12} {'n':>4}  {'lesson@1':>9} {'lesson@5':>9} {'lesson MRR':>10}")
        for name, b in by_type.items():
            print(
                f"  {name:<12} {b['n']:>4}  {b['lesson']['hit@1']['pct']:>8.1f}% "
                f"{b['lesson']['hit@5']['pct']:>8.1f}% {b['lesson']['mrr']:>10}"
            )
        hard_n = sum(by_type[t]["n"] for t in HARD_TYPES if t in by_type)
        print(f"  note  {hard_n} hard-type rows are included in the headline numbers above")

    print("\nPer-module (directional only)")
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
        rank = first_hit_rank(
            rec["results"], rec["expected_lesson"], "lesson", expected_module=rec["expected_module"]
        )
        if rank is None or rank > 5:
            any_miss = True
            got = ", ".join(f"{r['lesson']}" for r in rec["results"][:3])
            where = "not in top 10" if rank is None else f"rank {rank}"
            want = rec["expected_lesson"].replace(LABEL_SEP, " OR ")
            print(f"  {rec['question'][:62]}")
            print(f"    want: {want}  ({where})")
            print(f"    got:  {got}")
    if not any_miss:
        print("  none")


def print_diff(metrics: dict, config: dict, baseline: dict | None, relabelled: int = 0) -> None:
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
    if relabelled:
        # Without this, a corrected golden label reads as a retrieval win, which it is not. Note
        # this compares against the DUMP's labels — if the baseline was promoted after the same
        # relabel, the deltas here are already zero and only the notice remains.
        print(
            f"  RUBRIC CHANGED — {relabelled} row(s) scored against labels newer than this dump.\n"
            f"    Any delta below reflects the grading change, NOT retrieval quality."
        )

    print_diff_table(flat_metrics(metrics), flat_metrics(base_metrics))
    print_continuity(metrics, base_metrics)


def print_diff_table(now: dict, base: dict) -> None:
    print(f"\n  {'metric':<18} {'baseline':>10} {'now':>10} {'delta':>10}  verdict")
    for name, (value, hits, n) in now.items():
        if name not in base:
            print(f"  {name:<18} {'—':>10} {value:>10} {'new':>10}  new metric")
            continue
        base_value, base_hits, base_n = base[name]
        delta = round(value - base_value, 4)

        # Noise is defined in questions, so hit rates compare counts where they can. MRR is a 0-1
        # figure, so scale it to a percentage to reuse the same threshold. The threshold scales
        # with n, which keeps the same code honest at 27 rows and at 200.
        rate_pct = base_value if hits is not None else 100 * base_value
        floor = noise_questions(n, rate_pct)
        if hits is not None and base_hits is not None and base_n == n:
            moved = abs(hits - base_hits) >= floor
        elif hits is not None:
            # Different set sizes — counts are not comparable (27/27 vs 200/200 is a 173-question
            # "move" at an identical rate), so fall back to percentage points.
            moved = abs(delta) >= 100 * floor / max(n, 1)
        else:
            moved = abs(delta) >= floor / max(n, 1)

        if not moved:
            verdict = "within noise"
        elif delta > 0:
            verdict = "IMPROVED"
        else:
            verdict = "REGRESSION"
        print(f"  {name:<18} {base_value:>10} {value:>10} {delta:>+10}  {verdict}")


def print_continuity(metrics: dict, base_metrics: dict) -> None:
    """Diff the baseline's own cohort against the baseline, when the set has grown since.

    Growing the golden set makes every headline number incomparable to the accepted baseline. The
    subset the baseline was actually measured on is still comparable, and that is the only line of
    continuity across the expansion — without it, promoting a bigger set silently discards the
    ability to detect a regression against anything historical.
    """
    cohort = metrics.get("subsets", {}).get("by_cohort", {}).get(BASELINE_COHORT)
    if not cohort or not base_metrics:
        return
    # Nothing to reconcile if this run IS the baseline cohort — the headline diff already is
    # like-for-like, and a second identical table would only be confusing.
    if metrics["n"] == cohort["n"]:
        return
    # Only meaningful while the baseline predates the expansion; once re-promoted at the full set,
    # the cohort no longer lines up with what the baseline measured.
    if base_metrics.get("n") != cohort["n"]:
        return

    print(
        f"\n  Continuity check — the {cohort['n']} {BASELINE_COHORT} rows only, vs the baseline "
        f"measured on those same rows."
    )
    print("  This is the like-for-like comparison; the table above is not.")
    print_diff_table(flat_metrics(cohort), flat_metrics(base_metrics))


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
    parser.add_argument(
        "--dump-labels",
        action="store_true",
        help="score against the labels stored in the dump instead of the current golden CSV",
    )
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

    # Labels are rubric, not measurement — refresh them so a corrected label never costs a re-run.
    changed = 0
    if not args.dump_labels and GOLDEN_CSV.exists():
        changed = relabel(dump["records"], GOLDEN_CSV)
        if changed:
            print(
                f"\nNOTE  {changed} row(s) relabelled from {GOLDEN_CSV.name} since this run was "
                f"taken — scoring against the current labels (--dump-labels to score as-run)"
            )

    # Cohort/type are reporting partitions, so they come from the CSV too, even under --dump-labels.
    if GOLDEN_CSV.exists():
        annotate(dump["records"], GOLDEN_CSV)

    # A wrong module label is an automatic miss now that matching is pair-based, so say so.
    bad_pairs = check_label_pairs(dump["records"])
    if bad_pairs:
        print(
            f"\nWARNING  {len(bad_pairs)} golden row(s) have a (module, lesson) pair that is not in "
            f"the index.\n  These can never score a lesson-level hit — fix the label, not retrieval:"
        )
        for line in bad_pairs[:10]:
            print(f"    {line}")

    config, metrics = dump["config"], score(dump)

    print_report(metrics, config)
    if not args.no_misses:
        print_misses(dump)

    baseline = (
        json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.exists() else None
    )
    print_diff(metrics, config, baseline, relabelled=changed)

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
