# Review notes — `cursor/notify-module-split-bf9d`

Voice review of the coarser `__init__.py` split (PR #16, stacked on PR #15).
Reviewed against `TODO_BUGS.md` from `test-pr-access` (same-day Tesla session).

## Bugs from TODO_BUGS.md — status on this branch

### 1. WhatsApp gated on `message_tel` — FIXED

`notify.py` `_async_handle_notify` now gates WhatsApp on `wa_selector` alone:

```python
if wa_selector not in ("none", "aucun", ""):
    ...
    tasks.append(
        hass.async_create_task(
            messaging_mod._async_send_whatsapp(
                hass, entry, message_tel, notification_whatsapp, ...
            )
        )
    )
```

A WhatsApp-only notify (empty `message_tel`) is no longer silently skipped.
Empty `message_tel` is forwarded as an empty string to the bridge (documented in-code).

### 2. Inconsistent normalization of channel selectors — FIXED

All three selectors are now normalized with `.strip().lower()` before gating:

```python
tel_selector = notification_tel.strip().lower()
wa_selector = notification_whatsapp.strip().lower()
alexa_selector = notification_alexa.strip().lower()
```

### 3. `parse_mode` not forwarded to WhatsApp — STILL OPEN

`_async_send_whatsapp` / `_async_send_whatsapp_to_jid` still do not accept or apply
`parse_mode`. GoWA receives raw text only. Either thread `parse_mode` through or
document that WhatsApp is plain-text only (README / services.yaml).

### 4. `photo_path` and `photo_url` both allowed — STILL OPEN (minor)

The service schema still accepts both simultaneously. Runtime precedence (path wins)
is implicit in `_async_call_telegram` (`if photo_path: ... elif photo_url: ...`).
Either reject the combination in the schema or make precedence explicit and documented.

## Still open from earlier notes

- Watchdog: `@callback` decorator on `_async_check_standard` / `_async_check_critical`
  (async functions should not be `@callback`).
- `WATCHDOG_CRITICAL_INTERVAL_MINUTES` is 10 in `const.py` while the module
  docstring says 5 minutes.
- Dead `elif` in `coordinator.py` `async_shutdown` (the `elif self._unsub_refresh`
  branch is unreachable given the `if hasattr(super(), "async_shutdown")` guard).
- Unguarded private-config import in `const.py` (the `except ImportError` only
  covers the legacy `.const_private` fallback; a broken
  `/config/notification_manager_private.py` will raise at import time and prevent
  the whole integration from loading).
- Service registration not deduplicated across multiple config entries: each entry
  calls `async_register_bridge_services`, which re-registers
  `whatsapp_bridge_logs`, `whatsapp_bridge_restart`, and `recent_alexa_emissions`.
  With two entries this raises or silently no-ops depending on HA version.
- Alexa EN path volume-lock audit: `_async_send_alexa_en` does not participate in
  the volume save/restore lock used by `_async_send_alexa`. An overlapping FR+EN
  cycle can clobber volumes.

## New observations specific to this split

### Circular import pattern (intentional, fragile)

`notify.py` defines helpers first, then does:

```python
from . import alexa as alexa_mod  # noqa: E402
from . import messaging as messaging_mod  # noqa: E402
```

at module bottom so `alexa` / `messaging` can `from .notify import _get_runtime_config`
without a circular load at import time. This works but is load-order sensitive:

- Any future module that imports from `notify` at **module top level** and is itself
  imported by `notify` (directly or transitively) will break.
- `bridge_services.py` imports from `alexa` and `notify` at top level — safe today
  only because `notify`'s bottom-of-file imports happen after `notify`'s body is
  fully executed... actually no: `notify` imports `alexa` at the bottom, `alexa`
  imports `notify._get_runtime_config` at top level. This is the classic
  "partially initialized module" hazard. It currently works because
  `_get_runtime_config` is defined before the bottom-of-file import runs, but any
  reordering (e.g. moving the `alexa_mod` import earlier, or adding a top-level
  name access in `alexa` that runs during `alexa`'s import) will produce
  `ImportError: cannot import name '_get_runtime_config' from partially
  initialized module`.

**Suggestion:** extract `_get_runtime_config` (and `_entry_verify_ssl`,
`_run_logged`) into a tiny `runtime.py` module with zero internal imports, imported
by everyone. That removes the cycle entirely instead of papering over it.

### `ensure_future` → `hass.async_create_task` — FIXED

Phone / WhatsApp / Telegram group tasks now use `hass.async_create_task`, same as
Alexa, so HA tracks and cancels them on unload. Good.

### Tests patch where used — consistent

The PR body documents patching `messaging._async_send_*` / `alexa._async_send_*`,
and the moved code in `notify.py` calls through the module objects
(`alexa_mod._async_send_alexa(...)`, `messaging_mod._async_send_phone(...)`)
rather than direct names. This preserves patchability. Verified in
`tests/test_notify_handler.py` (13017 bytes, updated on this branch).

### `__all__` re-exports in `__init__.py`

The split `__init__.py` exposes a large `__all__` of private symbols for backward
compatibility with tests/consumers that historically imported from
`notification_manager.__init__`. Sensible, but it means the "public surface" of
the package is still the old private API. Fine as a transition; consider trimming
in a follow-up once consumers are migrated.

## Suggested next steps for Cursor

1. Fix the five still-open items above (watchdog `@callback`, critical interval
   mismatch, dead `elif`, unguarded private import, service dedup) — each is small
   and independently testable.
2. Consider the `runtime.py` extraction to kill the circular import for good.
3. Decide on WhatsApp `parse_mode` and photo precedence; document or enforce.
4. Run `pytest tests/ -v` (160 passed on this branch per PR body) after each
   change.
5. The EN Alexa volume-lock audit needs a decision: either bring
   `_async_send_alexa_en` under the same lock (hard, because it has its own
   start delay) or document that FR and EN cycles are independent and may
   interleave volume operations.
