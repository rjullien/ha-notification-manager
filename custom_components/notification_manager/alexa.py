"""Alexa TTS (FR + EN), volume stack, emission log helpers and resolvers."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Sequence

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .alexa_emissions import AlexaEmissionLog
from .const import (
    ALEXA_DEFAULT_KEYWORD,
    ALEXA_DEFAULT_VOLUME,
    ALEXA_EN_DELAY,
    ALEXA_KEYWORD_ALIASES,
    ALEXA_KEYWORD_EXCLUDES,
    DOMAIN,
)
from .notify import _get_runtime_config

_LOGGER = logging.getLogger(__name__)

# Internal hass.data keys MUST start with "_": async_unload_entry counts every
# other key as a config entry when deciding whether to remove the services.
DATA_ALEXA_LOCK = "_alexa_lock"
DATA_ALEXA_EMISSIONS = "_alexa_emissions"
# Lets a consumer ask "would this keyword actually reach a speaker?" instead of
# duplicating the resolution rules and drifting from them.
DATA_ALEXA_RESOLVER = "_alexa_resolver"
# Last resting volume per media_player, kept across TTS cycles. Alexa Media
# often leaves ``volume_level`` stuck at the TTS level after a restore
# ``volume_set`` returns, so the next cycle must not trust that attribute alone.
DATA_ALEXA_LAST_GOOD_VOLUMES = "_alexa_last_good_volumes"

# Tolerate Alexa/HA float noise when comparing volume_level to the TTS level.
_VOLUME_EPS = 0.01

# Bounds for the recent_alexa_emissions service window.
MAX_EMISSION_QUERY_SECONDS = 3600
DEFAULT_EMISSION_QUERY_SECONDS = 120

def _volumes_close(a: float, b: float) -> bool:
    """Return True when two volume levels are effectively the same."""
    return abs(float(a) - float(b)) < _VOLUME_EPS


def _parse_volume_attr(raw) -> float | None:
    """Parse a media_player ``volume_level`` attribute, or None if unusable."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _resolve_restore_volume(
    entity_id: str,
    reported: float | None,
    tts_volume: float,
    last_good: dict[str, float],
) -> float:
    """Pick the resting volume to restore after TTS.

    The lock already stops overlapping cycles from interleaving, but it does
    not help when Alexa Media lags: after restore, ``volume_level`` may still
    equal the TTS level. The next serialised cycle would then save TTS as the
    "original" and restore to TTS — leaving the speaker stuck loud.

    Prefer a remembered last-known-good volume when the live attribute looks
    like the TTS level. Otherwise trust the live reading and remember it.
    """
    remembered = last_good.get(entity_id)

    if reported is None:
        if remembered is not None:
            return remembered
        return ALEXA_DEFAULT_VOLUME

    if _volumes_close(reported, tts_volume):
        if remembered is not None and not _volumes_close(remembered, tts_volume):
            _LOGGER.debug(
                "Alexa player %s still reports TTS volume %.2f; "
                "reusing last-known-good %.2f",
                entity_id,
                reported,
                remembered,
            )
            return remembered
        # First cycle (or user resting volume really is TTS): keep reported.
        last_good[entity_id] = reported
        return reported

    last_good[entity_id] = reported
    return reported


