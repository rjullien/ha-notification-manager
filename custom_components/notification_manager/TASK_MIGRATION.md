# Migration: ensure_future → async_create_task

## Problem

In `_async_handle_notify`, phone, WhatsApp and Telegram group sends are launched with
`asyncio.ensure_future(...)`. Alexa FR and EN use `hass.async_create_task(...)`.

`ensure_future` bypasses Home Assistant's task registry: tasks are not cancelled on
integration unload/shutdown, and failures are not surfaced through HA's task error
handling. A WhatsApp retry loop (up to BRIDGE_RETRIES with backoff) can keep running
after the config entry is removed.

## Fix

In `custom_components/notification_manager/__init__.py`, inside `_async_handle_notify`,
replace every:

```python
tasks.append(
    asyncio.ensure_future(
        _async_send_phone(...)
    )
)
```

with:

```python
tasks.append(
    hass.async_create_task(
        _async_send_phone(...)
    )
)
```

Same for `_async_send_whatsapp` and `_async_send_telegram_group`.

The subsequent `await asyncio.gather(*tasks)` stays unchanged — it still gives
blocking-mode automations delivery feedback.

## Test

Extend `tests/test_notify_handler.py` with a test that mocks `hass.async_create_task`
as a tracking function, calls `_async_handle_notify` with a phone/WhatsApp/Telegram
payload, and asserts `async_create_task` was called (not `ensure_future`). Optionally
assert the created tasks are the same objects later gathered.
