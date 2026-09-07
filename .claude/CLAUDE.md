# FinLit Chatbot — Project Context for Claude Code

## Code style rules
- See @rules/code-style.md

## What this project is

A financial literacy chatbot for ~500 GVSU college students, embedded inside a LearnWorlds course platform. Students complete 6 self-paced financial education modules, pick an animal avatar based on a quiz, and use the chatbot for avatar-adaptive RAG help and continued engagement after course completion.

**Status: deployed and live.** The backend runs on Azure App Service and the LearnWorlds widget calls it directly. This is no longer a build-from-scratch project — changes now land on a system students are actively using, so guardrail/regression risk matters more than greenfield speed.

---

## Stack

- **Backend**: FastAPI on Azure App Service (Linux, Python 3.11, F1 tier — see Known gaps)
- **Agent/orchestration**: LangChain runnables (`RunnableWithMessageHistory`), **not LangGraph**
- **LLM**: Azure OpenAI GPT-4o-mini for chat + the guardrail judges
- **Embeddings**: text-embedding-3-small
- **Vector store**: Azure AI Search — index `finlit-modules`, 111 chunks live
- **Session memory**: in-process Python dict (`session_id → ChatMessageHistory`) inside `app/services/chain.py`. Lost on restart/redeploy. No CosmosDB yet.
- **PII/guardrails**: regex FERPA hard-block + Microsoft Presidio (custom GVSU/GPA/FinAid recognizers) + LLM classifier/judge for input and output
- **Frontend**: `Documentation/chatbot_script.txt` — a snapshot of the widget embedded in LearnWorlds (reference copy; the live source of truth lives in LearnWorlds). It streams from **`/chat/stream`** at a hardcoded Azure URL (`finlit-chatbot-backend-ccbjbfhvefcxhzhd.centralus-01.azurewebsites.net`) — `/chat` still exists but is not the path real students hit. See `Documentation/STREAMING_API_CONTRACT.md`.
- **Package manager**: `uv` (Python 3.11); `requirements.txt` is generated from the lockfile for Azure's Oryx build
- **Deploy repo**: `GVSUProjectGrandPath/Chatbot-Backend` via Azure Deployment Center + GitHub Actions

---

## Repository structure (actual)

```
FinLit-Bot/
├── app/
│   ├── main.py                  FastAPI app — GET /health, POST /chat, POST /chat/stream (live widget path)
│   └── services/
│       ├── chain.py             RunnableWithMessageHistory pipeline: rewrite query → retrieve → persona → LLM
│       ├── rate_limit.py        per-session + per-IP throttling (in-process, `limits`)
│       ├── guardrails.py        FERPA regex + Presidio + LLM input/output guardrails
│       ├── pii_detector.py      Presidio engine + custom GVSU-ID/GPA/FinAid recognizers
│       ├── llm.py               Azure OpenAI chat/embedding clients + Azure AI Search client
│       ├── avatars.py           8 avatar personas (system_prompt + voice + response_shape each)
│       └── logger.py            JSON logging + ContextVar request-id tracing
├── resources/
│   ├── data/
│   │   ├── raw/                 53 .txt files (52 lessons + 1 excluded course outline)
│   │   ├── cleaned/             52 cleaned .txt files
│   │   ├── chunks/chunks.json   local cache of 111 chunks (mirrors Azure AI Search)
│   │   └── video_manifest.csv   module/lesson metadata
│   └── logs/log.json            JSON request logs — gitignored
├── src/preprocessing/
│   ├── clean_data.py            lesson text cleanup (already run)
│   └── index_modules.py         chunk + upload to Azure AI Search
├── tests/
│   ├── test_chat_endpoint.py    end-to-end FastAPI tests (Azure mocked) for /chat and /chat/stream
│   ├── test_guardrails.py       FERPA/advice/injection regex + formatting tests
│   ├── test_indexing_pipeline.py
│   ├── test_azure_index.py      live Azure index integrity checks
│   ├── run_retrieval_eval.py    retrieval eval runner (not pytest) — replays 27 golden questions at k=10, dumps raw JSON
│   ├── score_retrieval_eval.py  scores a dump offline: lesson/module hit@1-10, MRR, score stats, diff vs baseline
│   ├── retrieval_baselines.json accepted retrieval baseline + config fingerprint (tracked; promote with --promote)
│   ├── run_golden_dataset.py    generation-side runner — replays golden CSVs through /chat
│   └── test_ferpa_sanitizer.py  STALE — imports a deleted src/nodes module and hardcodes absolute paths; does not run
├── test/                        CSV fixtures for the stale FERPA script above
├── Documentation/               frontend-facing docs + snapshot of the live widget script
│   ├── STREAMING_API_CONTRACT.md        ndjson event contract for /chat/stream
│   ├── FRONTEND_STREAM_HANDLING_FIX.md  open ask: widget must honor event `type`
│   ├── REWRITE_QUERY_STREAM_LEAK_FIX.md fixed: rewrite text leaking into the stream
│   └── chatbot_script.txt               snapshot of the live LearnWorlds widget
├── Additional-Docs/             specs, plans, explainers, brand assets
│   └── azure-app-service-deployment-plan.md   deploy steps + frontend wiring contract
├── README.md                    EMPTY — see To-do
├── pyproject.toml / uv.lock / requirements.txt
└── .env                         local secrets (gitignored); Azure uses Application Settings instead
```

