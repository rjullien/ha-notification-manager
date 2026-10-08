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
- **Jobs en mémoire** : un redémarrage Home Assistant les tue. Même limitation que le Case B de base ; le boot-time recovery + persistance (ci-dessous) compense.
- **Cleanup** : retirer les entrées de verrous/files quand une entity disparaît de la config.

### Estimation de durée du TTS au lieu de huit secondes hardcodées

Huit secondes, c'est le défaut actuel, mais c'est trop court pour certains messages : le volume redescend avant la fin de l'annonce. Idée : extrapoler la durée depuis la longueur du texte (vitesse de parole en mots par seconde, ajustée pour le français), avec le `alexa_post_tts_delay` comme plancher. À valider empiriquement.

### Anglais = Echo dédié = file séparée

Le chemin `message_alexa_en` vise un Echo dédié (`alexa_en_target`), distinct des players français. Il a donc **sa propre file**, pas de partage avec les files françaises. Comportement identique par ailleurs (save, set, TTS, restore), avec son propre délai.

### Persistance du last-known-good (nouveau — session du 8 octobre)

Aujourd'hui `DATA_ALEXA_LAST_GOOD_VOLUMES` est purement en mémoire : un reboot Home Assistant l'efface, et le Case B (volume bloqué haut après restart) n'a aucune parade. **Décision : persister le cache dans le store de Home Assistant** (`hass.helpers.storage` ou équivalent). Au boot, `async_setup_entry` recharge le cache avant de scanner les players.

Règle d'écriture stricte : **on ne persiste que sur une lecture de volume réussie** (`reported is not None`). Une entity indisponible ou un `volume_level` non numérique ne doit jamais écrire dans le cache — c'est exactement l'item 4. Sinon on persisterait une valeur empoisonnée à zéro virgule cinq et le boot-time recovery la réutiliserait indéfiniment.

Au démarrage : pour chaque player dont `volume_level` est à moins de `_VOLUME_EPS` du `alexa_tts_volume` et diffère de la valeur persistée, forcer un restore vers la valeur du cache (ou `ALEXA_DEFAULT_VOLUME` s'il n'y a pas de cache). Ça tue le Case B et le Case I à la source, sans dépendre d'un job en mémoire.

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

**Fix direction:** don't write to `last_good` when `reported is None`. Combiné à la persistance : une valeur empoisonnée survivrait au reboot, donc cette règle devient critique.

### 5. `alexa_post_tts_delay = 0` not rejected (LOW)

The options flow validates `>= 0` but should validate `> 0`. A zero delay races the restore against in-flight TTS playback.

### 6. No in-flight task cancellation on unload (MEDIUM)

`async_unload_entry` stops the watchdog and coordinator but does not cancel running Alexa cycles or run their restores. Reload mid-cycle = orphaned task, no restore, and the shared cache survives the reload boundary.

**Fix direction:** track in-flight tasks per entry, cancel or fence them on unload, and either clear or re-validate the (now persisted) cache.

---

## Chantiers pour un design solide (plan d'implémentation)

Dans l'ordre :

1. **Traiter les échecs de volume set** — collecter les bools du gather, sauter les enceintes mortes avant le TTS, warning agrégé. (Item 1)
2. **Persister le last-known-good** dans le store HA + **boot-time recovery** au setup. (Persistance ci-dessus, tue Case B et Case I)
3. **Restore différé exponentiel dédupliqué** — trois essais (30s / 1min / 2min), un job par entity, déclenché seulement si verrou libre et file vide. (Tue Case A et Case D)
4. **Ne plus écrire last-good sur reported is None** + valider `alexa_post_tts_delay > 0` dans le flow. (Items 4 et 5)
5. **Annuler ou clôturer les cycles en vol au unload** et purger le cache partagé. (Item 6)
6. **File par Echo + save/restore unique** (design cible). Élimine le churn de volume entre messages consécutifs.
7. **Estimation de durée TTS** depuis la longueur du texte, plancher = `alexa_post_tts_delay`. Empêche le restore anticipé en pleine annonce.

---

## Cas où le volume reste bloqué haut (matrice exhaustive)

| Case | Trigger | Ends at | Mitigation today | Gap |
|------|---------|---------|-----------------|-----|
| A | Restore fails twice | 0.7 | retry-once | **→ delayed re-restore job** |
| B | HA restart in sleep window | 0.7, self-sustaining | none (docs only) | **→ persistance + boot-time recovery** |
| C | Task cancelled pre-restore | 0.7 | `_run_logged` | **→ unload cancellation (item 6)** |
| D | Restore `volume_set` raises | 0.7 | immediate retry | **→ delayed re-restore job** |
| E | Overlapping cycles | 0.7 (transient) | last-known-good cache | cache can be poisoned (F) |
| F | Unavailable at save | 0.5 (wrong) | none | **→ don't cache default (item 4) + persistance rule** |
| G | delay = 0 | clipped TTS / race | validates `>= 0` | **→ validate > 0 (item 5)** |
| H | TTS call throws | restored (OK) | fall-through to step 5 | — |
| I | Unload mid-cycle | 0.7 | none | **→ unload cancellation (item 6)** |
| J | Bad volume_level type | 0.5 (wrong) | none | same as F |

---

None of these require upstream changes to alexa_media — they are all local to this component.
