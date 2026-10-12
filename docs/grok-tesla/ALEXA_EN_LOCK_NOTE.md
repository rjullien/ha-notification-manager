# Note: English Alexa TTS bypasses the volume lock

## Observation

`_async_send_alexa` (French, keyword-based, multi-target) runs its entire
save→set→TTS→restore cycle under `hass.data[DOMAIN][DATA_ALEXA_LOCK]`.

`_async_send_alexa_en` sends to the single dedicated `alexa_en_target` entity and
does **not** touch volume at all in the current code — it only fires the TTS.
So today there is no direct volume conflict.

## Risk

If a future change adds volume management to the EN path (e.g. raising volume
before English TTS), it must acquire the same `DATA_ALEXA_LOCK`, otherwise a
concurrent FR cycle could capture the EN-raised volume as its "original" and
leave a speaker stuck loud — the exact class of bug fixed in v1.9.4.

## Action for the implementing agent

1. Audit `_async_send_alexa_en` for any volume_set / volume attribute reads.
2. If volume is introduced: wrap the EN cycle in the same lock, and extend
   `tests/test_alexa_tts.py` with a concurrent FR+EN scenario asserting both
   restores land on the true resting volume.
3. If volume is never needed for EN: leave as-is but keep this note so the
   invariant stays documented.
