# Gemini Product Scope & Production Readiness Blueprint

---

## 1. Architectural & Scalability Essentials

### 1. In-Memory Session Store Bottleneck
* **Explanation:** Chat history is currently saved in a basic Python memory dictionary on the server. If the server restarts or if you run multiple servers to handle 1,000+ students, messages get lost and students get routed to servers that don't know their conversation history.
* **Fix:** Store chat conversations in an external database like Redis or Azure Cosmos DB with an automatic 1-hour expiration timer.

### 2. Missing Cryptographic Authentication & BOLA (Broken Object-Level Authorization)
* **Explanation:** The API currently trusts any session ID sent from the frontend without verifying who the student is. Anyone who guesses or changes a session ID can read or tamper with another student's chat history.
* **Fix:** Require signed student login tokens (JWTs from LearnWorlds SSO or University Shibboleth/Entra ID) on every request.

### 3. Azure OpenAI Quota & Concurrency Exhaustion
* **Explanation:** 1,000 students asking questions at the same time will exceed the default Azure token and request-per-minute limits, resulting in `429 Too Many Requests` crash errors for students.
* **Fix:** Purchase Provisioned Throughput (PTU) or set up multi-region deployment fallbacks with token-bucket rate limiting.

### 4. Hardcoded Health Checks
* **Explanation:** The current health check endpoint returns "ok" even if the database, search service, or AI model is down, meaning monitoring systems won't know when the chatbot is broken.
* **Fix:** Update the health check to actively test connections to Azure Search and Azure OpenAI before reporting a healthy status.

### 5. Missing External Service Retry & Circuit Breakers
* **Explanation:** If Azure OpenAI or Azure Search experiences a brief network blip, the chatbot immediately crashes the student's request with an error instead of automatically retrying behind the scenes.
* **Fix:** Implement exponential backoff retries and circuit breaker policies using libraries like `tenacity` for all external cloud calls.

### 6. Absence of Containerized & Environment-Locked Deployment
* **Explanation:** There is no `Dockerfile` or locked dependency freeze, meaning code running on a developer's computer may behave differently from what runs on the production server.
* **Fix:** Package the application in a hardened Docker container using pinned dependencies (`uv.lock` or `requirements.txt` with exact hashes).

### 7. Lack of Documented Data Retention & Lifecycle Policy
* **Explanation:** There is no formal automated rule or timeline for when student conversations, audit logs, and tracing records are permanently deleted, which is a compliance risk under FERPA.
* **Fix:** Define and automate a 30-to-90-day data retention and purge lifecycle across Redis, Azure Monitor, and Application Insights.

---

## 2. Security & Code Audit Findings

### 1. Stored/Reflected XSS via Unsanitized LLM Markdown Output
* **Explanation:** The chatbot creates Markdown links and returns raw HTML tags. If a malicious user tricks the AI into generating a link with harmful JavaScript, clicking that link in the student portal could steal user credentials or compromise accounts.
* **Fix:** Enforce strict link validation that only allows approved domain URLs starting with `https://www.rep4finlit.org/` and clean all output with an HTML sanitizer.

### 2. Post-Stream Output Guardrail Leakage
* **Explanation:** During streaming, response words appear on the student's screen in real-time *before* the output safety filter checks if the advice was appropriate. Students can read prohibited advice before the system tries to replace it.
* **Fix:** Buffer the first few words or sentences to run safety checks before sending them to the screen, or abort the stream immediately if unsafe tokens are detected.

### 3. Shallow Prompt Injection & Jailbreak Vulnerability
* **Explanation:** The system relies on a simple list of forbidden words (like "ignore" or "jailbreak"). Attackers can easily bypass this by asking questions in another language, using encoded text, or using creative roleplay prompts.
* **Fix:** Integrate an advanced model-based shield (such as Azure AI Content Safety / Prompt Shield) that understands intent rather than just matching exact keywords.

### 4. Unprotected PII Logging & FERPA Violation
* **Explanation:** Student questions and error messages are written directly to unencrypted text files on the server's hard drive. If a student types personal details, that data is stored insecurely without required privacy safeguards.
* **Fix:** Stop writing logs to local files. Stream logs directly to secure, encrypted cloud monitoring (Azure Monitor) and automatically mask sensitive information like student IDs, emails, and phone numbers.

