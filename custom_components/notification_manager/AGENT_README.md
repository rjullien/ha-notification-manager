# Note for the next agent: `__init__.py` is too large to work with via API

## The problem

`custom_components/notification_manager/__init__.py` is about 45 KB
(~1,000 lines) and contains nearly every function of the integration:

- service registration and handlers (`_async_handle_notify`, bridge log/restart
  services, recent-emissions service)
- Alexa FR flow (`_async_send_alexa`, volume save/set/restore, last-known-good)
- Alexa EN flow (`_async_send_alexa_en`, `_async_send_alexa_en_delayed`)
- phone / Telegram DM (`_async_send_phone`, `_async_send_phone_target`,
  `_async_call_telegram`)
- WhatsApp send path (`_async_send_whatsapp`, `_async_send_whatsapp_to_jid`,
  `_async_send_bridge_alert`)
- Telegram groups (`_async_send_telegram_group`)
- target resolvers (`_resolve_phone_targets`, `_resolve_alexa_targets`,
  `_resolve_whatsapp_targets`, `_resolve_restore_volume`, `_make_alexa_resolver`)
- runtime config (`_get_runtime_config`)

## Why it matters for agents

The GitHub MCP `get_file_contents` tool truncates responses around 18-19 KB.
Raw-file fetches via the web summarizer truncate the same way. Code-search
fragments only return small windows around each match. As a result:

- No single tool call can return the **whole** file.
- Reconstructing it from search fragments risks wrong line order and dropped lines.
- Any edit that requires the full file (e.g. `create_or_update_file`) cannot be
  done safely by an agent without a local checkout.

This is not a GitHub outage: it is a hard size limit of the retrieval path.

## What to do

**Split `__init__.py` into focused modules** under
`custom_components/notification_manager/`, for example:

| New module | Contents |
|---|---|
| `notify_handler.py` | `_async_handle_notify` and the service-call routing |
| `alexa_send.py` | `_async_send_alexa`, `_async_set_volume`,
  `_async_restore_volumes`, `_resolve_restore_volume`, `_volumes_close` |
| `alexa_en.py` | `_async_send_alexa_en`, `_async_send_alexa_en_delayed` |
| `phone_send.py` | `_async_send_phone`, `_async_send_phone_target` |
| `telegram_send.py` | `_async_call_telegram`, `_async_send_telegram_group` |
| `whatsapp_send.py` | `_async_send_whatsapp`, `_async_send_whatsapp_to_jid`,
  `_async_send_bridge_alert` |
| `resolvers.py` | `_resolve_phone_targets`, `_resolve_alexa_targets`,
  `_resolve_whatsapp_targets`, `_make_alexa_resolver` |
| `runtime_config.py` | `_get_runtime_config` |

Keep `__init__.py` as a thin barrel: imports, `async_setup_entry` /
`async_unload_entry`, and re-exports. Target size: well under the 18 KB
retrieval limit per file.

## Integration order

1. Create the new modules and move functions verbatim (no behavior change).
2. Update all internal imports and the test suite (`sys.path` / patch targets).
3. Run `pytest tests/ -v` — the existing 148 tests should pass untouched.
4. Only then apply behavioral fixes (see the other `*_MIGRATION.md` notes on
   this branch: JID conversion, ensure_future, watchdog @callback, service dedup,
   private-import guard, EN lock audit).
5. Delete the migration notes once each fix is applied.

## Also on this branch

- `jid_utils.py` + `tests/test_jid_utils.py` — fixed JID-to-phone conversion
  (ready to import from `whatsapp_send.py`).
- `alexa_players_validation.py` + `tests/test_alexa_players_validation.py` —
  ready to call from `config_flow.py`.
- `TEST_PUSH.md` — leftover from the access test; delete it.