`src/graph/graph_builder.py`, `src/nodes/`, and `src/states/state.py` no longer exist — the LangGraph design described in older notes was replaced by the LangChain runnable pipeline in `app/services/chain.py`. Don't resurrect it.

**Stale paths to watch:** `src/preprocessing/clean_data.py` and `index_modules.py` still reference `data/raw`, `data/cleaned`, and `data/chunks/chunks.json`, but the data moved to `resources/data/`. Those scripts won't run as-is from the repo root — fix the constants if you need to re-run preprocessing or re-index.

---

## Request flow (actual)

```
LearnWorlds widget
   │ POST /chat/stream { message, avatar, session_id }   (live path; /chat mirrors this non-streaming)
   ▼
app/main.py
   │ 0. check_rate_limit() — per-session 15/min+100/hr, per-IP 300/min. Runs first so a
   │    flood costs no Azure calls; 429 + Retry-After, body shaped like a normal reply.
   │ 1. ferpa_sanitizer() — hard regex block (FERPA terms), no LLM call
   │ 2. aguard_input() — Presidio PII (high-confidence blocks outright,
   │    borderline escalates to LLM) + injection prefilter → LLM classifier
   │ 3. build_chain(avatar).astream_events() — rewrite query → Azure AI Search
   │    hybrid retrieve (keyword + vector, top_k=5) → avatar persona system prompt
   │    → gpt-4o-mini, max_tokens=320 (tokens streamed as ndjson). Only the final-answer
   │    LLM call is tagged "final_response" so the rewrite call's tokens never reach the
   │    widget — see Documentation/REWRITE_QUERY_STREAM_LEAK_FIX.md
   │ 4. aguard_output() — runs AFTER the stream; deterministic "looks like advice" regex,
   │    escalates to an LLM compliance judge; fails CLOSED (blocks on judge error).
   │    On a block, emits a "replace" event AND sync_guarded_history() rewrites the
   │    persisted AI turn so follow-ups can't reference the ungated original.
   ▼
ndjson stream: {"type":"token"|"replace"|"error"|"done", ...}
```

Non-streaming `/chat` returns `{ "message": "...", "ferpa_blocked": bool }`. FERPA-blocked and guardrail-blocked responses return **HTTP 200** (so the widget renders them like normal messages). Pipeline failures return **HTTP 502** on `/chat`; on `/chat/stream` they surface as an `error` event followed by `done`.