### 5. Regular Expression Denial of Service (ReDoS)
* **Explanation:** Several pattern-matching safety rules use loose wildcard formulas. A student submitting a specifically crafted long message can cause the server processor to freeze at 100% CPU usage while trying to check the text.
* **Fix:** Simplify regex patterns and use linear-time search engines (like Google's RE2) to prevent processor lockups.

### 6. Unmetered Public Endpoints & Dormant Rate Limiting (`slowapi`)
* **Explanation:** A rate-limiting package (`slowapi`) is listed in the dependencies but was never wired into the server code, leaving the API completely open to automated scripts that can run up cloud AI bills.
* **Fix:** Activate `slowapi` with Redis to enforce strict limits (e.g., maximum 10 questions per minute per student).

### 7. Search Query Syntax & Lucene Injection Breakage
* **Explanation:** Student search questions are sent directly into Azure Search without filtering special characters (such as `+`, `-`, `&&`, `||`, `*`, `?`, `~`, `:`). Typing these symbols can break the query or cause search errors.
* **Fix:** Sanitize and escape all special query syntax characters before passing text to Azure AI Search.

### 8. Overly Broad CORS Header Permissions
* **Explanation:** The server allows arbitrary HTTP headers from the frontend rather than restricting requests to only the headers specifically required by the application.
* **Fix:** Restrict `allow_headers` in FastAPI's `CORSMiddleware` to only explicit headers (e.g., `Content-Type`, `Authorization`).

---

## 3. Performance & Workflow Optimizations

### 1. Synchronous Blocking Calls in Async Paths
* **Explanation:** The application uses synchronous search and embedding code inside an asynchronous web framework. While waiting for Azure to respond, the entire server process freezes and cannot handle requests from other students.
* **Fix:** Switch to native asynchronous clients (`AsyncAzureOpenAI` and `AsyncSearchClient`) so the server can handle hundreds of students concurrently.

### 2. Redundant Per-Request Chain Instantiation
* **Explanation:** Every time a message arrives, the server rebuilds the AI persona processing pipeline from scratch, wasting CPU and memory.
* **Fix:** Pre-build and cache all 8 avatar personas once when the server starts up.

### 3. Unnecessary Heavy NLP on Trivial Messages
* **Explanation:** The system runs a heavy language analysis engine (Presidio / spaCy) on every single message, adding a delay of 100–200ms even for simple messages like "hi", "ok", or "thanks".
* **Fix:** Add a quick check to bypass heavy security scans on standard conversational greetings under 3 words.

### 4. Regex Scan Loop Duplication
* **Explanation:** The system checks student messages against more than 20 individual pattern rules one by one in a Python loop.
* **Fix:** Combine all patterns into one single compiled master rule that checks the entire message in a single fast pass.

### 5. Logging on the Hot Path
* **Explanation:** The server pauses to write log files to the local disk during active conversation processing, slowing down response times.
* **Fix:** Output structured logs asynchronously to standard output (`stdout`).

### 6. Missing CORS Preflight Caching
* **Explanation:** Every time the student's browser asks a question, it sends an extra preliminary check request (OPTIONS) because caching headers are missing.
* **Fix:** Set `max_age=86400` in CORS settings to cache these checks for 24 hours, cutting browser network overhead in half.

### 7. Absence of Semantic Response Caching
* **Explanation:** Many students ask identical fundamental questions (e.g., "What is a FICO score?"). Right now, every identical question runs a full search and AI generation cycle from scratch.
* **Fix:** Implement a semantic cache (e.g., RedisVL / GPTCache) to instantly return verified answers for frequent, identical questions at zero AI token cost.

### 8. Multi-Worker Server Concurrency Tuning
* **Explanation:** The server is currently configured as a single process, leaving multi-core server processors underutilized during traffic spikes.
* **Fix:** Deploy FastAPI using Uvicorn workers behind Gunicorn (e.g., `workers = (2 * CPU_cores) + 1`) to maximize hardware throughput.

---

## 4. AI Engineering, RAG & Persona Steering

### 1. Attention Degradation & Prompt Overloading on `gpt-4o-mini`
* **Explanation:** Stacking personas, safety rules, formatting rules, and 5 course excerpts into one massive prompt confuses the smaller `gpt-4o-mini` model, causing it to ignore negative rules (e.g., telling a student to save money when instructed not to).
* **Fix:** Organize prompts into clear XML sections (`<persona>`, `<context>`, `<rules>`, `<response_format>`) to help the model focus.

### 2. Missing Few-Shot Persona Exemplars
* **Explanation:** Personas are currently described using vague adjectives (like "warm but never soft"). Without real examples, the AI defaults to a generic chatbot voice.
* **Fix:** Include 2 concrete example conversations (User question $\rightarrow$ Bot answer) in each persona definition to lock in the exact tone and style.

### 3. Naive Token-Based Chunking
* **Explanation:** Course materials are split strictly every 400 words. This cuts financial definitions, calculation tables, and numbered steps in half, feeding broken information to the AI.
* **Fix:** Switch to structure-aware Markdown chunking that splits text by lesson headings and complete concepts.

### 4. Missing Search Confidence Threshold & Semantic Reranking
* **Explanation:** The search engine always returns 5 chunks even if a question is totally unrelated to the course. Because there is no minimum relevance score, the AI tries to answer out-of-scope questions using irrelevant text.
* **Fix:** Add semantic reranking and a minimum similarity score threshold. If no chunks pass the threshold, the bot cleanly replies that the topic is not covered.

### 5. Dead Persona Metadata in Retrieval (`priority_modules`)
* **Explanation:** Each avatar specifies priority course modules (e.g., Panda focuses on Modules 1, 2, 3), but this information is completely ignored during search, resulting in generic retrieval.
* **Fix:** Use `priority_modules` as a boosting filter in Azure AI Search to prioritize the curriculum areas matching the student's avatar.

### 6. Context Blindness in Query Rewriting
* **Explanation:** The query rewriter only looks at the last 2 conversation turns. If a student asked a crucial question 4 turns ago, the rewriter loses the context and creates a poor search query.
* **Fix:** Include a running conversational summary when rewriting follow-up questions instead of a fixed 2-turn window.

### 7. Destructive Context Trimming
* **Explanation:** The system automatically deletes older messages when conversations get long. If a student shares their financial background in message 1, the AI forgets it 3 messages later.
* **Fix:** Implement a background memory summarizer that preserves key student profile facts throughout the conversation.

### 8. Hard Token Truncation Cliff
* **Explanation:** Setting a strict cutoff at 320 tokens cuts off longer, structured responses mid-sentence with broken formatting.
* **Fix:** Assign dynamic token limits matched to each persona (e.g., 150 tokens for short personas, 380 tokens for detailed personas).

---

## 5. LLMOps, Evaluation & Quality Gates

### 1. Absence of End-to-End RAG Triad Metrics
* **Explanation:** There is currently no automated way to check if chatbot answers are accurate, relevant, or hallucinated before releasing code updates.
* **Fix:** Implement automated testing using **Ragas / DeepEval** to score every build on:
  * **Groundedness:** Are answers backed strictly by course materials?
  * **Relevance:** Did the bot answer what was asked?
  * **Context Quality:** Were the retrieved chunks actually helpful?

### 2. CI/CD Deployment Without Automated Test Verification
* **Explanation:** Even though the project has 6 test files, the automated deployment pipeline pushes updates directly to production without running these tests first.
* **Fix:** Update GitHub Actions workflows to require 100% test passes before allowing deployment to staging or production.

### 3. Production Blindness via Disabled Tracing
* **Explanation:** Tracing content is disabled in production to protect student privacy, but this leaves engineers unable to see why a student got a bad response or why a question was blocked.
* **Fix:** Automatically mask sensitive student details with placeholder tags (`[REDACTED]`) before saving traces, allowing safe debugging in production.

### 4. Hardcoded & Outdated API / SDK Versions
* **Explanation:** The codebase uses older Azure OpenAI API versions (`2024-02-01`) and older search packages, risking feature deprecation and missing newer reliability fixes.
* **Fix:** Upgrade to stable, modern API versions and implement automated dependency update testing.

### 5. Lack of Student Feedback Loop
* **Explanation:** There is no way for students to give a thumbs up/down or report incorrect answers, meaning the development team receives no data on where the bot struggles.
* **Fix:** Add a feedback API endpoint (`/chat/feedback`) linked to telemetry dashboards to guide future model and prompt improvements.

### 6. Manual Knowledge Base Indexing
* **Explanation:** Updating course materials requires manually running a Python script on a local machine, with no automated tracking of index freshness.
* **Fix:** Build an automated ingestion pipeline that detects changes in course files and re-indexes the search database automatically with validation checksums.

---

## 6. Production Launch Checklist & Roadmap

| Priority | Area | Action Item |
| :--- | :--- | :--- |
| **P0** | **State** | Replace in-memory dictionary with Redis Cache (1-hour sliding TTL). |
| **P0** | **Security** | Implement JWT authentication; activate dormant `slowapi` rate limiting. |
| **P0** | **Compliance** | Remove local file logging; stream masked logs to encrypted Azure Monitor. |
| **P0** | **Safety** | Add sentence buffering to streaming to prevent output guardrail leaks. |
| **P1** | **Async** | Migrate sync OpenAI and Search calls to `AsyncAzureOpenAI` and `AsyncSearchClient`. |
| **P1** | **RAG** | Implement Markdown-aware chunking, search query escaping, and semantic reranking with score thresholds. |
| **P1** | **Prompts** | Add 2 few-shot conversation examples, XML tags, and dynamic token limits to each avatar. |
| **P1** | **CI/CD** | Add mandatory automated test runs and containerized Docker builds to the deployment pipeline. |
| **P2** | **Eval** | Configure automated Ragas/DeepEval CI/CD quality gates in GitHub Actions. |
| **P2** | **Telemetry** | Build a `/chat/feedback` endpoint and enable PII-masked production tracing. |
| **P2** | **Caching** | Implement semantic response caching for frequent, repeated student questions. |
