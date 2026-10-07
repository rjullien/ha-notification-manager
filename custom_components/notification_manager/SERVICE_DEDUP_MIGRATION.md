# Migration: guard service registration against duplicate entries

## Problem

In `async_setup_entry`, `hass.services.async_register(DOMAIN, SERVICE_NOTIFY, ...)`
(and the three diagnostic services) is called unconditionally for every config
entry. Home Assistant raises `ServiceAlreadyRegistered` on the second entry, so
a multi-entry setup fails to load the second one entirely.

## Fix

Before each registration, check existence:

```python
if not hass.services.has_service(DOMAIN, SERVICE_NOTIFY):
    hass.services.async_register(
        DOMAIN, SERVICE_NOTIFY, handle_notify, schema=SERVICE_NOTIFY_SCHEMA,
    )
```

Apply to `SERVICE_NOTIFY`, `SERVICE_RECENT_ALEXA_EMISSIONS`,
`whatsapp_bridge_logs` and `whatsapp_bridge_restart`.

## Semantic note

With this guard, the *first* loaded entry owns the service handlers (they close
over that entry's bridge URL/token). If two entries point at different bridges,
only the first is reachable via the service. A future improvement would key
the handler off `call.context.entry_id` or resolve the entry dynamically.
Document this limitation in the README.

## Test

Add a test with two config entries that asserts both load successfully and that
`has_service` is True exactly once per service name.
