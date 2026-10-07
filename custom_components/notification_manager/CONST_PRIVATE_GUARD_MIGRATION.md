# Migration: guard private-config import in const.py

## Problem

At module level, `const.py` does:

```python
_spec = _ilu.spec_from_file_location("_nm_private", _PRIVATE_PATH)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
```

If `/config/notification_manager_private.py` exists but has a syntax error or
raises on import, the exception propagates out of `const.py` and prevents the
entire integration from loading — including the watchdog and bridge health
sensor that do not depend on private config at all.

## Fix

Wrap the private import in try/except:

```python
if _os.path.isfile(_PRIVATE_PATH):
    try:
        _spec = _ilu.spec_from_file_location("_nm_private", _PRIVATE_PATH)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        for _name in dir(_mod):
            if _name.isupper():
                globals()[_name] = getattr(_mod, _mod)
    except Exception as exc:  # noqa: BLE001
        # Private config is optional; a broken file must not brick the component.
        import logging as _logging
        _logging.getLogger(__name__).error(
            "Failed to load private config %s: %s", _PRIVATE_PATH, exc
        )
else:
    try:
        from . import const_private as _mod_legacy
        for _name in dir(_mod_legacy):
            if _name.isupper():
                globals()[_name] = getattr(_mod_legacy, _mod_legacy)
    except ImportError:
        pass
```

## Test

Add a test that creates a temp file with invalid syntax at the private path,
reloads `const`, and asserts the module still imports with defaults intact and
that an error was logged.
