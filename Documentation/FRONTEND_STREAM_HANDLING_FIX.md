# Frontend Fix: Handle Stream Event Types in `chatbot_script.txt`

**Status:** open — for the frontend team
**Date:** 2026-07-23
**Endpoint:** `POST /chat/stream` (the live widget path)

---

## TL;DR

The widget reads the streamed response but **ignores the `type` field on each event**. It just appends every event's `content` to the chat bubble. Because of this, the **`replace` event (used to remove blocked/off-limits answers) does nothing** — the blocked text stays on screen. This needs a small change: switch on `event.type` instead of blindly appending.

---

## Background: what the backend sends

`/chat/stream` returns newline-delimited JSON (ndjson). Each line is one event with a `type`:

| `type` | When | What the frontend should do |
|---|---|---|
| `token` | For every chunk of the answer as it generates | **Append** `content` to the bubble (stream it in) |
| `replace` | Compliance guardrail blocked the answer after it streamed | **Replace** the whole bubble with `content` |
| `error` | Something failed mid-stream | Show `content` as an error (don't treat it as normal answer text) |
| `done` | End of stream, on every path | Finalize the UI (stop spinner, unlock input). Carries `ferpa_blocked` |

Full spec: `STREAMING_API_CONTRACT.md`.

---

## The problem

Current handler (in `chatbot_script.txt`, around line 1818):

```js
const parsed = JSON.parse(trimmed);
if (parsed.content !== undefined) {
    chunkedResponse += parsed.content;   // appends the content of EVERY event
}
```

It never checks `parsed.type`, so:

| Event | Intended behavior | What actually happens now | Impact |
|---|---|---|---|
| `token` | append | appends ✅ | correct |
| `replace` | overwrite the bubble | **appends** ❌ | **Blocked answer stays visible; the compliance message just gets tacked on after it. The guardrail is defeated on screen.** |
| `error` | show an error | appends the error string as if it were answer text | confusing UX |
| `done` | finalize | ignored (it has no `content` field) | works only because the code also stops on stream EOF |

**The one that matters most is `replace`.** The backend correctly blocks non-compliant / personalized-advice answers and sends a `replace` to swap them out, but the widget appends instead of replacing — so the student still sees the answer the guardrail was meant to withhold.

---

## The fix

Replace the `if (parsed.content !== undefined)` block with a `switch` on `parsed.type`:

```js
const parsed = JSON.parse(trimmed);

switch (parsed.type) {
    case "token":
        chunkedResponse += parsed.content;   // stream tokens in
        break;

    case "replace":
        chunkedResponse = parsed.content;    // OVERWRITE — do not append
        break;

    case "error":
        chunkedResponse = parsed.content;    // or render a dedicated error state
        break;

    case "done":
        // stream finished. parsed.ferpa_blocked is available here if needed.
        break;
}
```

Notes:
- `chunkedResponse` is progressively revealed elsewhere, so setting it (for `replace`) will cleanly re-render the bubble via the existing `marked.parse(chunkedResponse)` call.
- `done` now fires on **every** exit path (including errors), so it's safe to finalize on `done` rather than relying only on stream EOF.

---

## Priority

- **`replace` handling — high.** This is a compliance/guardrail issue: blocked answers currently remain visible.
- **`error` / `done` handling — nice to have.** Improves UX and robustness; the widget currently limps along on stream EOF.

## Backend status

No backend change needed for this. The server already blocks correctly and also corrects its own session history (`sync_guarded_history`), so the cross-turn leak is closed server-side regardless of the frontend. This fix is purely about what the student *sees* on screen.