**Known frontend gap (as of 2026-07-23):** the live widget appends every event's `content` and ignores `type`, so the `replace` event is NOT honored — guardrail-blocked text stays on screen. Backend-side history is still corrected via `sync_guarded_history`. Flagged to the frontend team.

---

## Retrieval & evaluation

`retrieve()` in `app/services/chain.py` runs an Azure AI Search **hybrid** query (keyword `search_text` + `VectorizedQuery` over `text_vector`), `top_k=5`, no semantic reranker. Every avatar gets the same 5 chunks — personas diverge only in which points they pick and how they say it.

The retrieval eval is a **runner/scorer split** (both standalone scripts, not pytest), so scoring never re-pays Azure for retrieval:

```
uv run python tests/run_retrieval_eval.py                     # k=10 dump -> resources/data/eval-runs/ (gitignored)
uv run python tests/score_retrieval_eval.py                   # scores newest dump, diffs vs baseline
uv run python tests/score_retrieval_eval.py <dump> --promote   # accept a run as the new baseline
```

Golden data lives **only** in `resources/data/golden-data/golden_dataset_curriculum.csv` (`question, expected_answer, module, lesson, cohort, type`) — the old hardcoded `GOLDEN_QA` list is gone. **200 rows** as of 2026-08-06: `cohort=core27` are the original 27, `cohort=phase6` the 173 added by hand against the indexed lesson text. `type` is `standard`/`vague`/`typo`. `expected_answer` is empty on the new rows and unused by both runners. Coverage is 50 of 52 indexed (module, lesson) pairs — the 2 holdouts are `Conclusion` and `Introduction to Money Mindsets`, instructor-facing text with nothing student-answerable.

Primary metric is **lesson-level** matching, on the **(module, lesson) pair** — `Know Your Rights` exists in both Module 3 (workplace/EEOC) and Module 4 (FCRA/disputes), and name-only matching scored one as a hit for the other. Module-level is kept as a legacy column because with 6 modules it saturates.

Accepted baseline (`tests/retrieval_baselines.json`, hybrid, no reranker, no rewrite, k=10, **n=200**, re-promoted 2026-08-07 after the relabelling pass): **lesson hit@1 119/200 (59.5%), hit@5 174/200 (87.0%), hit@10 187/200 (93.5%), MRR 0.704**; module hit@1 73.5%, hit@10 200/200. The pre-relabel figures on the same dump were 55.5% / 86.0% / 93.0% / 0.676 — that +4.0pp is a grading correction on 8 rows, **not** a retrieval change.

**The old 81.5% top-1 was a 27-row artefact, not a regression.** Retrieval is unchanged — the scorer's continuity check re-scores the core27 slice against the old baseline and reproduces 22/27 hit@1, MRR 0.8781 exactly. The 27-row set covered half the curriculum, and specifically the half someone had already written questions about.

Golden `module`/`lesson` cells may list several acceptable labels separated by `|` — some questions are genuinely answered by more than one lesson, and a single label grades a correct retrieval as a miss. The scorer **re-reads labels from the golden CSV at score time** (`--dump-labels` opts out), so fixing a label never costs another Azure run, and it prints `RUBRIC CHANGED` so a relabel is never mistaken for a retrieval win.

Rank of the correct lesson at n=200 (post-relabel): rank 1 x119, ranks 2-5 x55, ranks 6-10 x13, never x13. Two things follow. **Reranker headroom is 55 questions (27.5pp)** — the correct lesson is in the pool but not first; that is what a reranker reorders, and it is the largest addressable pool in the eval. And **the old "raising `top_k` past 5 is not worth it" is retracted**: 13 questions sit in ranks 6-10, where at n=27 it was 1. That is a reason to *test* a deeper `top_k` with a before/after, not to change it outright — more chunks means more prompt tokens on every student message.

