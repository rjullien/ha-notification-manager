# Review notes — `cursor/notify-module-split-bf9d`

> **Author:** This in-depth analysis was performed by **Grok Tesla** (Grok, voice session from a Tesla vehicle), on behalf of rjullien.
> Date: 2026-10-07.
>
> **Update (priority-fixes PR, 1.9.6):** status refreshed after agreed priority +
> multi-entry + confirmed follow-up fixes.

Voice review of the coarser `__init__.py` split (PR #16, based on `main` after
PR #15 squash-merge — behavior baseline is version **1.9.5**).

## Bugs from TODO_BUGS.md — status

### 1. WhatsApp gated on `message_tel` — FIXED
### 2. Inconsistent normalization of channel selectors — FIXED

### 3. `parse_mode` not forwarded to WhatsApp — FIXED (documented)

WhatsApp / GoWA is **plain-text only** (`{phone, message}`). Documented in
`services.yaml` (`parse_mode`), README, and `_async_send_whatsapp` docstring.
No bridge wiring — the API has no parse-mode field.

### 4. `photo_path` and `photo_url` both allowed — FIXED (documented rule)

**Rule: `photo_path` wins** when both are set (`photo_url` ignored). Documented
in `services.yaml` / README; locked by `test_photo_path_wins_when_both_provided`.

## Earlier notes

- Watchdog `@callback` on async check methods — **FIXED**
- `WATCHDOG_CRITICAL_INTERVAL_MINUTES` → 5 — **FIXED**
- Dead `elif` in `coordinator.async_shutdown` — **FIXED**
- Unguarded private-config import — **FIXED**
- Multi-entry service dedup (`has_service`) — **FIXED**
- Alexa EN volume-lock — **SKIPPED** (EN unused by maintainer; see below)

## Split / multi-entry

### Circular import → `runtime.py` — FIXED
### Per-entry Alexa resolver — FIXED

Shared across entries (intentional): `DATA_ALEXA_LOCK`, `DATA_ALEXA_EMISSIONS`,
`DATA_ALEXA_LAST_GOOD_VOLUMES`. Domain services register once and bind via
`_get_primary_entry`.

### EN Alexa (`message_alexa_en` / `alexa_en_target`) — DEPRECATED / UNUSED

Maintainer no longer uses the English Alexa path. **Left as-is** (no gating
change). Candidate for later removal. Noted in `services.yaml` and README.

## Deep review status

| # | Item | Status |
|---|------|--------|
| 5 | EN vs `notification_alexa` | SKIP — unused EN; no gating change |
| 6 | Bridge alert `parse_mode` | **FIXED** (`PARSE_MODE_PLAIN`) |
| 7 | Mobile service `split(".", 1)` | OPEN (minor, deferred) |
| 8 | Resolver per entry | **FIXED** |
| 9 | `phone_targets` structure validation | OPEN (deferred) |
| 10 | Reconfigure key-loss | **FIXED** (`dict(entry.data)` then overwrite) |
| 11 | EN selector test | SKIP — unused EN |
| 12 | EN services.yaml docs | **FIXED** (deprecated note) |
| 13 | Watchdog via `entry.data` | **SKIP** — site-private const intentional |
| 14 | `telegram_groups` cross-link | **FIXED** in `services.yaml` |
