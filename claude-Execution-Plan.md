# FinLit Chatbot — Execution Plan & Sequencing

A companion to `claude-Product-Scope.md`. The scope document says *what* is wrong; this one says *in what order we fix it and who waits on whom* — written so a non-engineer can follow both the sequence and the reasoning behind it.

Every item below references its scope-document ID (e.g. *Security #1*, *Prod #10*) so the two documents can be read side by side.

---

## How This Order Was Chosen

1) **Long-lead external items go out on day 0.** Anything that depends on Azure, on compliance/registrar sign-off, or on the frontend team has queue time we don't control. Those requests are sent before any code is written, so the waiting happens in the background instead of stalling the team later.

2) **The two "unblockers" come first — not the two scariest items.** A quality test harness and a shared conversation store each sit underneath eight to ten downstream items. Building them early means nothing later gets stuck waiting.

3) **Work is batched by surface, not by severity.** The original fix order asks the frontend team for four separate things at four separate times (sanitize replies, gate streaming, send an access token, add a feedback button). Bundling them into one contract means one integration and one round of QA instead of four.

**Assumed team:** roughly two backend engineers, one frontend engineer, plus a coordinator. With a single engineer, the parallel "lanes" below simply become the order of work within each wave.

---

## Day 0 — Fire These, Then Walk Away

These block nobody and start clocks we don't control.

1) **Azure OpenAI quota / Provisioned Throughput request** *(Prod #10)* — procurement and Azure approval take weeks. Requesting this in week five means load-testing against the wrong ceiling.
2) **Secondary region and search replica request** *(LLMOps #7)* — same approval queue, same paperwork. Bundle it into the same Azure ticket.
3) **Draft the data retention policy and send it for compliance review** *(Prod #5)* — pure document work with zero code dependencies, but it *sets* the conversation expiry window, the log retention period, and the tracing settings we're about to write. It needs to come back before Wave 2 lands.
4) **Send the frontend team one combined contract** — sanitized replies, buffered streaming, an access token header, and the feedback payload shape. One document, so they build once.

---

## Wave 1 (Weeks 1–2) — Three Lanes, No Cross-Blocking

