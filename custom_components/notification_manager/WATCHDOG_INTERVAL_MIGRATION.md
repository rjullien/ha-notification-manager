# Migration: WATCHDOG_CRITICAL_INTERVAL_MINUTES 10 → 5

## Problem

`const.py` sets `WATCHDOG_CRITICAL_INTERVAL_MINUTES = 10`, but the module
docstring of `watchdog.py` states the default is 5 minutes. The docstring is
the user-facing contract; the constant silently diverges from it.

## Fix

In `custom_components/notification_manager/const.py`, change:

```python
WATCHDOG_CRITICAL_INTERVAL_MINUTES = 10
```

to:

```python
WATCHDOG_CRITICAL_INTERVAL_MINUTES = 5
```

## Note

This is a behavior change for anyone relying on the 10-minute cadence: critical
entities (irrigation valves etc.) will now be checked twice as often. If that
load is a concern, override the constant in `notification_manager_private.py`
instead of editing `const.py`.