**Chunk count drives top-1.** Grouping the 200 questions by how many chunks their source lesson has: 1 chunk 43.3%, 2 chunks 58.4%, 3+ chunks 70.2% (post-relabel; 36.7 / 54.7 / 65.6 before). Monotonic either way — the relabel lifted every bucket without touching the gradient. A 1-chunk lesson gets one shot at the fused ranking; a 6-chunk lesson gets six. **9 of 51 lessons are single-chunk**, and `Selecting a Financial Institution` (1 chunk) is 1/5 on top-1, landing at ranks 1, 3, 6, 7, 8 — and the rank-1 is a relabelled row, so on its own label it is still 0/5. This is the strongest evidence in the repo for revisiting chunking.

**The relabelling pass is done (2026-08-07) — 59.5% is the number to beat.** All 89 rows whose labelled lesson was not at rank 1 were hand-reviewed against the retrieved chunk text, under the N.2 standard: relabel only when the retrieved lesson genuinely answers the question, never merely because it is topically close. **8 of 89 were mislabels**, all now multi-label: `subsidized vs unsubsidized` (+`Student Loan Deep Dive`, which spells out the distinction), the two credit-report-rights questions (+`Filing a Dispute`, which contains both AnnualCreditReport.com and the 30-day investigation window), `worth switching banks?` (+`Avoiding Fees and Overdrafts`, which ends by telling you when to switch), `is borrowing worth it for my major` and `longer loan / lower payment` (+`Deciding to Take Out a Loan`, which covers both the is-it-worth-it framework and the term/payment tradeoff), `break out of bad money patterns` (+`Cycle of Socialization`, whose Financial Liberation section answers it), and `savings goal I'll stick to` (+`Creating New Habits`). The other 81 are genuine retrieval misses — the reviewed misses are dominated by magnet chunks, not by strict labels. Of the 81 remaining, `Give Yourself Grace` alone takes rank 1 on 19 of them spanning 13 different labelled lessons, and `What Factors Do Loan Officers Consider` on 10 spanning 10. That is a ranking problem (reranker, Phase 5), not a rubric one.

**Content gap that retrieval cannot fix:** across all 111 chunks there are zero mentions of 401(k), Roth, IRA, or index funds, and "retirement" appears only in passing — despite Module 5 being described below as covering "investing, retirement". A student asking about a Roth IRA gets five irrelevant chunks and an answer built from them. Module 5 is the thinnest module in the index (8 chunks, 4 lessons). Note `Introduction to Money Mindsets`, `Conclusion`, and `Generational Legacies` are indexed but are instructor-facing syllabus/outline text, so they occupy ~6 chunks that can be retrieved instead of ones that would help.

Caveats that matter when reading a diff: RRF fusion is depth-dependent, so hit@k off a k=10 dump is candidate-pool recall, **not** a prediction of the live `top_k=5` config — only compare dumps taken at the same `--k`. Query rewriting is a no-op on this dataset (every golden row is a fresh single-turn session, and `rewrite_query()` returns the question unchanged with no history), so all numbers are the no-rewrite arm. **Do not hardcode a noise threshold** — the scorer prints one from `noise_questions(n, pct)` (one binomial standard error, floor 2): 2 questions at n=27, 7 at n=200. A fixed count silently becomes far too tight as the set grows. `ragas` is in the dev dependency group for deeper RAG eval work (see the `Additional-Docs/rag-eval-*-spec.md` files) and `Documentation/RETRIEVAL_EVAL_PLAN.md` tracks the remaining phases (negatives/abstain, context precision, growing the set).

---

## Avatar system

8 avatar types in `app/services/avatars.py`, each an entry with `name`, `tagline`, `system_prompt`, `voice`, and `response_shape` (the per-avatar length/format control; `max_tokens=320` in `llm.py` is only a backstop). Avatar is selected client-side and sent as a string key on every request; `build_chain()` falls back to the panda persona for an unrecognized key. Source wording for the personas lives in `Documentation/FinLit Assistant_ Avatar System Prompts.txt` and `Additional-Docs/Avatar System Prompts.md`.

