# Azure AI Foundry Tracing for the FinLit Chatbot

This document explains, from the ground up, how we added **tracing** to the FinLit chatbot backend: what tracing is, why we needed it, every change we made (in the code, in the Azure portal, and in the Azure AI Foundry portal), why each step was necessary, and how to use the result day to day.

It is written so that you don't need prior experience with observability tools — every technical term is explained the first time it appears.

---

## 1. What problem were we solving?

When a student sends a message to the chatbot, the answer they get back is the result of a **seven-stage pipeline** running behind the scenes:

1. **FERPA regex check** — fast pattern-matching that blocks messages containing obvious personal identifiers (SSNs, G-numbers, phone numbers, "my GPA is…").
2. **Presidio PII scan** — a machine-learning scanner (Microsoft's open-source Presidio library) that catches personal information the patterns miss, like names.
3. **LLM injection classifier** — a language-model check that blocks "prompt injection" attempts (messages trying to trick the bot into ignoring its instructions).
4. **Query rewrite** — a language-model call that turns follow-up questions like *"what about the fees?"* into standalone questions like *"What fees are associated with credit cards?"* so the search step works well.
5. **Retrieval** — a search against our Azure AI Search index of course material, which returns the most relevant lesson chunks.
6. **Answer generation** — the main language-model call: the avatar's persona prompt + the retrieved course chunks + the student's question go in, the answer comes out.
7. **Output guardrail** — a final check that the answer is general financial *education*, not personalized financial *advice*, before it reaches the student.

Before tracing, when something went wrong — a good question got blocked, an answer cited the wrong lesson, a response took 15 seconds — our only evidence was log lines. Logs tell you *that* something happened, but not the full picture: you couldn't see what the search actually returned, what the exact prompt sent to the model looked like, or which stage ate all the time.

**Tracing fixes this.** Every chat request now produces a *trace*: a complete, timed, tree-shaped record of all seven stages, including the raw text flowing between them. The team can open a trace in the Azure AI Foundry portal and see the entire journey from the student's input text to the bot's output text.

---

## 2. Key terms, in plain language

| Term | What it means |
|---|---|
| **Telemetry** | Any data an application emits about its own behavior (timings, events, errors) so people can observe it from the outside. |
| **Trace** | The full record of *one* request as it moves through the system — for us, one trace per `/chat` call. |
| **Span** | One step inside a trace. A span has a name (e.g. `rag.retrieval`), a start time, an end time (so we know its duration), and can carry extra data. Spans nest, forming a tree: the `chat_request` span is the root, and every pipeline stage is a child underneath it. |
| **Attribute** | A key–value pair attached to a span — this is where the *content* lives, e.g. `retrieval.query = "What fees are associated with credit cards?"`. |
| **OpenTelemetry (OTel)** | The industry-standard, vendor-neutral toolkit for producing traces. Our code uses OpenTelemetry APIs, which means the instrumentation is not locked to any one vendor. |
| **Exporter** | The component that ships the spans your code produces to a storage/viewing backend. We use the Azure Monitor exporter, so spans go to Azure. |
| **Application Insights** | Azure's telemetry storage and analytics service. This is the database where our traces physically live. |
| **Azure AI Foundry** | Microsoft's portal for AI projects (ai.azure.com). Its **Tracing** page is a purpose-built viewer for AI traces — it reads from the Application Insights resource connected to our Foundry project and renders each request as an expandable tree. |
| **Instrumentation** | Adding the code that creates spans. "Auto-instrumentation" means a library does it for you — we auto-instrument the OpenAI SDK so every model call becomes a span automatically. |
| **Managed identity** | An identity Azure creates *for an application* (our App Service) so it can authenticate to other Azure services without any password or API key stored anywhere. |
| **RBAC (role-based access control)** | Azure's permission system. You grant a *role* (a named bundle of permissions) to an identity on a resource. Several of our setup steps were about finding the right role. |
| **Connection string** | A single configuration value that tells the exporter *which* Application Insights resource to send data to. |

---

## 3. How the pieces fit together

```
Student message
      │
      ▼
FastAPI backend (App Service / local dev)
  ├─ our span code (OpenTelemetry) ── describes each pipeline stage
  └─ OpenAI SDK auto-instrumentation ── captures every LLM call + raw prompts
      │
      ▼  (Azure Monitor exporter, batched in the background)
Application Insights  ←— the storage layer, lives in our Azure subscription
      │
      ▼  (read-only)
Azure AI Foundry portal → project "REP4FinLit-chatbot-tracing" → Tracing page
```

Two important properties of this design:

- **Everything stays in Azure.** This was a hard project requirement — trace data contains student messages, so it must not leave our tenant. No third-party observability service (LangSmith, Datadog, etc.) is involved at any point.
- **Tracing can never break the chatbot.** If tracing isn't configured (or fails to start), the app runs exactly as before — every span call silently becomes a no-op. Exporting happens in a background thread, so it adds no latency to student requests.

---

## 4. What we changed in the code, step by step

### Step 4.1 — A single setup module: `app/services/tracing.py`

All tracing bootstrap lives in one small file with two functions:

- **`setup_tracing()`** — called once when the app starts (from `app/main.py`). It:
  1. Reads the environment variable `AZURE_AIPROJECT_ENDPOINT`. **If it's not set, tracing is disabled and the function returns immediately** — this is why local development works with zero setup.
  2. Connects to our Foundry project using `AIProjectClient` and `DefaultAzureCredential` (see §4.5 for what that credential is).
  3. Asks the project for the **connection string** of its linked Application Insights resource.
  4. Calls `configure_azure_monitor(...)`, which installs the OpenTelemetry exporter pointed at that resource.
  5. Activates **auto-instrumentation of the OpenAI SDK**, so every chat-model and embedding call in the app — including the ones LangChain makes internally — automatically becomes a span carrying the full prompt and response.
  6. Wraps all of the above in a `try/except`: if anything fails, we log it and run untraced rather than crash the app.

- **`get_tracer()`** — returns the app-wide tracer that the rest of the code uses to create spans. If setup never ran, it returns a **no-op tracer**, so calling code never needs to check "is tracing on?".

**Why this design:** one env var toggles the whole feature; no other file needs to know how exporting works; and a tracing outage can never take the chatbot down.

One subtle but important line: we set `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` before instrumenting. By default, OpenTelemetry **redacts** prompt and response text from LLM spans (a privacy-safe default for public backends). Because our traces stay inside our own Azure tenant, we opt in to capturing the raw text — that's the entire point: seeing the *exact* system prompt (persona + retrieved course chunks) the model received.

### Step 4.2 — The root span: `app/main.py`

The `/chat` endpoint wraps the whole pipeline in one span named **`chat_request`**. Everything else nests under it, which is what makes each request appear as a single tree in the portal. On this root span we record:

- `session_id` and `avatar` — so traces can be filtered by conversation or persona,
- `gen_ai.input.message` — the student's raw message,
- `gen_ai.output.message` — the final answer,
- `pipeline.stage_blocked` — if a guardrail stopped the request, *which one*.

So even without expanding a trace, the top-level row answers: what came in, what went out, and whether/where it was blocked.

### Step 4.3 — Guardrail spans: `app/services/guardrails.py`

Each safety layer got its own span, recording its decision and *why*:

| Span | What it records |
|---|---|
| `guardrail.ferpa_regex` | Whether the FERPA pattern layer blocked, and **which exact pattern matched** — essential for tuning false positives ("why was this innocent message blocked?"). |
| `guardrail.input` | Parent span for the model-based input checks; records the final blocked/allowed decision and the reason. |
| `guardrail.presidio_pii_scan` (child) | How many personal-information entities Presidio found, split into high-confidence (block immediately) and borderline (escalate to the LLM), and their types (`PERSON`, `US_SSN`, …). |
| `guardrail.injection_classifier` (child) | Only appears when something suspicious triggered it. Records why it was escalated and the model's verdict (`INJECTION` / `ALLOW`). |
| `guardrail.output` | The final advice check on the *answer*: whether the "looks like advice" prefilter fired, and the outcome. |
| `guardrail.output_judge` (child) | Only appears when the prefilter fired. Records the judge model's `ALLOW`/`BLOCK` verdict, so block decisions are auditable after the fact. |

**Why:** guardrails are where "the bot refused to answer me" complaints originate. With these spans, any block can be traced to the specific layer, rule, or model verdict that caused it — in seconds, not hours of log spelunking.

### Step 4.4 — RAG spans: `app/services/chain.py`

The retrieval-augmented-generation ("RAG") stages got spans too:

- **`rag.query_rewrite`** — records the student's original question and the rewritten standalone version side by side, plus a `rewrite.skipped` flag for first messages (nothing to rewrite yet). This lets us judge rewrite *quality*: is the rewriter preserving intent, or distorting questions before they hit search?
- **`rag.retrieval`** — records the query sent to Azure AI Search, how many chunks came back, and for each chunk its module, lesson, and relevance score. This answers the most common RAG debugging question: *"did the model give a bad answer, or was it handed bad material?"*

The main answer-generation call needs no manual span — the OpenAI SDK auto-instrumentation from Step 4.1 captures it, including the complete assembled system prompt (persona + safety line + retrieved chunks + formatting rules) and the model's full response.

### Step 4.5 — New dependencies: `pyproject.toml` / `requirements.txt`

Four packages were added, each with a specific job:

| Package | Job |
|---|---|
| `azure-ai-projects` | The `AIProjectClient` used to ask our Foundry project for its App Insights connection string. |
| `azure-identity` | Provides `DefaultAzureCredential` — a "smart" credential that automatically uses the **managed identity** when running on Azure App Service and your **`az login`** session when running locally. Same code, correct auth in both places, and **no API keys stored anywhere**. |
| `azure-monitor-opentelemetry` | The OpenTelemetry SDK plus the Azure Monitor exporter — the machinery that actually ships spans to Application Insights. |
| `opentelemetry-instrumentation-openai-v2` | The auto-instrumentation for the OpenAI SDK (the raw-prompt capture). |

---

## 5. What was done in Azure (portal + admin), and why

This is the part that isn't visible in the code. Getting permissions right took real investigation, so it's documented carefully — if anyone repeats this setup, this section will save them days.

### Step 5.1 — Created an Azure AI Foundry project

We created the Foundry project **REP4FinLit-chatbot-tracing** (resource `rep4finlit-chatbot-trac-resource`, resource group `Rep4FinLit-Resource-Group`). The Foundry Tracing UI is *project-based* — traces are viewed in the context of a project — so a project is the entry point for everything else.

The project's endpoint URL is what the backend needs at startup:

```
AZURE_AIPROJECT_ENDPOINT=https://rep4finlit-chatbot-trac-resource.services.ai.azure.com/api/projects/REP4FinLit-chatbot-tracing
```

### Step 5.2 — Solved the permissions puzzle (admin)

The backend's startup call — "give me your Application Insights connection string" — requires a specific fine-grained permission on the Foundry resource:

```
Microsoft.CognitiveServices/accounts/AIServices/connections/read
```

Finding a role that actually grants this took three attempts, and the findings matter:

1. **First attempt:** the admin assigned the built-in **Azure AI Developer** role. The call still failed with `PermissionDenied`. Investigation showed two separate problems:
   - The assignment had been made as a **PIM "eligible" assignment**. PIM (Privileged Identity Management) is Azure's just-in-time permission system — an *eligible* role is one you *may activate* for a limited time window; until you click **Activate**, you don't actually have it. It also doesn't show up in normal role listings, which made it look like the assignment had vanished.
2. **Second attempt:** after activating the role… still `PermissionDenied`. We dumped the role's actual definition and found the root cause: **Azure AI Developer does not include the permission we need.** Its data-level permissions cover OpenAI, Speech, and Content Safety operations — but *not* the `AIServices/connections/read` action that the Foundry telemetry call uses. The role's name suggests it should work; its contents say otherwise.
3. **Resolution:** searching the tenant's role definitions for one whose permissions include `Microsoft.CognitiveServices/*` found the **Foundry User** role. The admin assigned **Foundry User** on the resource group `Rep4FinLit-Resource-Group` to **two identities**:
   - **Erick's user account** — so tracing works when running the backend locally during development, and
   - **the App Service's managed identity** (`finlit-chatbot-backend`) — so tracing works in production. Without this second assignment, production would have failed with the exact same error we'd just spent days solving locally.

> **Key takeaway for anyone repeating this:** for Foundry tracing, assign **Foundry User**, not Azure AI Developer — and remember the app's managed identity needs it too, not just the humans.

### Step 5.3 — Registered resource providers (one-time subscription fix)

When creating the Application Insights resource (next step), Azure returned `MissingSubscriptionRegistration` for `Microsoft.OperationalInsights`. Azure subscriptions must "register" each service family before first use — a one-time, harmless switch that is off by default in new subscriptions. We registered two providers via the CLI:

```
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.Insights
```

(The first is Log Analytics, which backs every modern Application Insights resource; the second is Azure Monitor itself.)

### Step 5.4 — Connected Application Insights to the project (Foundry portal)

In the Foundry portal (ai.azure.com) → project → **Tracing**, we created a new Application Insights resource, **`finlit-chatbot-appinsights`**, and connected it to the project. This is the actual database traces are written to. The connection is what makes the whole flow work: the backend *asks the project* "where do I send telemetry?", and the project answers with this resource's connection string — so the destination is configured in Azure, not hard-coded in our repo.

### Step 5.5 — Set the environment variable on the App Service (Azure portal / CLI)

Finally, production needed the same switch local dev uses. We added `AZURE_AIPROJECT_ENDPOINT` (the URL from Step 5.1) as an **App setting** on the `finlit-chatbot-backend` App Service — the App Service equivalent of a line in a local `.env` file. On the next deployment of this code, the app reads it at startup, and production traces begin flowing.

Note what was **not** needed in production: no API key, no connection string, no secret of any kind. The managed identity (Step 5.2) plus this one URL is the entire production configuration.

---

## 6. How to use the traces (day-to-day guide)

Open **ai.azure.com → project `REP4FinLit-chatbot-tracing` → Tracing** (left sidebar). Each row is one chat request. Click a row to expand its tree.

### Reading a trace

A typical healthy request looks like this (durations from a real trace):

```
chat_request                              8.1 s   ← the whole request
├─ guardrail.ferpa_regex                 <0.01 s  ← pattern check: instant
├─ guardrail.input                        0.02 s
│  └─ guardrail.presidio_pii_scan                 ← ML PII scan
├─ rag.query_rewrite                      0.9 s
│  └─ chat gpt-4o-mini                            ← the rewrite LLM call
├─ rag.retrieval                          0.4 s
│  ├─ embeddings                                  ← turning the query into a vector
│  └─ SearchClient.search                         ← Azure AI Search lookup
├─ chat gpt-4o-mini                       5.5 s   ← the main answer generation
└─ guardrail.output                      <0.01 s
```

Click any span and look at its **attributes** — that's where the content is:

- On the **main `chat` span**: the complete raw prompt (persona + retrieved course chunks + formatting instructions) and the model's full answer, plus token counts.
- On **`rag.retrieval`**: which module/lesson chunks were retrieved and their relevance scores.
- On **`rag.query_rewrite`**: the question before and after rewriting.
- On **guardrail spans**: blocked or not, and the exact reason (which regex pattern, which PII entity types, which verdict).

### Debugging a bad answer — recommended order

1. **`rag.query_rewrite`** — did the rewriter distort the question? If yes, the problem starts here.
2. **`rag.retrieval`** — did the right lessons come back, with reasonable scores? If the material is wrong, the answer never had a chance.
3. **Main `chat` span** — the material was good but the answer wasn't? Read the exact prompt the model received; the fix is usually a prompt change.

### Debugging a wrongly blocked message

Look at `pipeline.stage_blocked` on the root span, then open that guardrail's span: `ferpa.matched_pattern` names the exact regex that fired; Presidio spans list the entity types detected; classifier/judge spans show the model's verdict. This turns "the bot blocked me for no reason" into a concrete, fixable rule.

### Performance questions

Span durations show exactly where time goes. In the example above, the main model call is 5.5 s of the 8.1 s total — so any latency work should target generation (shorter prompts, smaller model, streaming), not the guardrails, which are effectively free.

### Offline evaluation

Because traces are ordinary Application Insights data, we can analyze them in bulk, not just one at a time. In the Azure portal → `finlit-chatbot-appinsights` → **Logs**, KQL (Kusto Query Language — Azure's log query language, similar in spirit to SQL) can answer questions like:

- What fraction of requests were blocked, by guardrail stage, this week?
- What's the median / 95th-percentile response time?
- How often does the rewriter change questions? How many tokens does an average request consume?

This is the foundation for a future evaluation loop: export real question/context/answer triples from traces and score them for groundedness and relevance — all still inside Azure.

---

## 7. Quick reference

| Item | Value |
|---|---|
| Foundry project | `REP4FinLit-chatbot-tracing` (resource `rep4finlit-chatbot-trac-resource`) |
| Resource group | `Rep4FinLit-Resource-Group` |
| Trace storage | Application Insights `finlit-chatbot-appinsights` |
| Viewing UI | ai.azure.com → project → **Tracing** |
| On/off switch | `AZURE_AIPROJECT_ENDPOINT` env var (unset = tracing off, app unaffected) |
| Required RBAC role | **Foundry User** (on the resource group) — *Azure AI Developer is not sufficient* |
| Auth, locally | `az login` (via `DefaultAzureCredential`) |
| Auth, production | App Service **managed identity** — no keys or secrets anywhere |
| Code entry point | `app/services/tracing.py` → `setup_tracing()`, called from `app/main.py` |
| Data boundary | All telemetry stays in our Azure tenant — no external services |
