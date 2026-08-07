# Retrieval Eval — Implementation Plan

Step-by-step plan to finish the retrieval metrics. Companion to
`Documentation/RETRIEVAL_EVAL_METRICS.md`, which is the catalogue of *what* gets measured and its
current status. This file is the *how* and the order.

Created: 2026-08-06 · **Phases 0-3 done 2026-08-06** · Phases 4-6 open

## Status

| | |
|---|---|
| Runner / scorer | `tests/run_retrieval_eval.py` -> `tests/score_retrieval_eval.py` |
| Baseline | `tests/retrieval_baselines.json` — hybrid, no reranker, no rewrite, k=10, 27 questions |
| Lesson-level (primary) | hit@1 20/27, hit@3 23/27, hit@5 24/27, hit@10 25/27, MRR 0.804 |
| Module-level (legacy) | hit@1 23/27, hit@5 27/27, MRR 0.904 |
| Coverage | 27 of 52 indexed lessons have a golden question |

```
uv run python tests/run_retrieval_eval.py                    # k=10 dump -> resources/data/eval-runs/ (gitignored)
uv run python tests/score_retrieval_eval.py                  # score newest dump, diff vs baseline
uv run python tests/score_retrieval_eval.py <dump> --promote  # accept a run as the new baseline
```

**Phase order is no longer 4 -> 5 -> 6.** The first run showed that at n=27 nothing in the 1-2
question range is a finding, so growing the set comes first:
**"Do now" (N.1-N.3) -> 6 -> 5 -> (then judge a reranker)**, with 4 runnable independently whenever.
See "What this unlocks" at the bottom for why.

Phases 0-3 needed no new golden data and no changes to `app/`; that is still true of 4-6.

## The one architectural decision

Run retrieval **once at k=10**, dump the raw results to JSON, and score offline.

```
run_retrieval_eval.py   -> raw JSON (rank, score, lesson, module, text per row, k=10)
score_retrieval_eval.py -> metrics + diff vs baselines.json
```

Everything downstream gets cheap because of this: the k sweep costs no extra Azure calls (score
the same dump at k=1/3/5/10), new metrics can be added and re-run against old dumps for free, and
changing a rubric never means re-paying for retrieval. It also mirrors the runner/scorer split
already planned for the generation side (`run_golden_dataset.py` -> `score_golden_dataset.py`).

Held up in practice, with one caveat found while building it: the k sweep off a single dump is
*candidate-pool* recall, not a simulation of live retrieval at each k. See the next section.

## Resolved before starting

`retrieve()` does not rewrite — the docstring at the old `eval_retrieval.py:12` was wrong. But the
distinction turned out to be moot for this dataset: `rewrite_query()` (`chain.py:73-78`) returns
the question unchanged when the session has no history, and every golden row is a fresh single-turn
session. So the recorded 27/27 and 24/27 are the **no-rewrite** arm, and no rewrite-on arm exists
to compare against until multi-turn golden rows are added (Phase 6.4). The runner records
`query_rewrite: false` in the fingerprint so this stays explicit.

Re-running the old script verbatim on 2026-08-06 reproduced 27/27 hit@5 and 24/27 top-1 exactly, so
the index has not drifted since June and the new baseline is anchored to a verified control.

## Discovered during Phase 1: RRF fusion is depth-dependent

The one architectural decision above needs a caveat. Retrieving at k=5 scored lesson hit@5 = 25/27;
truncating the k=10 dump to 5 scored 24/27. Requesting 10 candidates changes the fused ranking of
the top 5, so hit@k off a k=10 dump is *candidate-pool recall*, not a prediction of the live
`top_k=5` config.

The dump-once design still holds — pool recall is the right question for "is `top_k=5` deep
enough" — but two dumps are only comparable at the same `--k`. `top_k` is part of the config
fingerprint, and the scorer prints a CONFIG CHANGED warning when it differs.

It does *not* settle reranker-vs-re-chunk. That framing was wrong for a separate reason, unrelated
to fusion depth: see "What this unlocks".

## Phase 0 — Single source of truth for golden data

The 27 questions existed twice: hardcoded in `eval_retrieval.py:27-66` (with `lesson`) and in
`resources/data/golden-data/golden_dataset_curriculum.csv` (with `expected_answer`, no `lesson`).
Two sources will drift. **Done:** the CSV now carries `lesson` and is the only source; the script
is deleted. All 27 lesson labels were checked against `resources/data/chunks/chunks.json` first.

- [x] 0.1 Diff the two sets. Confirm they are the same 27 questions; if they differ, pick one and
      delete the other.