| Avatar | Type | Size |
|---|---|---|
| Squirrel | Saver — saves but won't invest | 265 students |
| Panda | Indifferent — financially passive | 249 students |
| Owl | Investor — strategic, wants depth | 176 students |
| Armadillo | Defensive — debt-avoidant | 150 students |
| Bee | Hustler — income-focused | 108 students |
| Poodle | Spender — lifestyle overspender | 45 students |
| Rabbit | Risk-Taker — high-risk gambler | 43 students |
| Octopus | Shopper — impulse buyer | 41 students |

Avatar is stored only in the in-process session dict (Phase 1). CosmosDB persistence (Phase 2: avatar type + streak counter, no PII) is still not implemented.

---

## FERPA compliance — hard constraints
- https://www.gvsu.edu/policies/policy.htm?policyId=3EDA3028-A1EF-7514-962877B732FDA124
- https://www.gvsu.edu/legal/ferpa-55.htm
- https://www.gvsu.edu/registrar/student-ferpa-faqs-19.htm

Enforcement is in `app/services/guardrails.py` — `ferpa_sanitizer()` (regex hard-block) plus the Presidio/LLM layer in `aguard_input()`. Any change to FERPA-relevant regex or prompts should be paired with a `tests/test_guardrails.py` case.

---

## Session disclosure banner

Should be shown to every student at session start, before any chat, in the LearnWorlds embed:

> Welcome to the FinLit Assistant. This tool is here to support your financial education. To protect your privacy under FERPA and GVSU policy, please do not share your name, student ID, GVSU email, grades, financial aid details, or account numbers. Your conversation is not saved after this session ends. This assistant provides general financial education only and does not give personalized financial advice.

Verify this is actually live in the widget — it was still a to-do as of the last architecture review.

---

## Course modules (6 total)

1. Money Mindset — socialization, generational patterns
2. Building Healthy Habits — accounts, financial aid navigation, avoiding fees
3. Money Management — budgeting, savings, salary negotiation, taxes
4. Navigating Credit — FICO scores, loans, credit cards, debt payoff
5. Planning for the Future — scams, insurance, investing, retirement
6. Financial Independence — ethical investing, consistency

**What is actually indexed differs from that list.** Module 5's 4 indexed lessons are insurance,
credit disputes, scams, and online safety — there is no investing or retirement content anywhere in
the 111 chunks (zero mentions of 401(k), Roth, IRA, or index funds). Module 6 covers ethical
investing conceptually but not mechanics. Treat the list above as the course syllabus, not as what
the bot can answer from.

---

## Known gaps before scaling to 500 live students

Owned by a teammate, don't duplicate this work: shipping structured logs to somewhere queryable (stdout → Log Stream, then Application Insights), and any deep `/health` dependency check (that work is naturally paired with the monitoring setup, not standalone). `resources/logs/log.json` is now gitignored and untracked (commit `a5e3cee`).

Deferred on purpose: **App Service tier is F1 (free)** — no "Always On," ~30s cold starts after ~20 min idle, daily CPU quota won't hold under live class traffic. Fine for pilot/testing in front of managers; upgrade to B1 (~$13/mo) before the real 500-student rollout, not before. See `Additional-Docs/azure-app-service-deployment-plan.md`.

### To-do (work through these one at a time, in separate sessions)

