# 🔴 Persist conversation history in Redis

**Epic:** Production Readiness
**Type:** Technical Story
**Priority:** High
**Estimate:** 3 points (~1.5 days engineering + external provisioning wait)
**Blocks:** Horizontal scaling, Multi-instance deployment, Data retention policy
**Source:** `claude-Product-Scope.md` → Production Essentials #1
**Fix order rank:** 4 of 6

---

## User Story

> **As a** student using the FinLit chatbot,
> **I want** the bot to remember our conversation even if the server restarts or I'm routed to a different server,
> **so that** I can ask follow-up questions like *"what about the fees you mentioned?"* without having to re-explain myself.

> **As the** team operating the bot,
> **I want** conversation history stored outside the application process with an automatic expiry,
> **so that** we can run more than one server for 1,000+ students and can state a defensible FERPA retention period.

---

## Background / Why now

Conversation history currently lives in a plain Python dictionary inside the running process (`app/services/chain.py:51`). This causes three problems:

1. **Restart = amnesia.** Every deploy or crash silently wipes all in-flight conversations. Students get no error — the bot just acts like a stranger.
2. **Cannot scale past one server.** With a load balancer, consecutive messages hit different instances. Each instance creates a fresh empty history, so memory flickers on and off at random. *This is the single hard blocker on serving 1,000 concurrent students.*
3. **Unbounded growth.** Nothing ever deletes from the dictionary. Every session ID ever seen stays in RAM forever — a slow memory leak ending in an OOM kill.

It also weakens an existing safety control: `sync_guarded_history()` overwrites the last AI turn with the *guarded* text so a follow-up can't extract blocked content. That correction only exists in one process's RAM.

---

## Acceptance Criteria

- [ ] **AC1** — Conversation history is read from and written to Redis, not process memory. The module-level `store` dict is removed entirely.
- [ ] **AC2** — A student's conversation survives an application restart. Sending a follow-up after a restart returns a contextually correct answer.
- [ ] **AC3** — Two application instances running against the same Redis share history. A conversation started on instance A continues correctly on instance B.
- [ ] **AC4** — Each session key carries a TTL (default **24 hours**), after which it is automatically deleted with no manual intervention.
- [ ] **AC5** — `sync_guarded_history()` genuinely persists the guarded text to Redis. Re-fetching the session afterward returns the guarded version, never the raw one.
- [ ] **AC6** — If Redis is unreachable, the bot still answers (stateless / no-memory mode) and logs the degradation. It must not return 500s to students.
- [ ] **AC7** — Redis connection string is supplied via a `REDIS_URL` environment variable — never hardcoded, never committed.
- [ ] **AC8** — Encryption at rest is confirmed enabled on the Redis instance.

---

## Subtasks

| # | Task | Owner | Est. |
|---|---|---|---|
| 1 | **Request Azure Cache for Redis provisioning** (Basic tier, ~$16/mo) from GVSU IT — *start immediately, long lead time* | Infra | — |
| 2 | Add `langchain-redis` to `pyproject.toml`; regenerate `requirements.txt` | Eng | 30m |
| 3 | Add `REDIS_URL` to env config, `.env.example`, and deployment settings | Eng | 30m |
| 4 | Replace `get_session_history()` with a Redis-backed implementation + TTL | Eng | 2h |
| 5 | **Rewrite `sync_guarded_history()` to write back to Redis** — see risk below | Eng | 2h |
| 6 | Add graceful-degradation path for Redis outage | Eng | 2h |
| 7 | Tests: persistence across restart, cross-instance sharing, guarded-history write-back, Redis-down fallback | Eng | 3h |
| 8 | Confirm encryption at rest; document the 24h TTL as the written retention policy | Eng | 1h |

---

## ⚠️ Key Risk — read before starting subtask 5

`sync_guarded_history()` currently mutates the message list in place:

```python
history.messages[-1] = AIMessage(content=guarded_text)
```

This works only because the list lives in local memory. **Against Redis this will silently do nothing** — it edits an already-fetched copy while Redis retains the *unguarded* reply. There is no error and no crash; the safety control simply stops working.

It must be rewritten to explicitly write back to Redis, and **AC5 exists specifically to catch this.** Do not mark this story done without that test passing.

---

## Out of Scope

- Authentication on the API (separate story — Production #2)
- Rate limiting (separate story — Production #3)
- Moving application logs off local disk (separate story — Production #4)
- Any change to guardrail logic itself

---

## Definition of Done

- All acceptance criteria verified
- Tests passing in CI
- Deployed and validated in staging with ≥2 instances running
- TTL value recorded in the team's data retention documentation
- `store` dict fully removed from the codebase

---

**Dependency flag for standup:** subtask 1 is external and unestimated. If GVSU IT provisioning stalls, the fallback is Azure Table Storage or Cosmos DB — roughly 3 days instead of 1.5. Raise it early rather than letting the story sit blocked.