- [x] 0.2 Add a `lesson` column to `golden_dataset_curriculum.csv`, populated from the hardcoded list.
- [x] 0.3 Delete `GOLDEN_QA` from the script. The CSV becomes the only golden source.
- [x] 0.4 **Verify:** re-run and confirm still 27/27 hit@5 and 24/27 top-1 at module level. A data
      move must not change the measurement.
      **Result:** the two sets are the same 27 questions in the same order and modules, but the CSV
      *rewords* every one (more casual student phrasing), so this was not a pure data move. The old
      wording still gives exactly 27/27 and 24/27. On the CSV wording, module hit@5 holds at 27/27
      and module top-1 is 23/27 — one question, within noise. The CSV wording wins because it is
      already what the generation eval measures.

## Phase 1 — Split runner and scorer

- [x] 1.1 Create `tests/run_retrieval_eval.py`. Reads the CSV, calls `retrieve(q, top_k=10)` per row,
      writes raw JSON: question, expected module, expected lesson, and for each of the 10 results
      its rank, score, lesson, module, text.
- [x] 1.2 Reuse the concurrency + exponential-backoff pattern from `run_golden_dataset.py:38-65`.
      The shared Azure OpenAI deployment rate-limits easily and a 429 mid-run corrupts a whole eval.
- [x] 1.3 Create `tests/score_retrieval_eval.py`. Reads the JSON, computes all metrics, prints a
      summary and writes a metrics file.
- [x] 1.4 Delete `tests/eval_retrieval.py`. Update references in `.claude/CLAUDE.md` (Retrieval &
      evaluation section) and in `RETRIEVAL_EVAL_METRICS.md`.
- [x] 1.5 **Verify:** the scorer reproduces 27/27 and 24/27 from the dump when scored at k=5,
      module-level. If it does not, the rewrite changed the measurement and the old baseline is
      no longer comparable.
      **Result:** module hit@5 = 27/27 reproduces. Module top-1 = 23/27, off by one from the CSV
      rewording (see 0.4), not from rewriting — rewriting is a no-op here. This check also surfaced
      the RRF fusion-depth caveat above.

Naming note: went with `run_*` / `score_*` to match the generation-side convention, and churned
the references. Two gotchas worth keeping in mind when extending the scorer: `retrieval_*.json`
also globs the scorer's own `*_metrics.json` output (excluded explicitly), and `--out` outside the
repo root used to crash the runner's closing print.

## Phase 2 — Metrics that need no new data

All of these come out of the Phase 1 dump. No extra Azure calls, no new golden rows.

- [x] 2.1 **Lesson-level matching** as the primary number. Keep module-level as a secondary column so
      the June baseline stays comparable. This is the single change that de-saturates hit@5 — with
      only 6 modules, any chunk from the right module currently counts as a hit
      (`eval_retrieval.py:100`).
- [x] 2.2 **hit@1 / hit@3 / hit@5 / hit@10** — the k sweep, scored from the one k=10 dump.
- [x] 2.3 **MRR**, lesson-level. Continuous, so it will not saturate the way hit@5 did.
- [x] 2.4 **Per-module breakdown** of hit@5 and top-1. ~4 questions per module today, so treat these
      as directional, not precise.
- [x] 2.5 **Score statistics** — mean top-1 score, top1-top2 margin, top1-top5 margin, and
      percentiles. `score` is already returned (`chain.py:107`) and currently discarded.
- [x] 2.6 **Per-lesson coverage report** — which of the 52 lessons have zero golden questions. This
      is dataset health rather than retrieval quality, but it falls out of the same data and it is
      what justifies Phase 6.
- [x] 2.7 **Noise guard** — print `n=27, 1 question = 3.7pp` in the summary and label any diff under
      2 questions as within noise. With a set this small, unlabeled small deltas will get
      over-interpreted.

## Phase 3 — Turn it into a regression test

- [x] 3.1 Create `baselines.json` holding the **accepted** baseline only: the metric block plus a
      config fingerprint — top_k, reranker on/off, hybrid weights, chunk count, index name,
      embedding model, rewrite on/off, git sha, date. A score without its config is not a baseline.
- [x] 3.2 Runs write to a timestamped, gitignored output file. Promoting a run to baseline is a
      separate explicit command, so a bad run cannot silently become the reference.
- [x] 3.3 Scorer prints a diff table against the baseline, flagging regressions and within-noise
      moves differently.

**Done.** The eval is now a test: `uv run python tests/score_retrieval_eval.py` prints a diff
table labelling each metric IMPROVED / REGRESSION / within noise, and shouts CONFIG CHANGED when
the fingerprint differs from the baseline's.

## Do now — independent of every phase

The first run surfaced three concrete misses that need no new data, no spend, and no ordering
decision. They are the only findings on a 27-row set that clear the noise floor.

- [ ] N.1 **The 2 questions whose correct lesson never appears in the top 10** — "Why do I feel bad
      whenever I spend money on myself?" (want `Introduction to Money Mindsets`) and "How does the
      way I grew up shape how I handle cash?" (want `Generational Legacies`). Unreachable at any
      rank, so no reranker and no `top_k` change touches them. This is the one clear case for
      re-chunking.
