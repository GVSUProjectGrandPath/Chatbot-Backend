# FinLit Chatbot — Product Scope & Findings

A consolidated list of everything reviewed across architecture, compliance, quality, LLMOps, performance, and security — written so a non-engineer can follow the "what" and "why" of each point.

---

## Production Essentials (What's missing to serve 1000+ students safely)

1) **In-memory session store**: Conversation history is kept in a plain Python dictionary inside the running process. If the app restarts or you run more than one copy of it, students' conversations disappear or get split randomly between copies.
2) **No authentication on the API**: The backend only checks which website the request *says* it's coming from — that's easy to fake. There's nothing verifying the request is really from a real student using the chat widget.
3) **No rate limiting**: Nothing stops one person (or a script) from sending thousands of requests per minute, which could run up the AI bill or slow the bot down for everyone else.
4) **Logs written to a local file**: Activity logs are saved to a file on the server's disk, not a central place. If the app scales to multiple servers or restarts, those logs are scattered or lost.
5) **No documented data retention policy**: There's no written rule for how long student conversations, logs, and traces are kept or who can see them — important for a system handling FERPA-protected student data.
6) **No containerized/reproducible deployment**: There's no Dockerfile, so the exact environment the app runs in isn't locked down or easily reproducible.
7) **Health check doesn't check dependencies**: The "is the app alive" check only confirms the web server is running — not whether it can actually reach the AI service or the search database.
8) **No retry/backoff for external services**: If Azure's AI service has a brief hiccup, every user gets an error immediately instead of the system quietly retrying.
9) **No load testing done**: Nobody has verified the system can actually handle 1000 students using it at once — this is currently an unknown.
10) **Azure OpenAI quota/concurrency limits not accounted for**: Azure's AI service has a cap on how many requests and tokens can be processed per minute. With 1,000 students potentially asking questions around the same time, the app could start hitting `429 Too Many Requests` errors unless a higher quota tier (Provisioned Throughput) or a fallback plan is arranged in advance. *(Flagged during cross-review of gemini-prod-scope.md — a real gap I hadn't called out explicitly before.)*

---

## What's Hindering Chatbot Quality (Answers, Memory, Personalization)

1) **No test of the actual answers**: There's a test that checks the bot retrieves the *right course material*, but nothing checks whether the *final answer* it writes is actually correct, on-topic, or true to the retrieved material.
2) **Short-term memory during question rewriting**: Before searching for an answer, the bot rewrites a student's follow-up question into a standalone one — but it only looks at the last 2 exchanges. Anything said earlier in a longer conversation is invisible at this step.
3) **Aggressive history trimming**: The bot only keeps roughly the last 3-4 exchanges of a conversation before forgetting the rest — this is why longer chats can feel like the bot "forgot" what was said earlier.
4) **Personas are fixed, not adaptive**: Each student picks an animal "avatar" persona once at the start, and the bot never adjusts that persona based on how the student is actually behaving in the chat.
5) **No feedback loop from students**: There's no thumbs-up/thumbs-down or any way to know which answers actually helped — persona and prompt improvements are based on guesswork, not real usage data.
6) **No quality dashboard**: There's no ongoing tracking of how often the safety filters trigger, how often answers get blocked, or how good retrieval quality is over time — quality drift would go unnoticed.
7) **Multiple AI calls per message add cost and delay**: A single student message can trigger up to 4 separate AI calls (rewrite the question, generate the answer, check for prompt injection, check for bad advice) — this adds both latency and cost per message that isn't currently measured.
8) **Course material is split by word count, not by meaning**: The course content is chopped into pieces every ~400 words with no awareness of headings, tables, or step-by-step lists — so a definition or a numbered set of steps can get cut in half and fed to the AI as two disconnected fragments. *(Confirmed in `src/preprocessing/index_modules.py` — worth adding after cross-review.)*
9) **No relevance cutoff on retrieved course material**: The bot always hands the AI 5 chunks of course content for every question, even if none of them are actually related to what was asked — there's no minimum "how relevant is this" score, so the bot can be pushed into answering off-topic questions using irrelevant material. *(Confirmed in `app/services/chain.py`'s `retrieve()` — no score threshold or reranking step exists.)*
10) **Personas described in words only, no examples**: Each avatar's tone is defined with adjectives ("warm but never soft," "calm, organized") but no actual example conversation — without a concrete example to anchor it, the AI can drift toward a generic, samey voice across personas instead of a distinct one.

