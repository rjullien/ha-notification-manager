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

---

# Deep dive: every case where the volume stays HIGH

> Follow-up analysis, same session. The cycle has five steps: resolve resting volumes → set TTS volume → send TTS → sleep 8s → restore. For the volume to *stay* high, one of those steps must break **after** the set. Exhaustive enumeration below.

## Case A — Restore fails (both attempts) — HIGH

`_async_restore_volumes` gives each speaker two tries. If both `volume_set` calls fail (Echo back to unavailable mid-TTS, service error), the speaker sits at 0.7 with no further retry, no alert, no fallback. Silent.

## Case B — HA restart during the sleep window — HIGH

Resting volumes and the last-known-good cache are in-memory only. A restart between step 3 and step 5 means the restore never runs. On next boot, `volume_level` is still 0.7. The *next* cycle reads 0.7, compares it to the TTS volume, finds them equal, treats it as a legitimate resting volume, caches it, and restores to 0.7. Self-sustaining loop: stuck high until a human lowers it manually or a successful read of a true resting volume overwrites the cache. README mentions it as troubleshooting only — no automatic recovery.

## Case C — Task cancellation between sleep and restore — MEDIUM

The cycle runs inside an `async_create_task`. An HA shutdown, an unload, or an unhandled exception between the sleep and `_async_restore_volumes` aborts the restore with nothing catching it. `_run_logged` wraps the Alexa coroutine and logs, but does not retry. Same end state as Case A.

## Case D — `volume_set` itself raises on the *restore* call — MEDIUM

Covered by Case A for the happy path, but note: the retry is a second immediate call with zero backoff. If the failure is transient (brief network blip to the Echo), the retry often succeeds — good. If it's persistent (device offline), both fail and Case A applies. There is no third attempt and no delayed re-restore job scheduled for later.

## Case E — Concurrent cycle interleaving — LOW (mitigated)

Two overlapping notifies: cycle 1 saves V1, sets 0.7, sleeps; cycle 2 (queued on the lock) saves… 0.7 (the TTS level, because cycle 1 hasn't restored), sets 0.7 again, sleeps; cycle 1 restores to V1; cycle 2 restores to 0.7. Net effect: speaker ends at 0.7 instead of V1. The last-known-good cache is exactly the mitigation for this — `_resolve_restore_volume` reuses V1 when the live attribute reads as TTS level. So this case is *handled*, provided the cache wasn't itself poisoned (see Case F and the original item 4).

## Case F — Cache poisoned by an unavailable read — LOW

Same as original item 4: at save time, if the player is unavailable, `reported is None` → default 0.5 written to `last_good`. If the true resting volume was 0.3, restores go to 0.5 (not 0.7, but still wrong) until a good read lands. Not "stuck high at TTS level", but a related silent-wrong-volume failure mode.

## Case G — `alexa_post_tts_delay` set to 0 — LOW / config

If the user (or a bad options-flow save) sets the delay to 0, the restore runs immediately after the TTS service call returns — which may be *before* the Echo has finished speaking, and before alexa_media has flushed its own state. Risk: the restore's `volume_set` races with in-flight TTS playback; some Echo firmware applies volume changes by cutting/restarting the current stream, producing a clipped or doubled announcement, and in the worst interleaving the restore can be skipped if the entity goes `unavailable` in that instant. The options flow validates `>= 0` but not `> 0`.

## Case H — TTS service call throws — MEDIUM

If `notify.alexa_media` raises, the code logs the error and still falls through to the sleep + restore. So this case actually *does* restore — good, it's not a stuck-high path. Listed only so the matrix is complete: the dangerous cases are the ones where control never reaches step 5.

## Case I — Unload during cycle — MEDIUM

`async_unload_entry` stops the watchdog and shuts down the coordinator, but it does **not** cancel in-flight Alexa tasks or run restores for them. If the integration is reloaded mid-cycle, the task is orphaned: no restore, same as Case C. Additionally, because `DATA_ALEXA_LAST_GOOD_VOLUMES` is shared at domain root and *not* cleared on unload (only cleared when the last entry is removed), a reload keeps the poisoned or stale cache alive across the reload boundary.

## Case J — `volume_level` attribute missing/non-float at save time — LOW

`_parse_volume_attr` returns `None` on non-numeric values; handled like unavailable → default 0.5 cached. Same family as Case F.

## Summary table

| Case | Trigger | Ends at | Mitigation today | Gap |
|------|---------|---------|-----------------|-----|
| A | Restore fails twice | 0.7 | retry-once | no 3rd attempt, no alert |
| B | HA restart in sleep window | 0.7, self-sustaining | none (docs only) | no boot-time recovery |
| C | Task cancelled pre-restore | 0.7 | `_run_logged` | no retry, no cleanup hook |
| D | Restore `volume_set` raises | 0.7 | immediate retry | no delayed re-restore |
| E | Overlapping cycles | 0.7 (transient) | last-known-good cache | cache can be poisoned (F) |
| F | Unavailable at save | 0.5 (wrong) | none | caches default from non-event |
| G | delay = 0 | clipped TTS / race | validates `>= 0` | should be `> 0` |
| H | TTS call throws | restored (OK) | fall-through to step 5 | — |
| I | Unload mid-cycle | 0.7 | none | no in-flight task cancellation |
| J | Bad volume_level type | 0.5 (wrong) | none | same as F |

## What would actually fix the stuck-high class

1. **Boot-time recovery:** on `async_setup_entry`, scan `alexa_players` for any whose `volume_level` is within `_VOLUME_EPS` of `alexa_tts_volume` and differs from its cached last-known-good; force a restore to the cached value (or to `ALEXA_DEFAULT_VOLUME` if no cache). This kills Case B and Case I at the source.
2. **Delayed re-restore job:** when a restore attempt fails, schedule one more try after e.g. 30s via `async_call_later`. Kills Case A and Case D.
3. **Don't write `last_good` when `reported is None`** (original item 4). Kills Case F and Case J.
4. **Validate `alexa_post_tts_delay > 0`** in the options/reconfigure flow. Kills Case G.
5. **Cancel or fence in-flight cycles on unload**, and clear or re-validate the shared cache. Kills Case I.

None of these require upstream changes to alexa_media — they are all local to this component.