- [ ] N.2 **Check whether N.1 is a labelling problem before treating it as a retrieval bug.** Both
      return thematically adjacent lessons — `Cycle of Socialization`, `Give Yourself Grace`,
      `Money and Relationships`. If those chunks genuinely answer the question, the golden label is
      wrong and the fix is the label, not the index. Read the actual chunk text in the dump.
      Resolve this first: it decides whether the ceiling is ~7% or ~0%.
- [ ] N.3 **"How do I figure out which bank or credit union is right for me?"** ranks
      `Selecting a Financial Institution` at 8, behind loan-officer and loan-decision chunks. Only
      1 chunk exists for that lesson (vs 6 for `What Factors Do Loan Officers Consider`), so this
      looks like a chunk-count imbalance rather than a semantic failure — worth confirming, since it
      is the kind of thing re-chunking would fix.

Module 1 is the weakest module at 2/4 lesson hit@5, and N.1 accounts for both of its misses.

## Phase 4 — Negatives and the abstain path

`retrieve()` always returns results with some score. Out-of-scope questions therefore produce five
plausible-looking chunks that the LLM answers from. Nothing measures this today.

- [ ] 4.1 Create `resources/data/golden-data/golden_dataset_negatives.csv` with ~15 questions the
      111 chunks genuinely do not cover (FBAR filing, crypto tax treatment, mortgage underwriting,
      H-1B tax status).
- [ ] 4.2 Score them on a different axis: not "was the right chunk found" but "how high did the top
      score get". Report the in-corpus and out-of-corpus score distributions side by side.
- [ ] 4.3 Derive a candidate abstain threshold from where the two distributions separate. **Report
      it only** — do not wire a threshold into `retrieve()` in this phase. Changing live retrieval
      behavior is its own change with its own before/after run.
      Expect this to be hard: the in-corpus top-1 RRF scores measured on 2026-08-06 span only
      0.0312-0.0333 with a mean top1-top2 margin of 0.0010. If the out-of-corpus distribution lands
      inside that band, the honest output of this phase is "RRF score is not a usable confidence
      signal" — which is a real result, and would point at scoring the vector leg separately
      (cosine similarity, unlike fused RRF, is comparable across queries).

## Phase 5 — Context precision

This is now the phase that decides the reranker question, not the k sweep. A reranker changes the
*order* of the 5 chunks; all 5 reach the prompt either way, so its value depends entirely on
whether a correct chunk at rank 4 is used as well as one at rank 1. That is what context precision
plus the generation eval measure, and nothing in the retrieval metrics can substitute for it.

- [ ] 5.1 LLM judge over the 5 retrieved chunks per question: relevant / not relevant. Put it behind
      a `--judge` flag since it costs money on every run.
- [ ] 5.2 Use GPT-4o, not 4o-mini. Judging is offline and low-volume, so the cost argument that
      drove 4o-mini for the guardrails does not apply here — and 4o-mini is a noticeably weaker judge.
- [ ] 5.3 Cache judgments to disk keyed by (question, chunk hash). Re-scoring should never re-pay
      for a judgment already made.

## Phase 6 — Grow the golden set

Do this after Phases 0-3, not before — otherwise you cannot tell what the larger set actually
bought. **As of the first run this is the highest-value open phase**, ahead of 4 and 5: at n=27 the
noise floor is 2 questions (7.4pp), which is wider than most effects worth measuring. ~80 rows is
where a 15pp top-1 shift becomes detectable, ~157 for 10pp.

- [ ] 6.1 Generate candidate questions from each of the 111 chunks (the correct lesson is known by
      construction).
- [ ] 6.2 Hand-rewrite them into real student phrasing. LLM-generated questions reuse chunk
      vocabulary and inflate retrieval scores.
- [ ] 6.3 Target ~100 rows, proportional to module size rather than a flat count per module.
- [ ] 6.4 Add the question types currently absent: vague one-liners ("credit?"), misspellings, and
      follow-ups that only make sense with prior turns.

## Explicitly not doing

| Item | Why not |
|---|---|
| Chunk-level ID labels | Chunk IDs change on every re-chunk, and re-chunking is one of the experiments this eval exists to evaluate. A chunk-ID golden set would break exactly when you need it. Lesson labels are stable. Would also require adding `id` to `select=[...]` (`chain.py:98`). |
| nDCG@5 | Requires graded relevance labels. Binary labels over a 111-chunk corpus do not justify the labeling cost. |

## Blast radius

No changes to `app/` are needed for Phases 0-4. `top_k` is already a parameter (`chain.py:91`), and
`lesson`, `module`, and `score` are all already returned (`chain.py:101-109`).

