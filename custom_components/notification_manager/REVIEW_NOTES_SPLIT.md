# Review notes — `cursor/notify-module-split-bf9d`

> **Author:** This in-depth analysis was performed by **Grok Tesla** (Grok, voice session from a Tesla vehicle), on behalf of rjullien.
> Date: 2026-10-07.

Voice review of the coarser `__init__.py` split (PR #16, stacked on PR #15).
Reviewed against `TODO_BUGS.md` from `test-pr-access` (same-day Tesla session).
Second pass: cross-module review of config_flow, sensor, services.yaml, manifest,
strings, and tests/test_notify_handler.py.

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

---

## Deep review — pass 1 (notify / alexa / messaging / bridge_services / jid_utils)

### 5. English Alexa path ignores `notification_alexa` selector — OPEN

In `notify.py`, the FR Alexa gate checks `alexa_selector not in ("aucun", "none", "off", "disable")`,
but the EN branch only tests `if message_alexa_en:`. A call with `message_alexa_en` set and
`notification_alexa` = "none" still schedules the EN TTS. Fix: apply the same selector
gate (or document that EN is independent of the FR selector and always fires).

### 6. Bridge alert Telegram omits `parse_mode` — OPEN

`_async_send_bridge_alert` in `messaging.py` calls `telegram_bot.send_message` without
`parse_mode`. HA's telegram_bot defaults to markdown, so a message containing an
unbalanced `_` or `*` (file names, entity_ids, snake_case) is rejected by Telegram
with "Can't parse entities" and the alert is silently lost — the exact failure mode
`telegram_text.py` documents for other paths. Fix: pass `parse_mode=PARSE_MODE_PLAIN`
(or HTML + `escape_html`).

### 7. Unguarded `split(".", 1)` on mobile service — OPEN (minor)

`_async_send_phone_target` does `domain, service = mobile_service.split(".", 1)` with
no guard. A malformed `phone_targets` entry like `"notify"` (no dot) raises
`ValueError`, caught by the broad `except` and logged, but the misconfiguration is
only visible at send time. Validate the `mobile` field shape in the reconfigure
flow (or at least log a clear config-shape error at load).

### 8. Alexa resolver overwritten per entry — OPEN

`async_setup_entry` always does
`hass.data[DOMAIN][DATA_ALEXA_RESOLVER] = _make_alexa_resolver(hass, entry)`,
clobbering any previous entry's resolver. With two config entries the resolver
points at whichever loaded last. Store resolvers per `entry_id` (e.g. under
`entry_data`) or document single-entry assumption.

---

## Deep review — pass 2 (config_flow / sensor / services.yaml / manifest / strings / tests)

### 9. Config flow does not validate `phone_targets` JSON structure — OPEN

`_parse_json` only checks JSON syntax. A `phone_targets` object whose values lack
the required `"mobile"` key (e.g. `{"alice": {}}`) is accepted and saved. The
failure then surfaces later as `KeyError` inside `_async_send_phone_target` at
send time. Validate that each value is a dict with a string `mobile` field
(and optionally an int-or-null `telegram_chat_id`) before saving.

### 10. Reconfigure drops keys not carried forward — OPEN

`async_step_reconfigure_bridge` builds `_reconfigure_data` from the three bridge
fields, then copies `entry.data` keys absent from that dict. That preserves
legacy keys, but any key that *is* in `_reconfigure_data` (the bridge trio) is
fine; the real risk is keys introduced by a future step that a later step forgets
to re-copy. Today the five steps do carry everything through, but there is no
guard: a missed `self._reconfigure_data[k] = v` in any step silently drops that
setting on save. Consider starting from `dict(entry.data)` and overwriting, or
adding an assertion that the final dict contains every key from the previous
entry.

### 11. EN Alexa selector gate missing in tests — OPEN

`tests/test_notify_handler.py` covers FR selector disabling (`test_none_values_disable_channels`
sets `notification_alexa="aucun"` and asserts FR not called) but has no case where
`message_alexa_en` is set with `notification_alexa` disabled. Add one to lock the
decision from item 5 (gate or document-and-test independence).

### 12. services.yaml documents EN Alexa as selector-independent — OPEN (docs)

The `message_alexa_en` field description says the message goes to `alexa_en_target`
"avec un délai de 3 secondes" and never mentions `notification_alexa`. If item 5 is
fixed by gating EN on the selector, update this description; if EN is intentionally
independent, state it explicitly so the UI doesn't surprise users.

### 13. Watchdog entities only from const, not from entry data — OPEN (design)

`EntityWatchdog` reads `WATCHDOG_ENTITIES` / `WATCHDOG_CRITICAL_ENTITIES` directly
from `const` (populated by the private-config import). Nothing in the config flow
or entry data can override them, unlike phone/WhatsApp/Alexa maps. If the intent
is that watchdog targets are site-private, fine — but then document it; otherwise
wire them through `entry.data` like the other maps.

### 14. services.yaml omits `telegram_groups` from notify field docs — OPEN (docs, minor)

The `telegram_group` field says "Nom du groupe Telegram cible (défini dans la
configuration)" without pointing at the reconfigure step or the
`TELEGRAM_GROUPS` const. Cross-link it.

## Suggested next steps for Cursor

1. Fix the five still-open items from TODO_BUGS.md (watchdog `@callback`, critical
   interval mismatch, dead `elif`, unguarded private import, service dedup).
2. Fix the new findings above: EN Alexa selector gate (5), bridge alert
   `parse_mode` (6), mobile service split guard (7), resolver per-entry (8),
   phone_targets structure validation (9), reconfigure key-loss guard (10).
3. Add the missing EN-selector test (11) and align services.yaml docs (12, 14).
4. Decide watchdog entity sourcing (13) and document.
5. Consider the `runtime.py` extraction to kill the circular import for good.
6. Run `pytest tests/ -v` after each change.
7. EN Alexa volume-lock audit: bring `_async_send_alexa_en` under the same lock
   or document FR/EN interleaving.
