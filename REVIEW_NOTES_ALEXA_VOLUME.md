# Review: Alexa volume management — ha-notification-manager

> **Author:** Grok Tesla (Grok, voice session from a Tesla vehicle), on behalf of rjullien.
> **Date:** 2026-10-07. Updated 2026-10-08 with design decisions from voice review.
> **Scope:** `custom_components/notification_manager/alexa.py` — the save → set → TTS → restore volume cycle.
> **Baseline:** version 1.9.6 (`main` @ `e7c4a80`).

---

## Design cible : file par Echo + restore différé dédupliqué

### Principe

Aujourd'hui le cycle est : save → set TTS → TTS → sleep 8s → restore, sérialisé derrière un verrou global. Le nouveau design remplace ça par une architecture à deux niveaux :

1. **Une file de messages par `entity_id` Alexa** (dictionnaire de files, indexé par entity). Chaque Echo gère sa propre file indépendamment des autres : cuisine et chambre peuvent annoncer en même temps sans s'attendre. Le verrou global actuel devient un verrou par Echo.
2. **Le volume monte une fois, reste à zéro virgule sept tant que la file n'est pas vide, et ne redescend qu'à la fin.** Fin des allers-retours de volume entre chaque annonce quand quinze messages partent d'affilée.

### Cycle par Echo

```
message arrive
  → sous le verrou de l'Echo :
      si la file était vide : save le volume de repos (live + last-known-good)
      enqueue le message
      si c'est le seul message en file : lancer le worker
  → le worker, tant que la file n'est pas vide :
      dépile un message
      set volume TTS (bloquant, avant le TTS)
      envoie le TTS
      attend la fin (voir estimation de durée ci-dessous)
  → file vide : restore le volume de repos (retry exponentiel, voir ci-dessous)
```

### Règle atomique file vide → restore

La transition « file vide » → « lancer le restore » doit être faite **sous le verrou de l'Echo**, en regardant la file au moment de la décision :

- File vide → lancer le restore.
- File non vide → ne rien faire, le worker continue.

Un message qui arrive **pendant** un restore en cours se cale derrière dans la file : il redémarre un cycle propre (re-save, re-set, etc.) après que le restore soit terminé. On ne restaure jamais pendant qu'un message est en route, et on ne sauve jamais un volume qui n'a pas encore été restauré (le cache last-known-good protège de ça : à chaque save, si le volume live ressemble au niveau TTS, on prend la valeur mémorisée).

### Retry de restore différé, exponentiel, dédupliqué

Si un restore échoue, on programme un nouvel essai. Trois tentatives, espacement exponentiel : trente secondes, une minute, deux minutes. **Un seul job par entity** : si plusieurs échecs se succèdent, on ne crée pas plusieurs jobs, on en garde un seul qui se reprogramme. Le job ne se déclenche que quand le verrou de l'Echo est libre **et** que la file est vide — inutile de restaurer si quinze messages sont en queue.

Side effects à gérer :

