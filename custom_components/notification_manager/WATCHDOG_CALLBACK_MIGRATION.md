# Migration: watchdog @callback on async check functions

## Problem

In `custom_components/notification_manager/watchdog.py`, both
`_async_check_standard` and `_async_check_critical` are decorated with
`@callback` while being `async def`. `@callback` tells Home Assistant the
function returns immediately and is synchronous; the coroutine object it
returns is never awaited by HA's scheduler, producing "coroutine was never
awaited" warnings and skipped executions on some HA versions.

## Fix

Remove the `@callback` decorator from both functions. `async_track_time_interval`
accepts a coroutine function directly and awaits it properly.

```python
# before
@callback
async def _async_check_standard(self, _now: datetime | None = None) -> None:

# after
async def _async_check_standard(self, _now: datetime | None = None) -> None:
```

Do the same for `_async_check_critical`. No other change needed: both still
call `await self._async_check_entities(...)` internally.

## Test

In `tests/`, add a test that patches `async_track_time_interval` to capture the
callback it receives, invokes it, and asserts the underlying check ran (e.g. by
patching `_async_check_entities` and checking it was awaited). This locks the
no-@callback contract.
