# Migration: dead elif in NotificationManagerCoordinator.async_shutdown

## Problem

In `coordinator.py`:

```python
async def async_shutdown(self) -> None:
    if hasattr(super(), "async_shutdown"):
        await super().async_shutdown()
    elif self._unsub_refresh:  # pragma: no cover
        self._unsub_refresh()
        self._unsub_refresh = None
```

`DataUpdateCoordinator` has always defined `async_shutdown`, so `hasattr(super(),
"async_shutdown")` is unconditionally True and the `elif` is unreachable dead
code. It also references `self._unsub_refresh`, an internal HA attribute that may
not exist on every version — if it ever did run, it would raise AttributeError.

## Fix

Replace the method with:

```python
async def async_shutdown(self) -> None:
    """Cancel polling on unload."""
    await super().async_shutdown()
```

## Test

`tests/test_coordinator.py` already covers shutdown; just confirm it still
passes after the simplification.
