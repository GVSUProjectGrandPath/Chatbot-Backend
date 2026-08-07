# Retrieval Eval — Implementation Plan

Step-by-step plan to finish the retrieval metrics. Companion to
`Documentation/RETRIEVAL_EVAL_METRICS.md`, which is the catalogue of *what* gets measured and its
current status. This file is the *how* and the order.

Created: 2026-08-06 · **Phases 0-3 done 2026-08-06** · **N.1-N.3 done 2026-08-06** ·
**6.1-6.3 done 2026-08-06 (golden set 27 -> 200)** · **n=200 baseline promoted 2026-08-07** ·
Phases 4-5 and 6.4 open

**Read "Recommendations" at the bottom first** — it is the current priority order and it supersedes
the ordering arguments scattered through the older sections.

## Status

| | |
|---|---|
| Runner / scorer | `tests/run_retrieval_eval.py` -> `tests/score_retrieval_eval.py` |
| Baseline | `tests/retrieval_baselines.json` — hybrid, no reranker, no rewrite, k=10, **200 questions** (promoted 2026-08-07, sha e78d225) |
| Lesson-level (primary) | hit@1 111/200 (55.5%), hit@3 153/200, hit@5 172/200 (86.0%), hit@10 186/200 (93.0%), MRR 0.676 |
| Module-level (legacy) | hit@1 143/200 (71.5%), hit@5 188/200, hit@10 200/200, MRR 0.810 |
| Coverage | **50 of 52** indexed (module, lesson) pairs have a golden question (was 26; see Phase 6) |
| Continuity | the core27 slice still scores hit@1 22/27, hit@5 26/27, MRR 0.8781 — **identical to the 27-row baseline** |

**The 27-row set was flattering.** Top-1 is 55.5% at n=200, not 81.5%. Retrieval did not change:
the same 27 questions score exactly what they always did (the scorer's continuity check confirms it
line by line). The old number was measured on a set that covered half the curriculum and, by
construction, the half someone had already thought to write questions about.

```
uv run python tests/run_retrieval_eval.py                    # k=10 dump -> resources/data/eval-runs/ (gitignored)
uv run python tests/score_retrieval_eval.py                  # score newest dump, diff vs baseline
uv run python tests/score_retrieval_eval.py <dump> --promote  # accept a run as the new baseline
```

**Phase order.** The n=27 argument for doing Phase 6 first is spent — the set is grown and the
baseline re-promoted. Current order is in "Recommendations" at the bottom; in short, a free
relabelling pass, then Phase 4, then chunking, then Phase 5 and a reranker.

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
- [x] 2.7 **Noise guard** — print the per-question percentage and label small diffs as within noise,
      so unlabeled small deltas do not get over-interpreted.
      **Updated 2026-08-07:** the flat 2-question rule was replaced by `noise_questions(n, pct)`,
      one binomial standard error with 2 as the floor. It reads 2 questions at n=27 and 7 at n=200 —
      a fixed count silently becomes far too tight as the set grows.

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

- [x] N.1 **The 2 questions whose correct lesson never appears in the top 10** — "Why do I feel bad
      whenever I spend money on myself?" (want `Introduction to Money Mindsets`) and "How does the
      way I grew up shape how I handle cash?" (want `Generational Legacies`).
      **Dissolved by N.2: both were mislabelled, not unreachable.** There is no retrieval bug here
      and no case for re-chunking on this evidence.
