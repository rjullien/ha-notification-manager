"""In-memory log of Alexa TTS emissions.

Purpose: let other components answer the question "did a speaker in this house
actually say something around time T?" without guessing. The notification
manager is the only component that knows which speakers were really used: it
resolves the keywords, drops unavailable players, and issues the TTS call.

Deliberate design choices:

- **No persistence.** The log lives in memory and is dropped on unload/restart.
  It answers real-time correlation questions only; a restart means there is no
  recent emission to correlate against, which is the correct answer anyway.
- **No Home Assistant import.** Keeps the module unit-testable on its own and
  usable from any context. Timestamps are timezone-aware UTC.
- **Recorded after the fact.** An entry is only appended once the TTS service
  call has been issued with a concrete, available target list — so a request
  that resolved to nothing, or to an unavailable speaker, is never recorded as
  sound.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

# A TTS request is not instantaneous: the speaker keeps talking for a few
# seconds. Callers pass the configured post-TTS delay as the estimate.
DEFAULT_SPEECH_ESTIMATE = timedelta(seconds=8)

# Messages are truncated before being stored: the log is a diagnostic aid, not
# a transcript.
MESSAGE_SNIPPET_LEN = 80


def utcnow() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Normalise a datetime to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.astimezone().astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class AlexaEmission:
    """One Alexa TTS emission that actually reached a speaker."""

    started_at: datetime
    ends_at: datetime
    targets: tuple[str, ...]
    local: bool
    kind: str
    context_id: str | None = None
    message: str = ""

    def overlaps(self, start: datetime, end: datetime) -> bool:
        """Whether the speech interval intersects the [start, end] window.

        Interval comparison (not a point-in-time check) matters for sensors
        that report an aggregate over a period: speech starting just before the
        window and still running inside it did contribute to the measurement.
        """
        return self.started_at <= end and self.ends_at >= start

    def as_dict(self) -> dict:
        """Serialise for a service response."""
        return {
            "started_at": self.started_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "targets": list(self.targets),
            "local": self.local,
            "kind": self.kind,
            "context_id": self.context_id,
            "message": self.message,
        }


class AlexaEmissionLog:
    """Bounded, non-persistent history of Alexa TTS emissions."""

    def __init__(
        self,
        max_entries: int = 50,
        retention: timedelta = timedelta(minutes=30),
    ) -> None:
        self._entries: deque[AlexaEmission] = deque(maxlen=max_entries)
        self._retention = retention

    def __len__(self) -> int:
        return len(self._entries)

    def record(
        self,
        targets: Sequence[str],
        kind: str,
        local_players: Iterable[str] | None = None,
        context_id: str | None = None,
        message: str = "",
        speech_estimate: timedelta = DEFAULT_SPEECH_ESTIMATE,
        started_at: datetime | None = None,
    ) -> AlexaEmission | None:
        """Append an emission. Returns None when there is no real target.

        Args:
            targets: Speakers the TTS was actually sent to (already filtered
                for availability by the caller).
            kind: Emission kind, e.g. ``tts_fr`` or ``tts_en``.
            local_players: Speakers physically in the house being monitored.
                An empty or missing list means "cannot distinguish" and every
                emission is flagged local — the conservative choice, since a
                consumer uses this flag to suppress false alarms.
            context_id: Home Assistant context id of the originating service
                call, letting a consumer recognise its own emissions.
            message: Spoken text; stored truncated.
            speech_estimate: Assumed speech duration.
            started_at: Emission start; defaults to now.
        """
        real_targets = tuple(t for t in targets if t)
        if not real_targets:
            return None

        started = _as_utc(started_at) if started_at else utcnow()
        known_local = {p for p in (local_players or ()) if p}
        emission = AlexaEmission(
            started_at=started,
            ends_at=started + speech_estimate,
            targets=real_targets,
            local=not known_local or bool(known_local.intersection(real_targets)),
            kind=kind,
            context_id=context_id,
            message=(message or "")[:MESSAGE_SNIPPET_LEN],
        )
        self._entries.append(emission)
        self._prune(started)
        return emission

    def between(
        self,
        start: datetime,
        end: datetime | None = None,
        local_only: bool = False,
        exclude_context_ids: Iterable[str] | None = None,
    ) -> list[AlexaEmission]:
        """Return emissions whose speech overlaps [start, end], newest first."""
        start = _as_utc(start)
        end = _as_utc(end) if end else utcnow()
        if end < start:
            start, end = end, start
        excluded = {c for c in (exclude_context_ids or ()) if c}

        return [
            emission
            for emission in reversed(self._entries)
            if emission.overlaps(start, end)
            and (emission.local or not local_only)
            and (emission.context_id not in excluded if emission.context_id else True)
        ]

    def recent(self, within: timedelta, local_only: bool = False) -> list[AlexaEmission]:
        """Return emissions overlapping the last ``within`` period."""
        now = utcnow()
        return self.between(now - within, now, local_only=local_only)

    def _prune(self, reference: datetime) -> None:
        """Drop entries older than the retention window."""
        cutoff = reference - self._retention
        while self._entries and self._entries[0].ends_at < cutoff:
            self._entries.popleft()
