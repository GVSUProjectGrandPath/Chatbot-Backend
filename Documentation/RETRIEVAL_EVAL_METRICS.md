# Retrieval Evaluation Metrics

What we measure about the retrieval half of the RAG pipeline, what is actually implemented
today, and what order to close the gaps in.

Scope: retrieval only — did we hand the LLM the right course material? Generation quality
(faithfulness, answer correctness, avatar adherence) is a separate concern and is not covered here.

Last audited: 2026-08-06 (Phases 0-3 of RETRIEVAL_EVAL_PLAN.md implemented same day)

## Why retrieval is scored separately from the answer

The pipeline is two stages in series:

```
question -> [rewrite -> retrieve 5 chunks] -> [gpt-4o-mini generates] -> answer
                    retrieval                        generation
```

A wrong answer has two possible causes with opposite fixes:

- the correct chunk was never retrieved (fix: chunking, hybrid weights, reranker, top_k)
- the correct chunk was retrieved and the model ignored it (fix: prompt, model, temperature)

Scoring only the final answer cannot tell these apart, so every regression turns into guesswork.
Retrieval metrics isolate the first stage.

## Current state

The eval is a runner/scorer split — `tests/run_retrieval_eval.py` dumps raw results at k=10,
`tests/score_retrieval_eval.py` scores the dump offline and diffs it against
`tests/retrieval_baselines.json`. Retrieval is paid for once per run; metrics are free to add.

Accepted baseline (hybrid, no reranker, no rewrite, k=10, 27 questions, 2026-08-06):

| metric | lesson-level (primary) | module-level (legacy) |
|---|---|---|
| hit@1 | 22/27 (81.5%) | 24/27 (88.9%) |
| hit@3 | 25/27 (92.6%) | 26/27 (96.3%) |
| hit@5 | 26/27 (96.3%) | 27/27 (100%) |
| hit@10 | 27/27 (100%) | 27/27 (100%) |
| MRR | 0.878 | 0.929 |

Moving to lesson-level did what it was supposed to: the headline number came off the 100% ceiling
and there is now headroom at hit@1 through hit@5.

Four things to know when reading these numbers:

0. **These numbers include the 2026-08-06 relabel (plan item N.2), not a retrieval change.** Two
   golden rows were labelled against course-outline lessons that do not answer their question; they
   were graded as misses while retrieval was returning the right material. Re-scoring the same dump
   with corrected labels moved lesson metrics up ~7pp across the board. `--dump-labels` reproduces
   the pre-relabel column (20/23/24/25, MRR 0.804) exactly. Golden cells may now list several
   acceptable labels separated by `|`, and the scorer re-reads labels from the CSV at score time so
   a label fix never costs another run.

1. **The June 2026 baseline reproduces.** Re-running the old script verbatim on 2026-08-06 gave
   27/27 hit@5 and 24/27 top-1 again, so the index has not drifted. The module top-1 above is
   23/27 rather than 24/27 because the golden CSV wording (now the only source) is a more casual
   paraphrase of the old hardcoded wording — one question, within noise.
2. **RRF fusion is depth-dependent.** Retrieving at k=5 scored lesson hit@5 = 25/27; truncating a
   k=10 dump to 5 scored 24/27. hit@k off a k=10 dump is therefore *candidate-pool recall*, not a
   prediction of the live `top_k=5` config. Only compare dumps taken at the same `--k` — the config
   fingerprint makes a mismatch loud.
3. **The golden set is 27 rows.** One question is 3.7pp, so the scorer labels any move under 2
   questions as within noise rather than as a result.

## What `retrieve()` already gives us

`retrieve()` (`app/services/chain.py:91-110`) returns per chunk:

```python
{"text": ..., "lesson": ..., "module": ..., "source_url": ..., "score": r["@search.score"]}
```

and takes `top_k` as a parameter. So `lesson`, `score`, and the k sweep are all available today —
they are discarded by the eval script, not missing from the retrieval layer. Chunk IDs are the
one thing genuinely absent: `id` is not in the `select=[...]` list on `chain.py:98`.

## Data we have

| File | Rows | Columns |
|---|---|---|
| `resources/data/golden-data/golden_dataset_curriculum.csv` | 27 | `question, expected_answer, module, lesson` |
| `resources/data/golden-data/golden_dataset_edge_cases.csv` | 100 | `question, expected_answer, category` (note: leading space on ` question`) |

The curriculum CSV now carries `lesson` and is the **only** golden source for retrieval — the
hardcoded `GOLDEN_QA` list and the script that held it are deleted. All 27 lesson labels were
verified against `resources/data/chunks/chunks.json` before being written in.