- [x] N.2 **Check whether N.1 is a labelling problem before treating it as a retrieval bug.**
      **Result: both are labelling problems. The ceiling is ~0%, not ~7%.** Reading the expected
      lessons' actual chunk text settled it — *both are course-outline documents that do not answer
      their question*:
      - `Introduction to Money Mindsets` is an instructor-facing syllabus (Learning Objectives /
        Activities / Assessment per module). Nothing about guilt over spending. What retrieval
        returned instead does answer it: `Give Yourself Grace` at ranks 1-2 ("your worth is not
        defined by your bank account", "room in my budget for things that bring me joy") and
        `Cycle of Socialization` at 3-5 (Alex hesitating over a $25 copay from childhood money
        stress).
      - `Generational Legacies` is a 2-3 minute course outline about wealth *inequality* — redlining,
        St. Louis Fed links — with a bare link dump as its second chunk. The question is about
        inherited *habits*, which is exactly `Cycle of Socialization` ("our beliefs about money start
        forming at a young age… our families, culture, and the media all shape how we think and feel
        about money"), returned at ranks 1/3/5.

      **Fixed** by relabelling both rows in the golden CSV. Row 1 lists two acceptable lessons —
      the guilt-from-taught-values framing is genuinely covered by both `Cycle of Socialization`
      and `Give Yourself Grace` — which is why the `lesson`/`module` columns now accept a
      `|`-separated list (see "Multi-label golden rows" below).
- [x] N.3 **"How do I figure out which bank or credit union is right for me?"** ranks
      `Selecting a Financial Institution` at 8, behind loan-officer and loan-decision chunks.
      **Confirmed: chunk-count imbalance, not a semantic failure.** That lesson has exactly 1 chunk
      vs 6 for `What Factors Do Loan Officers Consider`. It is also systemic rather than a one-off —
      **9 of 51 lessons are single-chunk**, and every one of them is similarly outgunned in a fused
      ranking. This is now the only genuine retrieval miss in the set, and the one real argument for
      revisiting chunking.
      **Confirmed at n=200 (2026-08-07):** "the only genuine miss in the set" was a 27-row statement
      and no longer holds — but the mechanism does, and much more strongly. Top-1 by source-lesson
      chunk count is 36.7% / 54.7% / 65.6% for 1 / 2 / 3+ chunks, and this same question's lesson is
      0/5 on top-1 across five phrasings. See "The n=200 run".

Module 1 was reported as the weakest module at 2/4 lesson hit@5; after the N.2 relabel it is **4/4**.
The weakest module is now Module 2: Building Healthy Habits at 3/5 lesson hit@5.

### Corrected numbers (same dump, corrected labels)

Relabelling changed no retrieval behaviour whatsoever — it re-graded the existing k=10 dump.

| lesson-level | before (as-run labels) | after (corrected labels) |
|---|---|---|
| hit@1 | 20/27 | **22/27** |
| hit@3 | 23/27 | **25/27** |
| hit@5 | 24/27 | **26/27** |
| hit@10 | 25/27 | **27/27** |
| MRR | 0.804 | **0.8781** |

`uv run python tests/score_retrieval_eval.py --dump-labels` still reproduces the old column exactly,
which is the proof that relabelling is the only thing that moved. The corrected numbers are promoted
to `tests/retrieval_baselines.json`.

**hit@10 is now 27/27 — nothing in the golden set is unreachable.** The "~7% embedding/chunking
ceiling" recorded below in "What this unlocks" was an artefact of two bad labels; that claim is
retracted.

### Multi-label golden rows

The `module` and `lesson` columns accept `|`-separated alternatives, and any listed label counts as
a hit. Some student questions are legitimately answered by more than one lesson, and forcing a
single label grades a correct retrieval as a miss — exactly the failure N.2 found. It did matter
more at 200 rows: see "Read 55.5% as a floor" below, where near-duplicate lessons are the leading
suspect for the misses.

Two supporting changes came with it:

- **The scorer re-reads labels from the golden CSV at score time** (`--dump-labels` opts out). The
  dump embeds `expected_lesson`, so before this a label fix forced a full re-run — which contradicts
  the plan's own principle that changing a rubric never costs another Azure call. A question absent
  from the CSV keeps its dump label rather than silently scoring as a miss.
- **The diff prints `RUBRIC CHANGED` when rows were relabelled**, alongside the existing
  `CONFIG CHANGED`. Without it the relabel reads as a +7.4pp retrieval win across every lesson
  metric, which it is not. Grading changes and retrieval changes must never be confusable.

`tests/test_retrieval_scoring.py` covers the matching rules and the relabel step.

## Phase 4 — Negatives and the abstain path

**Promoted to the top of the queue after the n=200 run — see "Recommendations".** At hit@5 = 86%,
roughly 1 in 7 questions puts five chunks in front of the model that do not contain the answer.

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

**Do this after the relabelling pass and the chunking experiment** — see "Recommendations". Judging
rank order against a rubric known to be too strict, on a chunking layout about to change, measures
the wrong thing.

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

- [x] 6.1 Generate candidate questions from each of the 111 chunks (the correct lesson is known by
      construction).
- [x] 6.2 Hand-rewrite them into real student phrasing. LLM-generated questions reuse chunk
      vocabulary and inflate retrieval scores.
- [x] 6.3 Target ~100 rows, proportional to module size rather than a flat count per module.
- [ ] 6.4 Add the question types currently absent: vague one-liners ("credit?"), misspellings, and
      follow-ups that only make sense with prior turns.
      **Partially done:** 4 vague and 5 misspelled rows are in, tagged `type`. **Follow-ups are
      still open and need a runner change first** — `run_retrieval_eval.py` sends every row as a
      fresh single-turn session, so a follow-up row would silently be scored without the prior turn
      it depends on. Adding follow-up rows before that change would corrupt the measurement.

**Done 2026-08-06. The golden set is 27 -> 200 rows** (173 new), written by hand against the indexed
lesson text rather than LLM-generated from the chunks, which is what 6.2 exists to prevent.

| | |
|---|---|
| Rows | 200 (`cohort=core27` 27, `cohort=phase6` 173) |
| Lesson coverage | **50 of 52** (module, lesson) pairs (was 26) |
| Per module | M1 34, M2 39, M3 32, M4 51, M5 17, M6 27 — proportional to chunk count |
| Types | 191 standard, 4 vague, 5 misspelled (`type` column) |
| Single-chunk lessons | all 9 now covered, 3 questions each (N.3 said these are structurally hard) |

Two new columns, `cohort` and `type`. `expected_answer` is empty on the new rows — it is unused by
both the retrieval runner and `run_golden_dataset.py` (which only reads `question` and passes the
rest through), so filling it in is generation-side work, not a blocker here.

**Vocabulary-leak check (the 6.2 gate).** Rather than eyeballing 173 rows, the fraction of each
question's content words that appear in its target lesson was measured, calibrated against the 27
hand-written originals as the reference band. New rows mean **0.516** vs core27's **0.626** — less
vocabulary reuse than the questions that set the baseline, not more. Rows at 1.00 are short
questions where every content word is unavoidable ("How do I freeze my credit?").

**Deliberately still uncovered: `Conclusion` and `Introduction to Money Mindsets`.** Both are
instructor-facing syllabus/recap text with no student-answerable content — writing golden rows
against them would re-introduce exactly the mislabel N.2 removed. `Generational Legacies` got 2
rows, but only on the wealth-inequality/redlining material it actually contains, not on the
inherited-habits framing that was the original mislabel. See the open question at the bottom of
"What this unlocks" about whether these belong in the index at all.

### Two things this surfaced

**1. `Know Your Rights` is two different lessons.** It exists in both Module 3 (workplace
discrimination, pay transparency, EEOC) and Module 4 (FCRA, credit report disputes). The scorer
matches lesson labels **by name only**, so a Module 3 question scores a hit when retrieval returns
the Module 4 lesson and vice versa. 7 of the new rows sit on that label. Fixing it means matching on
the (module, lesson) pair, not the lesson string.

**2. ~~The baseline is now un-diffable and must be re-promoted.~~ Resolved 2026-08-07.** The scorer
gained cohort/type partitions and a continuity check, and a fresh n=200 baseline was promoted. See
"Scorer changes" and "The n=200 run" below.

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

## What this unlocks — answered at n=27, then re-answered at n=200

> **Everything in this section below the next paragraph is the n=27 analysis, kept for the record.**
> The n=200 run overturned two of its conclusions — see "Re-answered at n=200" at the end of the
> section. The methodological argument (judge a reranker on top-1, not recall@5) survived; the
> numbers did not.

The k sweep is in. Lesson-level: hit@1 20/27, hit@3 23/27, hit@5 24/27, hit@10 25/27.

This section originally carried a rule of thumb: recall@10 >> recall@5 means a ranking problem and
a reranker pays for itself; recall@10 ~ recall@5 means the embedding is the ceiling and reranking is
wasted. Measured, that rule turns out to be the wrong test, for two reasons. **Do not use it to
cancel a reranker.**

**1. Recall@5 is not what a reranker moves.** A reranker reorders; the metric that captures that is
top-1. Rank of the correct lesson across the 27, **corrected labels (2026-08-06)**:
rank 1 x22, rank 2 x2, rank 3 x1, rank 4 x1, rank 8 x1, never x0.
(As-run labels read rank 1 x20 … never x2 — the two "never" rows were the N.2 mislabels.)

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

- ~~**2 of 27 questions never retrieve the correct lesson in the top 10.**~~ **Retracted 2026-08-06
  (N.2).** Both were mislabelled against course-outline lessons that do not answer their question.
  With corrected labels lesson hit@10 is 27/27 — there is no measured embedding/chunking ceiling on
  this set, and this is no longer an argument for re-chunking. The surviving argument for revisiting
  chunking is N.3: 9 of 51 lessons have a single chunk and lose fused rankings to 6-chunk lessons.
- ~~**Raising `top_k` past 5 is not worth it.**~~ **Overturned at n=200 — see below.** At 27 rows
  only one question sat between rank 5 and rank 10; at 200 rows it is 14.
- **The score distribution is nearly flat** — top-1 RRF scores all within 0.0312-0.0333, mean
  top1-top2 margin 0.0010. Little confidence signal, so Phase 4's abstain threshold may not
  separate cleanly.

### What this eval structurally cannot answer

Whether rank order *within* the 5 matters. All 5 chunks go into the prompt and `SELECTION_RULE`
tells the model to pick 1-2 points, so for presence of the right material rank 1 vs rank 4 is
irrelevant — 172/200 already have it. Whether the model uses the right chunk when it sits at rank 4
is a generation-side question (Phase 5 context precision, answer correctness). This is why the
61-question top-1 headroom below is *potential*, not banked.

### Sample size, so the next decision is planned rather than guessed

McNemar, 80% power, alpha 0.05, assuming a reranker both fixes and breaks some questions:

| effect to detect | golden questions needed |
|---|---|
| top-1 +15pp | ~80 |
| top-1 +10pp | ~157 |
| top-1 +5pp | ~471 |
| recall@5 +3.7pp | ~361 |

Phase 6's ~100-row target is where a 10-15pp top-1 shift becomes detectable. **The set is now 200,
so a 10pp top-1 shift is detectable and 15pp comfortably so.** The recall@5 question remains
unanswerable at any realistic set size — which is itself the argument for judging a reranker on
top-1 and precision instead.

### Re-answered at n=200 (2026-08-07)

Rank of the correct lesson across all 200 questions:

| rank | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 10 | never |
|---|---|---|---|---|---|---|---|---|---|---|
| questions | 111 | 24 | 18 | 9 | 10 | 3 | 5 | 3 | 3 | 14 |

Two conclusions change:

1. **Top-1 headroom is now 61 questions (30.5pp), not 4.** The correct lesson is inside the top 5
   but not first for 61 of 200. That is the largest single pool of addressable error in the eval,
   and it is exactly what a reranker reorders. The n=27 CI [4.2%, 33.7%] turned out to be centred
   about right and hopelessly wide.
2. **"Raising `top_k` past 5 is not worth it" no longer holds.** 14 questions (7pp) have their
   lesson in the k=10 pool but outside the top 5 — at n=27 that was a single question. Read with
   the RRF caveat: truncating a k=10 dump is not the same as retrieving at k=5, so this is a reason
   to *test* `top_k=8` or `10` with a proper before/after, not a reason to change it outright. The
   cost is real too — more chunks means more prompt tokens per request on every student message.

Unchanged: the score distribution is still nearly flat (top-1 RRF 0.0282-0.0333, mean top1-top2
margin 0.0010), so Phase 4's abstain threshold still looks unlikely to separate cleanly.

### Revised order — superseded by "Recommendations" at the bottom

~~**"Do now" N.1-N.3**~~ done 2026-08-06. ~~**Phase 6**~~ done 2026-08-06, baseline promoted
2026-08-07. The remaining ordering argument now lives in "Recommendations", which reverses one part
of the old plan: **Phase 4 moves ahead of Phase 5 and the reranker**, because at hit@5 = 86% the
un-measured failure is the bot answering from five irrelevant chunks, not the ordering of five
relevant ones.

~~**Next up: Phase 6.**~~ **Done 2026-08-06** — 200 rows, 50 of 52 lessons, all 9 single-chunk
lessons covered (N.3 showed those are structurally hard to retrieve, so they carry 3 questions each).

## Scorer changes — done 2026-08-06

All three shipped together in `score_retrieval_eval.py`, covered by
`tests/test_retrieval_scoring.py` (36 tests):

- [x] **Cohort and type partitions.** Both are read from the golden CSV at score time, same as
      labels — how a run is partitioned for reporting is rubric, not measurement, so re-partitioning
      an old dump costs nothing. The report gains a by-cohort and a by-type table, and the diff gains
      a **continuity check**: the core27 slice diffed against the 27-row baseline, which is the only
      like-for-like comparison that survives the expansion. It suppresses itself once the baseline is
      re-promoted at the full set.
- [x] **`Know Your Rights` disambiguated.** Lesson matching is now on the (module, lesson) pair.
      `first_hit_rank(..., expected_module=...)` opts in; without it, name-only matching is
      unchanged. Coverage counts pairs too, so a Module 3 question no longer marks Module 4's
      same-named lesson as covered. **Verified the 27-row baseline reproduces exactly** under pair
      matching — 22/27 hit@1, MRR 0.8781, every diff line "within noise".
- [x] **Noise floor scales with n.** The flat 2-question rule was right at n=27 (7.4pp) and far too
      tight at n=200, where 2 questions is 1pp — well inside sampling noise. `noise_questions(n, pct)`
      now uses one binomial standard error with the old value as a floor. It depends on the rate as
      well as n: 2 questions at n=27/p=0.815, 5 at n=200/p=0.815, and **7 at the measured n=200
      top-1 of 55.5%** (variance peaks at p=0.5). Deliberately unpaired and conservative; McNemar
      (sample-size table above) is still the honest test for whether a change ships.

Pair matching makes a wrong module label an automatic miss, so `check_label_pairs()` reports any
golden (module, lesson) pair that is not in the index. **This immediately caught a real mislabel:**
the three Module 3 workplace-rights questions had been written out with Module 4's label, because
the build script's lesson->module lookup collapsed the duplicate name. Fixed. Note the warning only
catches pairs that do not exist at all — the coverage count (50 of 52, not 49) is what exposed a
pair that was valid but wrong for its question.

## The n=200 run — 2026-08-07, promoted

200/200 retrieved, zero errors, sha e78d225. Lesson hit@1 **111/200 (55.5%)**, hit@5 172/200
(86.0%), hit@10 186/200 (93.0%), MRR 0.676. Noise floor is now **7 questions (3.5pp)** at this n
and rate — the scorer computes it rather than assuming 2.

**Retrieval did not regress.** The core27 continuity check reproduces the old baseline exactly on
every metric. What changed is that the measurement now covers the whole curriculum.

### N.3 confirmed at scale — chunk count drives top-1

Top-1 by the chunk count of the question's source lesson:

| chunks in the source lesson | top-1 |
|---|---|
| 1 chunk | 11/30 (36.7%) |
| 2 chunks | 58/106 (54.7%) |
| 3+ chunks | 42/64 (65.6%) |

Monotonic, and it holds across 200 questions rather than the single rank-8 anecdote that motivated
N.3. A lesson with one chunk gets one shot at the fused ranking while a 6-chunk lesson gets six.
**This is now the strongest argument in the document for revisiting chunking**, and unlike the
retracted n=27 claim it is not a labelling artefact.

### 14 questions (7%) never retrieve their lesson in the top 10

Unlike the retracted 27-row version of this claim, these were not checked one by one. Concentrated
in `Cycle of Liberation` (3), `Tactics for Mindful Decision Making` (2) and `Embracing Your Worth`
(2) — abstract mindset lessons whose vocabulary a student question rarely shares.

### Read 55.5% as a floor, not a settled number

Spot-checking the worst lessons shows a mix, and the two need separating before anyone reranks
anything:

- **Genuine near-duplicate lessons, i.e. label candidates.** "What's the difference between
  subsidized and unsubsidized?" is labelled `Student Loans` (M2) and returns `Student Loan Deep
  Dive` (M4) at rank 1 — which answers it. Same shape as the N.2 mislabels: the retrieval is right
  and the rubric is too strict. `Identifying Common Scams` vs `Online Financial Safety` and the two
  `Know Your Rights` lessons are the same story.
- **Genuine retrieval weakness.** `Selecting a Financial Institution` (1 chunk) is 0/5 on top-1 and
  lands at ranks 3, 3, 6, 7, 8 — the original N.3 finding, now reproduced five times over.

Relabelling costs nothing: the scorer re-reads labels from the CSV, so a pass over the misses can
be re-scored against **this same dump** with no new Azure spend, and it will print RUBRIC CHANGED
so the correction is never mistaken for a retrieval win.

## Recommendations — current priority order (2026-08-07)

This supersedes the ordering arguments in "Revised order" and "Phase order" above.

**0. Rate limiting on `/chat` and `/chat/stream` comes before any of this.** Not an eval task, but
it is the only live exposure on the board — `slowapi` is a dependency and nothing in `app/` imports
it. Everything below improves a system whose front door is open. See the to-do in `.claude/CLAUDE.md`.

**1. Hand-review the misses for near-duplicate-lesson mislabels.** Free: the scorer re-reads labels,
so this re-scores the existing dump with no Azure spend, and prints RUBRIC CHANGED so a relabel is
never mistaken for a retrieval win. Start with `Student Loans`/`Student Loan Deep Dive`, the two
`Know Your Rights`, and `Identifying Common Scams`/`Online Financial Safety`.

> **Set the rule before looking, or this is just moving the goalposts.** Relabel only when you have
> read the retrieved lesson's chunk text and it genuinely answers the question — the N.2 standard.
> Relabelling until the number improves is unfalsifiable, and the same 55.5% that looks bad is what
> makes a later win credible.

**2. Phase 4 (negatives and the abstain path) — moved ahead of Phase 5 and the reranker.** hit@5 is
86%, so roughly **1 in 7 student questions puts five chunks in front of the model that do not
contain the answer**, and `retrieve()` has no abstain path, so the bot answers from them anyway.
That is a failure students actually experience. A reranker only reorders chunks the model already
sees. Bigger payoff, lower cost, and it is the one phase that needs no decision from anyone else.
Expect the flat RRF distribution to make a clean threshold hard — that negative result is still
worth having, and points at scoring the vector leg separately.

**3. Re-chunk the 9 single-chunk lessons.** The 36.7% / 54.7% / 65.6% top-1 gradient by chunk count
is the clearest causal story in the data. Smaller chunk size with overlap so short lessons yield 2-3
chunks. **Judge on hit@10**, per the rules below — that is the metric that moves when material is
unreachable at any rank. One variable, before and after.

**4. Phase 5 (context precision), then a reranker.** 61 questions have the right lesson in the top 5
but not first, which is real headroom — but measure it against a corrected rubric (1) and a settled
chunking layout (3), or you are optimising against a moving target. At n=200 a 10-15pp top-1 shift
is finally detectable.

**5. Phase 6.4 multi-turn rows**, whenever the runner learns to seed session history. Also the only
way the query-rewrite arm becomes measurable.

### Content gaps — for whoever owns the curriculum, not fixable by retrieval

**Retirement and investing are essentially not in the index.** Across all 111 chunks: zero mentions
of 401(k), Roth, IRA, or index funds; "retirement" appears in 5 chunks, all in passing (e.g. a line
in `Spending Plan Foundations`). But `.claude/CLAUDE.md` describes Module 5 as covering "investing,
retirement". A student asking "should I open a Roth IRA?" gets five irrelevant chunks and an answer
generated from them. **No retrieval change fixes this — the content is missing.** Module 5 is also
the thinnest module in the index at 8 chunks across 4 lessons. Note the golden set cannot surface
this on its own: it was built from the chunks, so it can only ask what the corpus already covers.

**Three indexed documents cannot answer a student question.** `Introduction to Money Mindsets` and
`Conclusion` are instructor-facing syllabus/recap text; `Generational Legacies` is largely a link
dump. CLAUDE.md records that one course outline was excluded at indexing time; these were not.
That is ~6 of 111 chunks that can be retrieved *instead of* one that would help. Excluding them is a
one-line change to `src/preprocessing/index_modules.py` and should show up as a small hit@1 gain —
worth running as its own before/after.

## Rules for every run

- Run before **and** after each retrieval change. One variable at a time.
- Same questions, same index version, **same `--k`**, or the numbers are not comparable. The
  fingerprint check enforces this; do not talk yourself past a CONFIG CHANGED warning.
- Movement under the scorer's printed noise floor is noise — 7 questions (3.5pp) at n=200. Say so
  out loud when reporting a delta, including a favourable one. Do not hardcode the old 2.
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