- **Volume baissé manuellement pendant la fenêtre de retry** : le job doit vérifier que `volume_level` n'a pas bougé depuis l'échec avant de restaurer (sinon il écraserait le choix de l'utilisateur).
- **Jobs en mémoire** : un redémarrage Home Assistant les tue. Même limitation que le Case B de base ; le boot-time recovery (ci-dessous) compense.
- **Cleanup** : retirer les entrées de verrous/files quand une entity disparaît de la config.

### Estimation de durée du TTS au lieu de huit secondes hardcodées

Huit secondes, c'est le défaut actuel, mais c'est trop court pour certains messages : le volume redescend avant la fin de l'annonce. Idée : extrapoler la durée depuis la longueur du texte (vitesse de parole en mots par seconde, ajustée pour le français), avec le `alexa_post_tts_delay` comme plancher. À valider empiriquement.

### Anglais = Echo dédié = file séparée

Le chemin `message_alexa_en` vise un Echo dédié (`alexa_en_target`), distinct des players français. Il a donc **sa propre file**, pas de partage avec les files françaises. Comportement identique par ailleurs (save, set, TTS, restore), avec son propre délai.

### Mémoire

Un dictionnaire de verrous/files par Echo : quelques octets par entrée. Même avec vingt haut-parleurs, on parle de kilooctets. Le coût réel, c'est la complexité du code, pas la RAM. À ignorer.

---

## Issues encore ouvertes (priorité)

### 1. `volume_set` failures silently swallowed (HIGH) — comportement

**Where:** `_async_send_alexa`, step 2.

`_async_set_volume` returns `bool`, but the results are discarded. If `media_player.volume_set` raises on one speaker, the TTS is still sent to the whole target list : some rooms speak at TTS volume, others at their old resting volume, with no aggregated error.

**Fix direction:** collect the bools, skip or warn on failed targets before issuing the TTS. Decide whether a single failure aborts the whole TTS or just that speaker.

### 2. No read-back verification after `volume_set` (MEDIUM) — documentation

Alexa Media's `volume_set` can return success before the Echo actually applied the level. The TTS may start at the old volume. Last-known-good protects the *next* cycle but not the current one. Upstream `alexa_media` limitation ; document as known caveat in README troubleshooting. (Re-read + warn is optional and low value — not observed in practice.)

### 3. The 8-second post-TTS sleep holds the Alexa lock — **NOT A BUG, IMPORTANT FEATURE**

Any concurrent `notify` with an Alexa message queues behind the full cycle. This is **intentional and desirable** : two messages must never be spoken simultaneously on the same Echo, or Alexa mixes them and it's chaos. The lock exists to serialize speech per speaker. Do **not** detach the sleep from the lock for the French path.

The real improvements on this item are the ones in the design cible above : per-Echo queues (so different Echos don't block each other), duration estimation (so the wait matches the message), and the atomic empty-queue → restore rule.

### 4. Unavailable player seeded into last-known-good at default 0.5 (LOW)

When a target is `unavailable` / `unknown` at save time, `reported is None` → `ALEXA_DEFAULT_VOLUME` (0.5) written into `last_good`. If the true resting volume was 0.3, restores go to 0.5 until a good read lands.

**Fix direction:** don't write to `last_good` when `reported is None`.

---

## Cas où le volume reste bloqué haut (matrice exhaustive)

| Case | Trigger | Ends at | Mitigation today | Gap |
|------|---------|---------|-----------------|-----|
| A | Restore fails twice | 0.7 | retry-once | **→ delayed re-restore job (design cible)** |
| B | HA restart in sleep window | 0.7, self-sustaining | none (docs only) | **→ boot-time recovery** |
| C | Task cancelled pre-restore | 0.7 | `_run_logged` | no retry, no cleanup hook |
| D | Restore `volume_set` raises | 0.7 | immediate retry | **→ delayed re-restore job** |
| E | Overlapping cycles | 0.7 (transient) | last-known-good cache | cache can be poisoned (F) |
| F | Unavailable at save | 0.5 (wrong) | none | **→ don't cache default (item 4)** |
| G | delay = 0 | clipped TTS / race | validates `>= 0` | should be `> 0` |
| H | TTS call throws | restored (OK) | fall-through to step 5 | — |
| I | Unload mid-cycle | 0.7 | none | no in-flight task cancellation |
| J | Bad volume_level type | 0.5 (wrong) | none | same as F |

---

## What would actually fix the stuck-high class

1. **Boot-time recovery:** on `async_setup_entry`, scan `alexa_players` for any whose `volume_level` is within `_VOLUME_EPS` of `alexa_tts_volume` and differs from its cached last-known-good; force a restore to the cached value (or `ALEXA_DEFAULT_VOLUME` if no cache). Kills Case B and Case I.
2. **Delayed re-restore job:** exponential, 3 attempts (30s / 1min / 2min), deduplicated per entity, gated on lock-free + empty queue. Kills Case A and Case D. Side effects: manual volume changes during the window, in-memory jobs lost on restart.
3. **Don't write `last_good` when `reported is None`** (item 4). Kills Case F and Case J.
4. **Validate `alexa_post_tts_delay > 0`** in the options/reconfigure flow. Kills Case G.
5. **Cancel or fence in-flight cycles on unload**, and clear or re-validate the shared cache. Kills Case I.
6. **Per-Echo queue + single save/restore** (design cible). Eliminates volume churn between back-to-back messages and bounds the restore-failure window.
7. **TTS duration estimation from message length** instead of hardcoded 8s. Prevents early restore mid-speech.

None of these require upstream changes to alexa_media — they are all local to this component.
