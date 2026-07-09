# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Setup** (requires [uv](https://docs.astral.sh/uv/)):
```bash
uv sync                        # install all dependencies including dev extras
uv sync --no-dev               # production deps only
```

**Run the API server:**
```bash
uvicorn app.main:app --reload
```

**Run the chain interactively (CLI test mode):**
```bash
python -m app.services.chain
```

**Tests:**
```bash
pytest                         # run all tests
pytest tests/test_guardrails.py                       # run a single test file
pytest tests/test_guardrails.py::test_ferpa_regex_blocks  # run a single test
pytest tests/test_guardrails.py -k "presidio"         # run by keyword
```
Most guardrail tests are deterministic (no Azure calls). Tests that hit Azure (`test_azure_index.py`, `eval_retrieval.py`) require `.env` credentials.

**Regenerate `requirements.txt` after changing `pyproject.toml`:**
```bash
uv export --no-dev --no-emit-project --no-hashes --format requirements-txt -o requirements.txt
```

**Data pipeline** (run from `src/preprocessing/`):
```bash
python index_modules.py                  # chunk cleaned docs → chunks.json
python index_modules.py --upload         # chunk + upload embeddings to Azure AI Search
python index_modules.py --upload --reset # wipe index first, then upload
```

## Architecture

This is a **FastAPI RAG chatbot** for financial literacy education at GVSU, deployed to Azure App Service. The core request path is:

```
POST /chat
  → FERPA hard-regex guard (ferpa_sanitizer)
  → Presidio PII guard + optional LLM injection classifier (aguard_input)
  → build_chain(avatar).ainvoke(...)
      → rewrite_query() — rewrites follow-ups into standalone questions using last 2 turns
      → retrieve() — hybrid (keyword + vector) search against Azure AI Search
      → AzureChatOpenAI (gpt-4o-mini) with avatar persona + trimmed history
  → output guardrail: regex prefilter → LLM judge (aguard_output)
  → return {message, ferpa_blocked}
```

### Key modules

| File | Role |
|---|---|
| `app/main.py` | FastAPI app, `/health` and `/chat` endpoints, request orchestration |
| `app/services/llm.py` | Singleton Azure OpenAI + Azure AI Search clients; all credentials read from `.env` |
| `app/services/chain.py` | LangChain RAG chain: query rewrite → hybrid retrieve → assemble → LLM; in-memory session history |
| `app/services/avatars.py` | 8 animal personas (panda, owl, squirrel, etc.), each with a unique system prompt and module priorities |
| `app/services/guardrails.py` | Three-layer input guard (FERPA regex → Presidio → LLM classifier) + output advice guard |
| `app/services/pii_detector.py` | Presidio `AnalyzerEngine` with GVSU-specific custom recognizers (G-numbers, GPA, financial aid) |
| `app/services/logger.py` | Structured JSON logger writing to `resources/logs/log.json`; uses `ContextVar` for per-request IDs |
| `src/preprocessing/index_modules.py` | One-time data pipeline: reads cleaned `.txt` files, chunks at 400 tokens (50 overlap), embeds, uploads to Azure AI Search |

### Guardrail layers (input, in order)

1. **FERPA hard regex** (`ferpa_sanitizer`) — structural patterns (SSN, G-number, card numbers, grade disclosures). Blocks before any LLM call.
2. **Presidio NER** (`analyze_pii`) — scores ≥ 0.85 block immediately; 0.50–0.85 escalates to LLM.
3. **LLM injection classifier** (`_classify_injection`) — called only when injection prefilter OR borderline Presidio fires. Returns `ALLOW`/`INJECTION`.
4. **Azure content filter** — jailbreak prompts that pass all local guards are rejected by Azure at the API level; `BadRequestError` with `content_filter` code is treated as a positive block.

### Guardrail layers (output)

- **Advice regex prefilter** (`looks_like_advice`) — cheap keyword check; only escalates to judge when it fires.
- **LLM compliance judge** (`ajudge_output`) — `ALLOW`/`BLOCK` verdict. Fails **closed** (blocks on error) because releasing personalized advice is the costlier mistake.

### Azure environment variables

Required in `.env` (not committed):
```
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_API_KEY
AZURE_OPENAI_CHAT_DEPLOYMENT       # defaults to gpt-4o-mini
AZURE_OPENAI_EMBEDDING_DEPLOYMENT  # defaults to text-embedding-3-small
AZURE_SEARCH_ENDPOINT
AZURE_SEARCH_API_KEY
AZURE_SEARCH_INDEX_NAME            # defaults to finlit-modules
```

### Data flow (offline pipeline)

Raw transcripts → `src/preprocessing/clean_data.py` → `resources/data/cleaned/*.txt` → `src/preprocessing/index_modules.py` → `resources/data/chunks/chunks.json` + Azure AI Search index (`finlit-modules`). The manifest `resources/data/video_manifest.csv` maps file names to module/lesson metadata and source video URLs.

### Deployment

Push to `master` triggers the GitHub Actions workflow (`.github/workflows/master_finlit-chatbot-backend.yml`), which builds and deploys to Azure App Service (`finlit-chatbot-backend`). The Oryx build engine runs `pip install -r requirements.txt` on the platform.

### Session history

Conversation history is held **in-memory** (`store` dict in `chain.py`), keyed by `session_id` (frontend-owned). History is lost on process restart. The chain trims to ≤ 1000 tokens per request using `trim_messages`.

### Tracing / observability

All trace and evaluation data must stay within Azure — do not route telemetry to LangSmith or any external service. Logs write to `resources/logs/log.json` as structured JSON; each log line carries a `request_id` from a `ContextVar` set at request start.