## Metric catalogue

Legend: DONE implemented / PARTIAL implemented but limited / TODO not implemented

### Core accuracy

| Metric | What it answers | Status | Notes |
|---|---|---|---|
| Hit rate @5 | Is a correct chunk anywhere in the top 5? | DONE | Lesson-level 26/27 — de-saturated. Module-level still pinned at 27/27 |
| Top-1 rate | Is the correct chunk ranked first? | DONE | Lesson-level 22/27 — the largest remaining gap |
| MRR | On average, how high does the correct chunk rank? | DONE | 0.878 lesson-level. Continuous, so it will not saturate |
| Recall@k sweep (k=1,3,5,10) | Is top_k=5 the right cutoff? | DONE | 22/25/26/27. hit@10 is +1 over hit@5, so deeper pools buy nothing — but this does not settle the reranker question |
| nDCG@5 | Rank-weighted quality across the whole result list | TODO | Needs graded (not binary) relevance labels |

The k sweep answers whether `top_k=5` is the right cutoff (measured: it is — hit@10 is one question
above hit@5). It does **not** decide the reranker question, despite the rule of thumb it was chosen
for: a reranker reorders rather than retrieves deeper, so the metric that captures it is top-1, and
there the headroom is 4-5 questions (14.8-18.5pp), not 1. See "What is left" below.

### Match granularity

| Metric | What it answers | Status | Notes |
|---|---|---|---|
| Module-level match | Right module retrieved? | DONE | Kept as a secondary column only, so the June baseline stays comparable |
| Lesson-level match | Right lesson retrieved? | DONE | Now the primary metric at every k |
| Chunk-level match | Exact right chunk retrieved? | TODO | Needs chunk IDs in the golden set and `id` added to `select=[...]` |

Tightening from module to lesson de-saturates the headline metric without collecting any new data.
This is the cheapest meaningful change on this page.

### Coverage and distribution

| Metric | What it answers | Status | Notes |
|---|---|---|---|
| Per-module breakdown | Is one module quietly failing? | DONE | After the N.2 relabel Module 1 is 4/4; the weakest is now Module 2 at 3/5 lesson hit@5 |
| Per-lesson coverage | Which lessons are untested? | DONE | Measured: 26/52 lessons covered, 26 untested. Listed in every run's metrics file |
| Question-type breakdown | Do vague, short, or misspelled queries degrade? | TODO | All 27 golden questions are well-formed sentences |

### Failure modes

| Metric | What it answers | Status | Notes |
|---|---|---|---|
| Out-of-corpus / negatives | Does retrieval return confident junk for uncovered topics? | TODO | Zero negative rows. `retrieve()` always returns 5 chunks with a score |
| Score distribution / top1-top5 margin | Is the ranking meaningful or effectively arbitrary? | DONE | top-1 mean 0.0327 over a 0.0312-0.0333 range; top1-top2 margin 0.0010. Effectively flat — see below |
| Abstain threshold validation | What score cutoff separates in-scope from out-of-scope? | TODO | Depends on negatives + score distribution above |
| Context precision | How many of the 5 chunks are genuinely relevant? | TODO | Needs per-chunk relevance judgments (LLM judge or manual labels) |
| Rewrite-step isolation | Does query rewriting help or hurt retrieval? | N/A at single turn | Resolved: `rewrite_query()` returns the question unchanged when a session has no history, and every golden row is a fresh session. Rewriting can only be measured on multi-turn rows |
| Multi-turn retrieval | Does retrieval work on follow-ups ("what about the other one?") | TODO | Every golden row uses a fresh session. This is what the rewrite step exists for |

The negatives gap is structural, not cosmetic: retrieval cannot fail loudly. Any question,
including ones the 111 chunks do not cover, produces five plausible-looking chunks that the LLM
then answers from. Nothing currently measures that path.

### Regression infrastructure

| Item | Status | Notes |
|---|---|---|
| Baseline recorded | DONE | `tests/retrieval_baselines.json` — metrics plus a config fingerprint (top_k, reranker, rewrite, index, embedding model, chunk count, git sha, date) |
| Timestamped runs, explicit promotion | DONE | Runs write to gitignored `resources/data/eval-runs/`; `--promote` is a separate act, so a bad run cannot become the reference |
| Automated before/after diff | DONE | Scorer prints a diff table labelling each metric IMPROVED / REGRESSION / within noise, and shouts if the config fingerprint changed |
| Golden set size | PARTIAL | 27 rows. Sub-5% changes are undetectable |

## What is left