- [x] **CORS lockdown on `/chat`.** `ALLOWED_ORIGINS` (`app/main.py`) is now hardcoded to `https://www.rep4finlit.org` instead of `*`. Still needs confirmation from whoever owns `chatbot_script.txt` that this is the actual origin the widget calls from (vs. a `*.learnworlds.com` embed domain) before this is fully verified.
- [x] **Rate limiting on `/chat` and `/chat/stream`.** Done — `app/services/rate_limit.py`, checked at the top of both endpoints *before* the FERPA/Presidio guards, so a flood costs zero Azure calls. Two stacked limits: **per `session_id` 15/min + 100/hour** (the real fairness limit) and **per client IP 300/min** (anti-scripting backstop only, kept loose because students may share a campus NAT). Blocks return **429** with `Retry-After`, but in each endpoint's normal body shape (`{"message": ...}` on `/chat`, a `token`+`done` ndjson pair on `/chat/stream`) — the widget ignores `res.ok` and just appends `content`, so a throttled student sees a normal bot message. Logged as `rate_limited` with `block_reason` `rate_limit_session`/`rate_limit_ip`. **`slowapi` was dropped from `pyproject.toml`** in favor of `limits` (slowapi's own engine) used directly: slowapi's `key_func` is sync and only sees the `Request`, so it cannot key on `session_id`, which lives in the JSON body. Counters are in-process like the session dict — they reset on restart/redeploy and assume the single uvicorn worker.
- [x] **`/chat` integration tests.** `tests/test_chat_endpoint.py` hits the FastAPI app end-to-end with Azure mocked: normal answer, FERPA block, guardrail block, the 502 failure path, and a streaming regression test for the rewrite-query leak.
- [ ] **Frontend must honor the stream event `type`.** The widget still appends every event's `content`, so `replace` is ignored and blocked text stays on screen. Written up for the frontend team in `Documentation/FRONTEND_STREAM_HANDLING_FIX.md` — still open.
- [ ] **Verify the session disclosure banner is live** in the LearnWorlds embed before any chat starts. The snapshot at `Documentation/chatbot_script.txt` contains no disclosure text, so as far as this repo shows it is not live — confirm with whoever owns the widget.
- [ ] **Fix or delete the stale FERPA test harness.** `tests/test_ferpa_sanitizer.py` imports `src.nodes.ferpa_sanitizer_node` (deleted) and hardcodes `/Users/puneeth/Desktop/FinLit-Bot/...` paths. It cannot run. Either repoint it at `app/services/guardrails.ferpa_sanitizer` with relative paths to the `test/` CSVs, or remove it along with the CSV fixtures.
- [ ] **Fix the preprocessing script paths** (`data/*` → `resources/data/*`) so re-cleaning and re-indexing work from the repo root.
- [ ] **Retrieval eval follow-ups**, in order — full reasoning in `Documentation/RETRIEVAL_EVAL_PLAN.md` under "Recommendations":
  1. ~~Relabelling pass over the n=200 misses~~ — done 2026-08-07, 8 of 89 rows relabelled, baseline re-promoted at 59.5% top-1.
  2. Phase 4 — negatives and an abstain path. Promoted ahead of the reranker: at hit@5 = 87%, ~1 in 8 questions puts five irrelevant chunks in front of the model and it answers anyway.
  3. Re-chunk the 9 single-chunk lessons; judge on hit@10.
  4. Phase 5 context precision, then a reranker judged on top-1. The relabel pass sharpened the case for this: the surviving misses are magnet chunks winning rank 1 across a dozen unrelated lessons.
- [ ] **Raise with the curriculum owner:** no retirement/investing mechanics in the index (see Course modules), and 3 instructor-facing outline documents are indexed that cannot answer a student question.
- [ ] **Decide the session-memory story.** Single in-process dict (`app/services/chain.py`) forces exactly one uvicorn worker — any restart/redeploy/crash wipes every active conversation, and there's no path to horizontal scaling as-is. Either accept this permanently for this project's scale, or commit to the CosmosDB Phase 2 plan (avatar type + streak counter only, no PII).
- [ ] **Write `README.md`.** Currently empty — no dev setup, architecture overview, or "how to run this locally" for anyone joining the project.

---

## Architecture decisions already made (don't re-litigate)

- **LangChain runnables, not LangGraph**, for the chat pipeline. The LangGraph attempt in `src/graph/graph_builder.py` had broken stub nodes; it was abandoned in favor of `RunnableWithMessageHistory`. Do not extend the old graph code.
- **GPT-4o-mini for everything**, including the guardrail judges (not GPT-4o) — cost-driven.
- **Single uvicorn worker, no gunicorn multi-worker**, because session history is an in-process dict (see Known gaps #4).