---

## LLMOps Issues (Senior AI Engineer / Ops lens)

1) **CI/CD deploys straight to production with no tests run**: Even though 6 test files exist in the project, the automated deployment pipeline never actually runs them before shipping to real students.
2) **Rate limiter installed but never turned on**: A library for limiting request rates (`slowapi`) is already part of the project's dependencies but isn't actually being used anywhere in the code — it was started and left unfinished.
3) **No versioning for AI prompts**: The instructions given to the AI (personas, safety rules) are just code with no version history tied to quality results — if a prompt change makes answers worse, there's no easy way to trace it back or roll it back independently.
4) **AI model/API version is hardcoded and outdated**: The specific version of Azure's AI service being used is over a year old, with no process for testing and safely upgrading it.
5) **No guaranteed matching environment between dev and prod**: There's no locked list of exact dependency versions carried through to deployment, so what's tested on a developer's machine may not exactly match what runs in production.
6) **No performance baseline tracked over time**: Nothing records how fast the bot responds or how many users it can handle at once, so a performance regression would only be noticed if students start complaining.
7) **Single point of failure on Azure services**: The bot depends on one AI service region and one search database, with no backup — if that goes down, the whole bot goes down.
8) **Course content re-indexing is a manual script**: When new course material needs to be added to the bot's knowledge base, someone has to manually run a script — there's no automatic process, and no way to tell if the search index is out of date.

---

## Optimizations (Performance / Code Efficiency)

