# Review: Alexa volume management — ha-notification-manager

> **Author:** Grok Tesla (Grok, voice session from a Tesla vehicle), on behalf of rjullien.
> **Date:** 2026-10-07.
> **Scope:** `custom_components/notification_manager/alexa.py` — the save → set → TTS → restore volume cycle.
> **Baseline:** version 1.9.6 (`main` @ `e7c4a80`).

Four issues found. None are show-stoppers, but items 1 and 2 can produce audible misbehavior in real homes.

---

## 1. `volume_set` failures are silently swallowed (HIGH)

**Where:** `_async_send_alexa`, step 2.

```python
await asyncio.gather(
    *(_async_set_volume(hass, eid, alexa_tts_volume) for eid in targets)
)
```

`_async_set_volume` returns `bool`, but the results are discarded. If `media_player.volume_set` raises on one speaker (entity gone, service hiccup), the TTS is still sent to the whole target list. Consequence: some rooms speak at the correct TTS volume while others play at their old resting volume, with no error log beyond the per-entity warning inside `_async_set_volume`.

**Fix direction:** collect the bools, log/skip targets that failed before issuing the TTS call, or at least aggregate a warning. Decide whether a single failure should abort the whole TTS or just that speaker.

---

## 2. No read-back verification after `volume_set` (MEDIUM)

**Where:** same step 2, between the gather and the TTS `async_call`.

Alexa Media's `volume_set` can return success before the Echo has actually applied the level (network round-trip to the device). The TTS then starts at whatever the speaker was doing before. The last-known-good cache (`DATA_ALEXA_LAST_GOOD_VOLUMES` + `_resolve_restore_volume`) protects the *next* cycle from saving TTS-as-resting, but it does nothing for the cycle in progress.

This is partly an upstream `alexa_media` limitation, but the component should at least document it as a known caveat (README troubleshooting section) so it's not mistaken for a bug in this repo.

**Fix direction:** optional — re-read `volume_level` after the gather and warn when it still differs from `alexa_tts_volume` beyond `_VOLUME_EPS`. Cheap, and turns a silent misbehavior into a visible one.

---

## 3. The 8-second post-TTS sleep holds the Alexa lock (MEDIUM)

**Where:** step 4, inside `async with lock:`.

```python
async with lock:
    ...
    await asyncio.sleep(cfg["alexa_post_tts_delay"])  # default 8s
    await _async_restore_volumes(hass, original_volumes)
```

Any concurrent `notify` with an Alexa message queues behind the full cycle: save, set, TTS, sleep, restore. The English path (`_async_send_alexa_en_delayed`) was deliberately detached into its own task with its own 3s delay precisely to avoid being blocked by slow channels — but the French path, which is the one actually used, still blocks.

**Fix direction:** the lock only needs to cover save → set → TTS → restore-decision. The sleep + restore could run after releasing it, with the restore still serialised per-entity via the lock or a narrower critical section. Trade-off: two overlapping cycles could interleave restores; the last-known-good cache already handles the observable symptom of that, so the risk is bounded.

---

## 4. Unavailable player seeded into last-known-good at default 0.5 (LOW)

**Where:** the pre-TTS loop in `_async_send_alexa`.

When a target's state is `unavailable` / `unknown` at save time, `reported` is `None`, `_resolve_restore_volume` falls back to `ALEXA_DEFAULT_VOLUME` (0.5), and that value is written into `last_good[entity_id]`. If the player's true resting volume was, say, 0.3, the next cycle restores to 0.5 until a *successful* read overrides it. While the player stays unavailable, 0.5 is sticky.

**Fix direction:** either don't write to `last_good` when `reported is None` (keep whatever was there, or nothing), or key the default per-player. Minor, but it's the one place the cache can learn a wrong value from a non-event.

---

## What looks solid

- `_VOLUME_EPS = 0.01` float tolerance in `_volumes_close`.
- `_resolve_restore_volume` preferring last-known-good when the live attribute is stuck at TTS level — this is the right fix for the alexa_media lag class of bugs.
- Restore retry-once in `_async_restore_volumes`.
- `last_good[entity_id] = restore_vol` written *before* the restore attempt, so a failed `volume_set` doesn't poison the next cycle.
- Emission recorded only after the TTS service call succeeds with a resolved, availability-filtered target list.
- The lock comment in `__init__.py` correctly explains why `DATA_ALEXA_LOCK` is shared across entries.

---

## Suggested priority for Cursor

1. Item 1 — silent volume_set failure (behavior bug, user-visible).
2. Item 3 — lock scope (latency bug, user-visible under concurrency).
3. Item 2 — document or add read-back (mostly documentation).
4. Item 4 — don't cache default on unavailable (edge case).