Phases 0-3 of the plan are done: lesson-level matching, the k sweep, MRR, per-module and per-lesson
breakdowns, score statistics, and a promotable baseline with an automated diff. What remains needs
either new data or new spend:

1. **Out-of-corpus negatives** (~15 uncovered-topic questions), then an abstain threshold derived
   from where the in-corpus and out-of-corpus score distributions separate. Report the threshold
   before wiring it into `retrieve()`.
2. **Context precision** — LLM judge over the 5 chunks per question, behind a `--judge` flag,
   using GPT-4o and a disk cache keyed by (question, chunk hash).
3. **Grow the golden set to ~100** — proportional to module size, in student phrasing, including
   follow-ups and malformed queries. 26 of 52 lessons still have zero questions, and single-chunk
   lessons deserve extra weight (see the rank-8 miss below).
4. **Multi-turn rows** — the only way query rewriting becomes measurable at all.

### What the first run already tells us

Rank of the correct lesson, corrected labels: rank 1 x22, rank 2 x2, rank 3 x1, rank 4 x1,
rank 8 x1, never x0.

- ~~**2 of 27 questions never retrieve the correct lesson in the top 10.**~~ **Retracted 2026-08-06.**
  Both were mislabelled against course-outline lessons that do not answer their question; retrieval
  had returned the right material all along. Lesson hit@10 is 27/27, so there is no measured
  embedding ceiling on this set. The surviving chunking argument is different: 9 of 51 lessons have
  a single chunk, and the one remaining miss is exactly such a lesson losing a fused ranking to a
  6-chunk lesson.
- **`top_k=5` is the right cutoff.** hit@10 is one question above hit@5; deeper pools buy nothing.
- **The reranker question is still open, and the k sweep does not close it.** A reranker reorders,
  so its headroom is top-1, not recall@5: 4 questions (14.8pp, 95% CI [4.2%, 33.7%]) within a pool
  of 5, 5 questions (18.5pp) within a pool of 10. The recall@5 headroom is 1 question, but at n=27
  that has a 95% CI of [0.1%, 19.0%] and cannot carry a decision either way.
- **Whether ordering matters at all is a generation-side question.** All 5 chunks reach the prompt,
  so for presence of the right material rank 1 vs rank 4 is irrelevant. Phase 5 / the generation
  eval decides this, not retrieval metrics.
- **The score distribution is nearly flat** — top-1 RRF scores all in 0.0312-0.0333, mean top1-top2
  margin 0.0010. Little confidence signal, a warning for Phase 4's abstain threshold.
- **Module 1's two "misses" were label ambiguity, and it is now 4/4.** The suspicion recorded here
  was correct: "why do I feel bad spending on myself" and "how did growing up shape how I handle
  cash" were labelled against syllabus/outline lessons, while the thematically adjacent lessons they
  retrieved — `Cycle of Socialization`, `Give Yourself Grace` — are the ones that actually answer
  them. The weakest module is now Module 2 (Building Healthy Habits) at 3/5 lesson hit@5.
- **The only remaining lesson-level miss is a chunk-count artefact.** "Which bank or credit union is
  right for me?" puts `Selecting a Financial Institution` (1 chunk) at rank 8, behind
  `What Factors Do Loan Officers Consider` (6 chunks).

### How big the golden set has to be to decide anything

McNemar, 80% power, alpha 0.05: **~80** questions to detect a +15pp top-1 shift, ~157 for +10pp,
~471 for +5pp, ~361 for a +3.7pp recall@5 shift. Phase 6's ~100-row target is where a 10-15pp
top-1 effect becomes detectable; the recall@5 question is unanswerable at any realistic set size.
Treat every 1-2 question move on the current 27 rows as noise, including the ones above.

## Rules for running an eval

- Run before **and** after every retrieval change. One variable at a time.
- Same questions, same index version, or the numbers are not comparable.
- With a 27-row set, treat movement under ~2 questions as noise, not signal.
- Record the config alongside the numbers. A score without its config is not a baseline.
- A retrieval win should also show up as improved context recall in the generation eval. If
  top-1 improves and answers do not, the win was cosmetic.

## Related

- `tests/run_retrieval_eval.py` — collects the dump (`uv run python tests/run_retrieval_eval.py`)
- `tests/score_retrieval_eval.py` — scores it and diffs the baseline (`--promote` to accept a run)
- `Documentation/RETRIEVAL_EVAL_PLAN.md` — phase plan; Phases 0-3 done, 4-6 open
- `tests/run_golden_dataset.py` — collects real answers; scores nothing yet
- `app/services/chain.py:91` — `retrieve()`
- `.claude/CLAUDE.md` — Retrieval & evaluation section