### Lane A — Security (blocks going live at all)
1) **Sanitize AI-generated replies before they reach the browser** *(Security #1)* — backend scrubbing plus a frontend that renders text safely rather than as raw content.
2) **Gate live-streamed text behind the safety check** *(Security #10)* — buffer by sentence or delay emission so a student can never read content that's about to be blocked. This is the one item on the list where students are actively exposed today.
3) **Quick closures, used to fill gaps in the lane** — length caps on the session ID and avatar fields *(Security #3)*, escaping of search-query syntax *(Security #6)*, narrowing the browser permission headers *(Security #9)*, and making the raw-message tracing setting fail closed *(Security #7)*. Roughly an hour each.

### Lane B — Delivery Substrate (blocks every future change)
4) **Lock exact dependency versions** *(LLMOps #5)* and **add a Dockerfile** *(Prod #6)*.
5) **Make the deployment pipeline run the six existing test files as a merge gate** *(LLMOps #1)*.
6) **Move personas and safety rules into versioned files, with the version stamped onto every trace** *(LLMOps #3)*.

This lane is deliberately early rather than late. It is the cheapest work on the entire list, and every subsequent change is safer and faster once tests actually gate the pipeline.

### Lane C — Quality Test Harness (the long pole)
7) **Build a golden set of 100–150 questions with expected answers and citations, plus an adversarial set for prompt injection and personal-information leakage** *(Quality #1, Security #4, Security #5)*, wired in as an automated job.

This takes the most calendar time and unblocks the most downstream work: chunking, relevance thresholds, persona rewrites, the model upgrade, and every claim we want to make about the safety filters. Whoever is not blocked in Wave 1 should be building this.

---

## Wave 2 (Weeks 3–4) — Make It Survive More Than One Process

Everything here depends on Wave 1's container and test gate, and on the retention policy coming back from review.

8) **Move conversation history into Redis** *(Prod #1)*, with expiry set from the approved retention policy.
9) **Require an access token from the widget** *(Prod #2)*, then **turn on the already-installed rate limiter keyed to that identity** *(Prod #3)*. These ship as one ticket — rate limiting before authentication can only limit by network address, which is the wrong unit of measurement.
10) **Send structured logs to a central destination, off the response path** *(Prod #4 and Optimization #5)* — the same code change, so don't split it.
11) **Make the health check verify the AI service and search database** *(Prod #7)* and **add retry/backoff around Azure calls** *(Prod #8)*. Both are prerequisites for the load test producing meaningful numbers.
12) **Configure multiple worker processes** *(Optimization #7)* — correct only *after* Redis is in place. Before that, extra workers actively corrupt conversations.
13) **Run the load test** *(Prod #9)* — the gate at the end of this wave, and the first honest answer to "can we serve 1,000 students?" It requires the container, Redis, authentication, retries, and the Azure quota answer all to be present.

---

## Wave 3 (Weeks 5–7) — Quality, Now Measurable

None of this should be attempted earlier. Without the Wave 1 harness, prompt and retrieval changes are guesswork.

14) **Add a relevance cutoff and a reranking step to retrieved course material** *(Quality #9)* — the highest quality-per-hour item on the list, and the harness proves the gain.
15) **Chunk course content by structure rather than word count, re-index, and re-run the evaluation** *(Quality #8)*.
16) **Widen the rewrite context and the conversation history window** *(Quality #2, #3)* — and measure the resulting cost and latency change rather than simply raising the numbers.
17) **Give each persona a concrete example conversation** *(Quality #10)*, and **re-test the "too much stacked into one prompt" hypothesis** *(Caveat #4)* against the harness instead of assuming it.
18) **Ship the thumbs-up / thumbs-down feedback loop** *(Quality #5)* — the frontend has had the contract since day 0.
19) **Promote the adversarial guardrail results to a tracked, recurring metric** *(Security #4, #5)* rather than a one-time audit.

---

## Wave 4 (Weeks 8+) — Cost, Speed, and Operational Maturity

20) **Instrument per-message cost and latency, then collapse the four-call chain** *(Quality #7)* — measure before cutting.
21) **Efficiency cleanups**: merge the duplicated safety scan *(Optimization #1)*, move the blocking personal-information scanner off the request path *(Optimization #2)*, build the eight persona pipelines once and reuse them *(Optimization #3)*, track history size incrementally *(Optimization #6)*, and set the browser permission cache duration *(Optimization #8)*.
22) **Cache answers to repeated questions** *(Optimization #4)* — deliberately last, because caching a pipeline that is still changing means constantly throwing the cache away.
23) **Build the quality dashboard** *(Quality #6)* — it needs feedback data, central logs, and evaluation history before it has anything to plot.
24) **Upgrade the AI model/API version behind the harness** *(LLMOps #4)*, and **automate content re-indexing with a staleness signal** *(LLMOps #8)*.
25) **Adaptive personas** *(Quality #4)* — genuinely last. It needs real feedback data to define what "adapt" even means, and it is the only item on the list that is a research question rather than an engineering task.
26) **Replace the beta search dependency** *(Security #8)* once a stable release exists — track it, don't force it.

---

## Deliberately Not Scheduled

The cross-review in the scope document already established these are not real problems today. They stay documented and unbudgeted:

- **Slow pattern-matching / ReDoS** *(Caveat #3)* — no vulnerable pattern structure exists, and messages are already capped at 2,000 characters.
- **Responses cut off mid-sentence** *(Caveat #5)* — the length limit was a considered choice with headroom, not an oversight.

---

## Where This Plan Can Still Stall

- **The retention policy doesn't come back in time.** Redis ships with a placeholder expiry and a follow-up ticket. Do not block Wave 2 on the review.
- **The Azure quota request is denied or slow.** The Wave 2 load test still runs, but only against today's ceiling — so if there's no answer by the start of Wave 2, scope a short spike on queueing and backpressure as the fallback.
- **The quality harness slips.** Wave 3 then has nothing to stand on. This is the item to protect: if a lane frees up during Wave 1, put the spare capacity here.

---

## Two Changes From the Scope Document's Suggested Fix Order

1) **"Make automated tests actually run before every deployment" moves from #5 into Wave 1.** It is a few hours of work and it makes every subsequent change cheaper and safer.
2) **The quality test set starts in week 1, not at position #6.** Its *calendar time* — not its difficulty — is what gates the entire quality half of the scope.
