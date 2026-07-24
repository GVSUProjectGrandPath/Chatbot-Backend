# Fix: query-rewrite text leaking into the streamed answer

**Date:** 2026-07-23
**Endpoint affected:** `POST /chat/stream` (the live widget path)
**Files changed:** `app/services/chain.py`, `app/main.py`, `tests/test_chat_endpoint.py`

## Symptom

On the streaming endpoint, the bot's reply was prefixed with a reworded version of
the student's own question before the real answer began. Example:

> **Student typed:** "how about growing it?"
>
> **Widget showed:** "What can you do to help me grow my savings over time? I'm here
> to help you build on your amazing saving habits..."

That leading sentence is the *rewritten standalone question*, not part of the answer.

## Root cause

The chat pipeline calls the LLM **twice** per request:

1. `rewrite_query()` in `chain.py` — turns a follow-up like "how about growing it?"
   into a standalone question so retrieval works. Its output is only meant to feed
   Azure AI Search.
2. The final answer generation.

The streaming endpoint consumes events via `astream_events(version="v2")`, which
traces **every** nested model call. `main.py` grabbed all `on_chat_model_stream`
events indiscriminately, so the rewrite call's tokens got prepended to the streamed
answer.

The non-streaming `/chat` endpoint was never affected — there the rewrite output only
flows into retrieval and never reaches the response object.

## Fix

Tag the final-answer LLM call and stream only tokens carrying that tag.

- **`chain.py`** — the final model step is now
  `CHAT_LLM.with_config(tags=["final_response"])`. The rewrite call stays untagged.
- **`main.py`** — the stream loop computes `is_final = "final_response" in ev.get("tags", [])`
  and gates both `on_chat_model_stream` (token forwarding) and `on_chat_model_end`
  (token counting) on it. The rewrite call's events no longer match, so they are dropped.

Tags set with `.with_config()` attach only to that specific runnable, so this needs no
fragile run-id or model-name matching.

Side effect: the logged `total_tokens` now reflects only the answer generation, not the
answer + rewrite combined.

## Regression test

`tests/test_chat_endpoint.py::test_stream_does_not_leak_rewritten_query` replays the real
event ordering (an untagged rewrite call followed by the tagged answer call) through the
live `/chat/stream` endpoint and asserts the rewritten question never appears in the
streamed tokens. Verified it fails without the tag filter and passes with it.
