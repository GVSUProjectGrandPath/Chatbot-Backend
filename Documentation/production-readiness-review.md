# Production Readiness Review — FinLit Chatbot

A pass through the Chatbot-Backend codebase identifying issues that could cause failures, data exposure, or degraded performance once the bot is running at scale (~1,000 concurrent students) instead of light testing. Findings are grouped by area below, each with the issue and a proposed fix.

---

## Architecture / scaling stuff

- **Sessions are stored in a plain Python dict in memory.** If the server restarts or we run more than one instance, students lose their chat history or get bounced to a server that doesn't recognize them. Should move this to Redis or Cosmos DB with like a 1hr expiry.
- **Session IDs aren't checked against who's actually asking.** Anyone who guesses/changes a session ID can read someone else's chat. Need real auth (JWT via LearnWorlds SSO or school login) on every request.
- **Azure OpenAI will rate-limit us hard at 1,000 students.** We'll start seeing 429 errors. Need to either buy more throughput (PTU) or add rate limiting + fallback regions.
- **Health check always says "ok" even when stuff's broken.** It doesn't actually check if Search or OpenAI are reachable, so we won't get alerted when things go down.
- **No retries when Azure has a hiccup.** Right now any blip just errors out to the student instead of quietly retrying.
- **No Dockerfile / locked dependencies.** Risk of "works on my machine" not working in prod.
- **No real data retention policy.** Nothing automatically deletes old chat logs/traces — probably a FERPA issue since this is student data.

## Security stuff

- **LLM output isn't sanitized before rendering.** If someone tricks the bot into outputting a bad link/HTML, it could be an XSS vector. Should whitelist our domain only and sanitize everything else.
- **Streaming shows text before the safety filter checks it.** So a student could see something bad before we catch it. Need to buffer a sentence or two first.
- **Jailbreak protection is just a word blocklist** (blocking words like "ignore"). Trivially bypassed. Should use an actual model-based safety check instead.
- **Logs are plaintext files on disk with student PII in them.** Need to stop that, ship logs to Azure Monitor instead, and mask emails/IDs/etc.
- **A few regex patterns are sloppy enough that a long crafted message could hang the CPU** (ReDoS). Worth cleaning up.
- **We have `slowapi` (rate limiting) installed but never actually turned on.** So there's currently nothing stopping someone from spamming the API and running up our Azure bill.
- **Special characters in student questions can break Azure Search** (stuff like `+`, `&&`, `~`). Need to escape those.
- **CORS is way too permissive on headers** — should lock it down to just what we need.

## Performance stuff

- **Sync calls are blocking the async server**, so one slow Azure call freezes everything for other students. Should switch to async clients.
- **We rebuild the whole persona setup on every message** instead of just once at startup. Wasteful.
- **Heavy NLP scanning runs even on "hi" / "thanks"** — adds 100-200ms for no reason. Should skip it for short trivial messages.
- **20+ regex safety checks run one-by-one instead of combined** — could be one pass.
- **Logging happens mid-request and slows things down** — should be async / not blocking.
- **No CORS preflight caching**, so every question does an extra network round trip.
- **No caching for repeat questions** ("what's a FICO score" runs the whole pipeline every time). Semantic caching would save a ton of tokens/cost.
- **Only running a single worker process** even though the server has multiple cores sitting idle.

## AI / RAG / prompt stuff

- **Prompts are way too stacked for gpt-4o-mini** — persona + rules + 5 doc chunks all crammed together confuses it and it starts ignoring instructions. Should organize into clear sections.
- **Personas are just vague adjectives, no examples**, so the model defaults to generic chatbot voice. Adding 2 example exchanges per persona would help a lot.
- **Course content gets chunked every 400 words with no regard for structure** — cuts definitions and tables in half. Should chunk by actual lesson headings.
- **Search always returns 5 chunks even for totally unrelated questions** — no relevance threshold, so we try to answer stuff we shouldn't. Need a minimum score + a "not covered" fallback.
- **Each persona has a "priority_modules" field that's just... never used** anywhere in search. Dead code that should actually be doing something.
- **Query rewriting only looks at last 2 messages** — loses context from earlier in convo.
- **Long conversations just drop old messages** instead of summarizing — bot forgets stuff students told it early on.
- **Hard cutoff at 320 tokens cuts off responses mid-sentence.** Should be per-persona, not one fixed number.

## Testing / ops stuff

- **No automated way to check if answers are accurate/grounded** before we ship changes. Should add Ragas or DeepEval to the pipeline.
- **We have test files but deploys don't actually wait on them passing.** Kind of a big gap.
- **Tracing is disabled in prod (good for privacy) but that means we can't debug anything** when something goes wrong. Should mask PII in traces instead of turning tracing off entirely.
- **Using outdated Azure API versions.** Should bump these.
- **No feedback mechanism for students** (no thumbs up/down), so we have zero signal on what's actually working.
- **Updating the knowledge base is a manual script someone runs locally.** Should be automated.

---

## If I had to rank what matters most

**Do first (blockers):**
- Redis for sessions
- Real auth (JWT) + turn on rate limiting
- Stop local file logging / mask PII
- Buffer streaming so safety checks happen before text shows up

**Do soon after:**
- Async clients everywhere
- Better chunking + search relevance threshold
- Few-shot examples in prompts
- Tests actually gating deploys

**Nice to have:**
- Automated eval scoring
- Feedback endpoint
- Response caching
