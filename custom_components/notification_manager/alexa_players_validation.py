"""Validation helpers for Alexa player configuration.

Used by the reconfigure flow to reject malformed alexa_players JSON before it is
saved to the config entry, where it would only crash at runtime during target
resolution.
"""

from __future__ import annotations

from typing import Any


def validate_alexa_players(value: Any) -> list[str] | None:
    """Return the player list if valid, None otherwise.

    Rules:
    - must be a list
    - every element must be a non-empty string
    - duplicates are dropped (order preserved)
    """
    if not isinstance(value, list):
        return None
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        eid = item.strip()
        if eid not in seen:
            seen.add(eid)
            cleaned.append(eid)
    return cleaned