The one exception is **resolved**: `rewrite_query(question, session_id)` (`chain.py:73`) is already
a standalone callable, so no `app/` change is needed. But the comparison is not worth scoping yet —
rewriting is a no-op on single-turn rows, so it only becomes measurable once Phase 6.4 adds
follow-up questions. Phase 5's judge is the only item here that costs money per run.

## What this unlocks — partially answered 2026-08-06

The k sweep is in. Lesson-level: hit@1 20/27, hit@3 23/27, hit@5 24/27, hit@10 25/27.

This section originally carried a rule of thumb: recall@10 >> recall@5 means a ranking problem and
a reranker pays for itself; recall@10 ~ recall@5 means the embedding is the ceiling and reranking is
wasted. Measured, that rule turns out to be the wrong test, for two reasons. **Do not use it to
cancel a reranker.**

**1. Recall@5 is not what a reranker moves.** A reranker reorders; the metric that captures that is
top-1. Rank of the correct lesson across the 27: rank 1 x20, rank 2 x2, rank 3 x1, rank 4 x1,
rank 8 x1, never x2.

| headroom | questions | pp |
|---|---|---|
| recall@5 (pool 10 -> keep 5) | 1 | 3.7 |
| top-1 (reorder the pool of 5) | 4 | 14.8 |
| top-1 (reorder a pool of 10) | 5 | 18.5 |

Five questions retrieve the correct lesson but not first. That is reranker-addressable, and the
top-1 headroom's 95% CI [4.2%, 33.7%] excludes zero.

**2. At n=27 the recall number cannot carry the claim anyway.** 1/27 has a 95% CI of
[0.1%, 19.0%] — consistent with a true pool-recall gap of up to 5 questions.

### What does survive

- **2 of 27 questions never retrieve the correct lesson in the top 10.** No reranker fixes those;
  that is an embedding/chunking ceiling worth ~7%, and the one clear argument for re-chunking.
- **Raising `top_k` past 5 is not worth it.** A claim about pool depth, which is what the sweep
  actually measures.
- **The score distribution is nearly flat** — top-1 RRF scores all within 0.0312-0.0333, mean
  top1-top2 margin 0.0010. Little confidence signal, so Phase 4's abstain threshold may not
  separate cleanly.

### What this eval structurally cannot answer

Whether rank order *within* the 5 matters. All 5 chunks go into the prompt and `SELECTION_RULE`
tells the model to pick 1-2 points, so for presence of the right material rank 1 vs rank 4 is
irrelevant — 24/27 already have it. Whether the model uses the right chunk when it sits at rank 4
is a generation-side question (Phase 5 context precision, answer correctness).

### Sample size, so the next decision is planned rather than guessed

McNemar, 80% power, alpha 0.05, assuming a reranker both fixes and breaks some questions:

| effect to detect | golden questions needed |
|---|---|
| top-1 +15pp | ~80 |
| top-1 +10pp | ~157 |
| top-1 +5pp | ~471 |
| recall@5 +3.7pp | ~361 |

Phase 6's ~100-row target is where a 10-15pp top-1 shift becomes detectable. The recall@5 question
is unanswerable at any realistic set size — which is itself the argument for judging a reranker on
top-1 and precision instead.

### Revised order

**"Do now" N.1-N.3** (the 3 misses that clear the noise floor; N.2 first, since it decides whether
the ceiling is real or a labelling artefact) -> **Phase 6** (grow to ~100, so effects become
detectable at all) -> **Phase 5** (context precision: does rank order actually affect answers?) ->
only then evaluate a reranker, scored on top-1 and precision, never on recall@5. Phase 4 is
independent and can slot in anywhere.

## Rules for every run

- Run before **and** after each retrieval change. One variable at a time.
- Same questions, same index version, **same `--k`**, or the numbers are not comparable. The
  fingerprint check enforces this; do not talk yourself past a CONFIG CHANGED warning.
- Movement under ~2 questions on a 27-row set is noise. Say so out loud when reporting a delta,
  including a favourable one.
- Judge a *reordering* change (reranker, hybrid weights) on **top-1 and MRR**. Judge a *pool-depth*
  change (top_k) on hit@k. Judge a *chunking or embedding* change on hit@10, since that is the only
  metric that moves when material is unreachable at any rank.
- Record the config with the numbers.
- A retrieval win should show up downstream as improved context recall in the generation eval. If
  top-1 improves and answers do not, the win was cosmetic.

## Related

- `Documentation/RETRIEVAL_EVAL_METRICS.md` — metric catalogue and current status
- `tests/run_retrieval_eval.py` / `tests/score_retrieval_eval.py` — the runner and scorer built in Phase 1
- `tests/retrieval_baselines.json` — the accepted baseline (Phase 3)
- `tests/run_golden_dataset.py` — generation-side runner; source of the concurrency pattern
- `app/services/chain.py:91` — `retrieve()`