1) **Guardrail scan duplication**: The safety-check step scans the same message twice in separate passes (once with simple pattern matching, once with a more advanced AI-powered scanner) for overlapping types of information — combining them would reduce repeated work.
2) **Blocking calls inside the async request handler**: Some slow operations (like the personal-info scanner) run in a way that freezes the server from handling other requests at the same time, instead of running in the background — this limits how many students can be served at once.
3) **Chat "pipeline" rebuilt on every single message**: The bot reconstructs its entire internal processing pipeline from scratch for every message, even though there are only 8 possible personas — this could be built once and reused.
4) **No caching for repeated questions**: Since all students share the same course material, many will ask very similar questions — right now every question triggers a full fresh search and AI generation, even if the same question was just answered minutes ago.
5) **Logging on the hot path**: Saving log entries to a file happens synchronously, meaning the server has to pause and wait for the disk write to finish before it can respond to the student — this adds small delays to every single message.
6) **Conversation history re-measured every turn**: Every message, the system recalculates the "size" (token count) of the entire chat history from scratch instead of tracking it incrementally as messages are added.
7) **No tuning of server worker processes**: The server isn't explicitly configured to use multiple worker processes to take advantage of all available server cores, so some computing capacity may sit unused under load.
8) **Browser re-checks permission on every request**: The server doesn't tell the browser how long it's allowed to remember that cross-site requests are permitted, so the browser sends an extra "am I allowed to do this?" check before nearly every real question — a one-line setting (`max_age`) would let the browser cache that answer for a day. *(Confirmed in `main.py`'s CORS setup — no `max_age` configured; added after cross-review.)*

---

## Security Code Audit

1) **Likely stored/reflected XSS via unsanitized LLM output**: The bot's replies (including AI-generated ones) are sent back to the website without being cleaned of potentially dangerous code — if the AI is ever tricked into including a malicious script in its answer, and the website displays that answer as raw content, it could run harmful code in a student's browser.
2) **Unauthenticated public API accepting arbitrary payloads**: Anyone who finds the backend's web address can send it requests directly, bypassing the actual chat widget — there's no login, token, or secret key required.
3) **No length limit on session ID and avatar fields**: Unlike the chat message itself (which is capped at 2000 characters), these two fields can be any size — someone could send extremely large values to try to overload the server's memory.
4) **AI-based safety checks can potentially be tricked**: The system uses the AI itself to catch attempts to manipulate it or extract personal information — this kind of check is known to sometimes be bypassable with cleverly disguised or encoded messages, and there's no testing in place to confirm how well it holds up.
5) **Personal information filter can be bypassed**: The pattern-matching and AI-scanning that block personal details (like SSNs, addresses, student IDs) only catch obvious formats — cleverly disguised or spaced-out personal information could slip through both layers, especially since the AI's safety check isn't designed to double up on this specific job.
6) **Unfiltered user text sent into the search engine's query language**: What a student types is passed almost directly into the search system, which has its own special characters and syntax — a student's phrasing could accidentally (or deliberately) break the search query or behave unpredictably.
7) **One setting away from leaking private data into logs**: A single on/off setting controls whether raw student messages get sent to Microsoft's monitoring tool for 90 days — if that setting is ever accidentally left on in production, private student data would be stored there with no second safeguard.
8) **Using a "beta" version of a core dependency in production**: One of the key software libraries the project relies on for search is still in beta/testing status, not a finished, stable release — beta software is generally less battle-tested and more likely to have undiscovered issues.
9) **Overly broad browser permission settings**: The server currently allows a website to send it any type of request header, when in practice only one specific type is ever needed — a small unnecessary opening that's easy to close.
10) **Unsafe text can reach the screen before the safety check finishes**: For the live "typing" style responses, the AI's words are shown to the student in real time as they're generated — but the check for personalized-advice/bad-content only runs *after* the entire response is finished. If that check decides to block the response, the student may have already read the un-checked version for a few seconds before it gets swapped out. *(Confirmed in `app/main.py`'s streaming handler — flagged during cross-review of gemini-prod-scope.md; this is a genuine gap I missed originally and should be treated as high priority.)*

---

## Corrections & Caveats (from cross-checking against a second review)

A second independent review (`gemini-prod-scope.md`) raised a few additional points that, on closer inspection of the actual code, turned out to be either inaccurate or overstated. Noted here for accuracy rather than silently dropped:

- **"The bot's safety check relies on a simple list of forbidden words"** — not quite right. The word list is only a cheap first pass that decides *whether to escalate* to a second, AI-powered check (`guardrails.py`'s `_classify_injection`) — it's not the only line of defense. The real concern (that an AI-powered check can itself be fooled by clever phrasing) still stands and is already covered above (Security #4).
- **"Malicious Markdown links could be injected into answers"** — the source links the bot includes only ever come from GVSU's own indexed course material, not from anything the student typed, so this specific path is narrower than it sounds. The broader risk — that nothing scrubs the AI's raw output before sending it to the browser — is real and already covered above (Security #1).
- **"Crafted messages could freeze the server via slow pattern-matching (ReDoS)"** — checked the actual pattern-matching rules in the codebase; none of them use the kind of open-ended, self-repeating pattern structure that typically causes this kind of slowdown, and messages are already capped at 2,000 characters. Low real risk today — worth revisiting only if the patterns get significantly more complex later.
- **"The AI ignores 'don't do X' instructions because too much is stacked into one prompt"** — plausible, and consistent with known small-model behavior, but there's currently no test in place that actually measures this (see Quality #1) — so it should be treated as a hypothesis to verify, not a confirmed problem yet.
- **"Responses get cut off mid-sentence due to a strict length limit"** — the current length limit was deliberately set with room to spare above the longest persona's typical response, per the code's own notes, so this looks like a considered choice rather than an oversight. Worth double-checking against real usage, but not a fire to put out.

---

## Suggested Fix Order (Highest Impact First)
1. Sanitize AI-generated replies before sending them to students (Security #1)
2. Buffer or gate live-streamed responses so unsafe content can't reach the screen before the safety check finishes (Security #10)
3. Turn on the already-installed rate limiter and require an access token from the chat widget (Production #2, #3)
4. Move conversation history storage to a shared, persistent system (like Redis) so the bot can run on more than one server (Production #1)
5. Make automated tests actually run before every deployment (LLMOps #1)
6. Build a simple test set to measure answer quality and safety-filter reliability (Quality #1, LLMOps quality tracking)