async def _async_set_volume(hass: HomeAssistant, entity_id: str, volume: float) -> bool:
    """Set a media_player volume (blocking). Return True on success."""
    try:
        await hass.services.async_call(
            "media_player",
            "volume_set",
            {"entity_id": entity_id, "volume_level": volume},
            blocking=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Failed to set volume for %s: %s", entity_id, exc)
        return False


async def _async_restore_volumes(
    hass: HomeAssistant,
    original_volumes: dict[str, float],
) -> None:
    """Restore resting volumes, retrying once per entity on failure."""
    for entity_id, vol_level in original_volumes.items():
        ok = await _async_set_volume(hass, entity_id, vol_level)
        if not ok:
            _LOGGER.warning(
                "Retrying volume restore for %s to %.2f", entity_id, vol_level
            )
            await _async_set_volume(hass, entity_id, vol_level)


def _make_alexa_resolver(hass: HomeAssistant, entry: ConfigEntry):
    """Build the resolver exposed to other components.

    Answers which speakers a ``notification_alexa`` value really reaches, using
    the same rules as an actual TTS call. A consumer can therefore detect a
    keyword that resolves to nothing — a silent "nobody hears it" failure —
    without reimplementing keyword matching.
    """

    def resolve(notification_alexa: str, available_only: bool = True) -> list[str]:
        cfg = _get_runtime_config(entry)
        targets = _resolve_alexa_targets(notification_alexa, cfg["alexa_players"])
        if not available_only:
            return targets
        return [
            target
            for target in targets
            if (state := hass.states.get(target)) is not None
            and state.state != "unavailable"
        ]

    return resolve


def _get_emission_log(hass: HomeAssistant) -> AlexaEmissionLog | None:
    """Return the in-memory Alexa emission log, if the integration is loaded."""
    log = hass.data.get(DOMAIN, {}).get(DATA_ALEXA_EMISSIONS)
    return log if isinstance(log, AlexaEmissionLog) else None


def _record_alexa_emission(
    hass: HomeAssistant,
    targets: Sequence[str],
    kind: str,
    cfg: dict,
    message: str,
    context_id: str | None,
) -> None:
    """Record a TTS emission that was actually issued to real speakers.

    Never raises: the log is an observability aid and must not be able to break
    a notification.
    """
    log = _get_emission_log(hass)
    if log is None:
        return
    try:
        log.record(
            targets=targets,
            kind=kind,
            local_players=cfg.get("alexa_local_players") or (),
            context_id=context_id,
            message=message,
            speech_estimate=timedelta(seconds=float(cfg.get("alexa_post_tts_delay") or 0)),
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Failed to record Alexa emission: %s", exc)


async def _async_send_alexa(
    hass: HomeAssistant,
    entry: ConfigEntry,
    message: str,
    notification_alexa: str,
    context_id: str | None = None,
) -> None:
    """Send Alexa TTS with volume save/restore.

    The whole save→set→TTS→restore cycle is serialised behind a lock so that
    overlapping notify calls cannot interleave. Across consecutive cycles we
    also remember each player's last known good (resting) volume: Alexa Media
    can leave ``volume_level`` stuck at the TTS level after restore returns,
    and the next cycle must not treat that stale attribute as the original.
    """
    # Skip if alexa_media integration is not available on this instance
    if not hass.services.has_service("notify", "alexa_media"):
        _LOGGER.debug("Alexa TTS skipped: notify.alexa_media service not available")
        return

    cfg = _get_runtime_config(entry)
    targets = _resolve_alexa_targets(notification_alexa, cfg["alexa_players"])
    if not targets:
        _LOGGER.warning(
            "No Alexa targets resolved for notification_alexa=%r — TTS skipped. "
            "Keywords must match entity_ids (compound keywords like rene_show "
            "require each part, e.g. rene + show, to appear in the entity_id).",
            notification_alexa,
        )
        return

    # Filter out unavailable players (e.g. stale _2 duplicates from re-added integrations)
    available_targets = [
        t for t in targets
        if (state := hass.states.get(t)) is not None and state.state != "unavailable"
    ]
    if not available_targets:
        _LOGGER.warning(
            "All resolved Alexa targets are unavailable: %s (skipping TTS)", targets
        )
        return
    targets = available_targets

    _LOGGER.debug("Alexa targets: %s", targets)

    domain_data = hass.data.setdefault(DOMAIN, {})
    lock: asyncio.Lock = domain_data.setdefault(DATA_ALEXA_LOCK, asyncio.Lock())
    last_good: dict[str, float] = domain_data.setdefault(
        DATA_ALEXA_LAST_GOOD_VOLUMES, {}
    )

    async with lock:
        alexa_tts_volume = cfg["alexa_tts_volume"]

        # 1. Resolve resting volumes (live attribute + last-known-good memory)
        original_volumes: dict[str, float] = {}
        for entity_id in targets:
            state = hass.states.get(entity_id)
            if state is None or state.state in ("unavailable", "unknown"):
                _LOGGER.debug(
                    "Alexa player %s unavailable, using default volume", entity_id
                )
                reported = None
            else:
                reported = _parse_volume_attr(state.attributes.get("volume_level"))
                if reported is None and state.attributes.get("volume_level") is not None:
                    _LOGGER.debug(
                        "Alexa player %s has invalid volume_level %r",
                        entity_id,
                        state.attributes.get("volume_level"),
                    )
            restore_vol = _resolve_restore_volume(
                entity_id, reported, alexa_tts_volume, last_good
            )
            original_volumes[entity_id] = restore_vol
            # Always remember the intended resting volume for the next cycle,
            # even if a later restore ``volume_set`` fails or HA state lags.
            last_good[entity_id] = restore_vol

        # 2. Set volume to TTS level — blocking + awaited BEFORE the TTS is
        #    sent, so speech can never start at the old volume.
        await asyncio.gather(
            *(_async_set_volume(hass, eid, alexa_tts_volume) for eid in targets)
        )

        # 3. Send TTS
        try:
            await hass.services.async_call(
                "notify",
                "alexa_media",
                {"message": message, "target": targets, "data": {"type": "tts"}},
                blocking=True,
            )
            _LOGGER.debug("Alexa TTS sent to %s", targets)
            # Recorded only here: `targets` is resolved and availability
            # filtered, so this is proof that speakers really spoke.
            _record_alexa_emission(
                hass, targets, "tts_fr", cfg, message, context_id
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to send Alexa TTS: %s", exc)

        # 4. Wait for speech to finish
        await asyncio.sleep(cfg["alexa_post_tts_delay"])

        # 5. Restore resting volumes (retry once per entity on failure)
        await _async_restore_volumes(hass, original_volumes)


def _keyword_matches_alexa_player(keyword: str, player: str) -> bool:
    """Match a keyword against a player entity_id.

    Simple keywords use substring match (legacy mamagetts behaviour).
    Compound keywords with spaces or underscores (e.g. ``rene_show``) require
    every part to appear in the entity_id — so ``rene_show`` matches
    ``your_kitchen_rene_echo_show`` but not ``your_kitchen_rene_echo_spot``.
    """
    parts = [p for p in keyword.replace("_", " ").split() if p]
    if len(parts) > 1:
        return all(part in player for part in parts)
    return keyword in player


def _excluded_players_for_keyword(keyword: str) -> set[str]:
    """Return lowercased entity_ids excluded for ``keyword`` (post-alias)."""
    return {e.lower() for e in ALEXA_KEYWORD_EXCLUDES.get(keyword, ()) if e}


def _resolve_alexa_targets(notification_alexa: str, alexa_players: list) -> list[str]:
    """Resolve notification_alexa string to list of entity_ids.

    After substring/compound matching, drops any player listed under
    ``ALEXA_KEYWORD_EXCLUDES`` for that keyword. Lookup uses the post-alias
    keyword (after ``ALEXA_KEYWORD_ALIASES``), so an exclude for callers of
    ``show_2`` must be keyed as ``rene_show``. Excludes are per-keyword, so
    a device can be omitted from broad ``show`` / ``rene_show`` while still
    matching a more specific compound like ``show_11``.
    """
    value = notification_alexa.strip().lower()
    if not value:
        # Default: "show" keyword (same matcher + excludes as explicit "show")
        keyword = ALEXA_DEFAULT_KEYWORD
        excluded = _excluded_players_for_keyword(keyword)
        return [
            p
            for p in alexa_players
            if _keyword_matches_alexa_player(keyword, p) and p.lower() not in excluded
        ]

    # Special values
    if value in ("aucun", "none", "off", "disable"):
        return []

    keywords = [
        ALEXA_KEYWORD_ALIASES.get(k.strip(), k.strip())
        for k in value.split()
        if k.strip()
    ]
    matched: list[str] = []
    for keyword in keywords:
        excluded = _excluded_players_for_keyword(keyword)
        for player in alexa_players:
            if (
                _keyword_matches_alexa_player(keyword, player)
                and player not in matched
                and player.lower() not in excluded
            ):
                matched.append(player)
    return matched


async def _async_send_alexa_en_delayed(
    hass: HomeAssistant,
    entry: ConfigEntry,
    message: str,
    context_id: str | None = None,
) -> None:
    """Send the English Alexa TTS after its fixed delay.

    Runs as an independent task so the delay starts immediately and is not
    pushed back by slow channels (e.g. WhatsApp retries when the bridge is
    down used to delay it by 30+ seconds).
    """
    await asyncio.sleep(ALEXA_EN_DELAY)
    await _async_send_alexa_en(hass, entry, message, context_id)


async def _async_send_alexa_en(
    hass: HomeAssistant,
    entry: ConfigEntry,
    message: str,
    context_id: str | None = None,
) -> None:
    """Send English Alexa TTS to the dedicated English Echo."""
    # Skip if alexa_media integration is not available on this instance
    if not hass.services.has_service("notify", "alexa_media"):
        _LOGGER.debug("Alexa EN TTS skipped: notify.alexa_media service not available")
        return

    cfg = _get_runtime_config(entry)
    alexa_en_target = cfg["alexa_en_target"]
    if not alexa_en_target:
        _LOGGER.warning("English Alexa target not configured — skipping")
        return
    try:
        await hass.services.async_call(
            "notify",
            "alexa_media",
            {
                "message": message,
                "target": [alexa_en_target],
                "data": {"type": "tts"},
            },
            blocking=True,
        )
        _LOGGER.debug("English Alexa TTS sent: %r", message)
        _record_alexa_emission(
            hass, [alexa_en_target], "tts_en", cfg, message, context_id
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to send English Alexa TTS: %s", exc)
