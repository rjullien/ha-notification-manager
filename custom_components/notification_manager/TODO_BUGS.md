# TODO: additional bugs spotted during voice review (Tesla session, 2026-10-07)

## ⚠️ Critical caveat: incomplete view of `__init__.py`

**The reviewing agent (Grok, via voice from a Tesla) did NOT have a complete
view of `__init__.py`.** The file is ~45 KB (~1,000 lines). The GitHub MCP
`get_file_contents` tool truncates at ~18.9 KB, and `browse_page` paraphrases
instead of transcribing (see `AGENT_README.md` for details on why).

Consequences for this to-do list:

- Every bug below was spotted from **partial, non-contiguous reads**.
  Treat each as a *hypothesis to verify*, not a confirmed defect.
- There is a real risk of **false positives** (the summarizer mangled code in
  ways that looked like bugs) and **false negatives** (bugs in the unread
  ~60% of the file that were never seen).
- Line numbers cited in earlier migration notes may not match the real file
  after the paraphrasing — always locate symbols by name via
  `github___search_code` rather than by assumed line.
- The tests (`tests/`) are the most reliable description of intended behavior;
  prefer reading them over guessing from truncated source.

**Recommended workflow for the next agent:**

1. Get a local checkout of the repo (the only way to see `__init__.py` whole).
2. Re-derive each item in this file and in the `*_MIGRATION.md` notes from the
   real source; discard anything that doesn't reproduce.
3. Only then implement fixes, with `pytest tests/ -v` as the regression net.
4. Split `__init__.py` into modules per `AGENT_README.md` *before* large
   behavioral changes — it makes every subsequent review tractable.

## Bugs spotted (to verify)

### 1. WhatsApp gated on `message_tel`

In `_async_handle_notify`, the WhatsApp branch is conditioned on
`message_tel and notification_whatsapp...`. Consequence: a WhatsApp-only
notification (no phone message) is silently skipped. WhatsApp should trigger
on `notification_whatsapp` independently of `message_tel`.

**Fix:** split the condition — `if notification_whatsapp.lower() not in ("aucun", "none")`
with its own message source (e.g. `message_tel` or a future `message_whatsapp`).

### 2. Inconsistent normalization of channel selectors

- `notification_tel.lower()` — no `strip()`
- `notification_alexa.strip().lower()` — strips first
- `notification_whatsapp` — check exact normalization used

A value like `" all "` may fail resolution on the tel path while working on
the alexa path. **Fix:** normalize all three with `.strip().lower()`.

### 3. `parse_mode` not forwarded to WhatsApp

`_async_handle_notify` passes `parse_mode` to `_async_send_phone` but the
WhatsApp send path (`_async_send_whatsapp` / `_async_send_whatsapp_to_jid`)
does not accept or apply it. GoWA supports formatting; either thread
`parse_mode` through or document that WhatsApp is plain-text only.

### 4. `photo_path` and `photo_url` both allowed with no precedence rule

The service schema accepts both simultaneously. Runtime behavior (path wins)
is implicit. **Fix:** either reject the combination in the schema, or make
precedence explicit in `_async_call_telegram` and document it.

## Still open from earlier notes

- JID conversion: `jid_utils.py` ready, not yet wired into `__init__.py`
- `ensure_future` → `hass.async_create_task` for phone/WhatsApp/Telegram
- Watchdog `@callback` on async checks
- `WATCHDOG_CRITICAL_INTERVAL_MINUTES` 10 → 5 (docstring says 5)
- Dead `elif` in coordinator `async_shutdown`
- Unguarded private-config import in `const.py`
- Service registration not deduplicated across multiple config entries
- Alexa EN path volume-lock audit
- `__init__.py` split into modules (blocking item — see `AGENT_README.md`)

## Notes for the implementing agent

- All migration notes on this branch follow the same template: problem,
  verbatim before/after snippet, and a suggested test.
- Run `pytest tests/ -v` after each change; the suite is the safety net
  while `__init__.py` is still monolithic.
- Do **not** attempt to retrieve `__init__.py` via `browse_page` — the
  summarizer paraphrases. Use `github___get_file_contents` (truncated but
  verbatim for the first ~18 KB) plus `github___search_code` fragments for
  the rest, and cross-check against the tests which encode the expected
  behavior precisely.
