# Review: Alexa volume management — ha-notification-manager

Document consolidé (PR #18) pour la revue de René : **Partie 1** = design initial Grok Tesla (substance inchangée) ; **Partie 2** = revue critique Grok Bot, vérifiée contre le code de la branche ; **Partie 3** = spec révisée et plan.

---

# Partie 1 — Analyse et design initial (Grok Tesla)

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

---

# Partie 2 — Revue critique et arguments (Grok Bot)

Revue en lecture seule (8 oct. 2026). Affirmations sur **ce dépôt** vérifiées contre la branche `cursor/alexa-volume-review` (refs fichier / fonction / test). Affirmations sur **alexa_media / alexapy / HA core** : d'après lecture de code upstream par Grok Bot — **non testées sur appareil** chez René ; version installée non vérifiable ici.

## 2.1 Cause racine manquante (upstream, non testée sur appareil)

Le design Partie 1 traite bien les symptômes locaux (restore raté A/D, restart pendant le sleep B, unload mi-cycle C/I, cache empoisonné à 0.5 F/J, délai 0 G). Il ne mentionne pas le mécanisme amont :

- Dans **alexa_media**, `async_set_volume_level` et `async_send_tts` seraient fire-and-forget (`hass.async_create_task(self.alexa_api.set_volume(...))`) ; `volume_level` mis à jour de façon **optimiste** ; appel sur Echo indisponible ignoré silencieusement *(lecture upstream, non testée)*.
- Dans **alexapy** `run_behavior`, les exceptions seraient avalées et les commandes regroupées ~**1,5 s** (`queue_delay`) en une séquence *(lecture upstream, non testée)*.

Conséquences pour notre code local :

- `_async_set_volume` (`alexa.py`) renvoie `True` dès que `media_player.volume_set` avec `blocking=True` ne lève pas — un échec Amazon ne remonte donc presque jamais.
- Le commentaire d'étape 2 (`alexa.py` ~269–270 : « speech can never start at the old volume ») suppose un ordre volume→TTS que `blocking=True` **ne garantit pas** si alexa_media détache l'appel API *(hypothèse upstream)*.

## 2.2 Verdict idée par idée

### Files par Echo — à ajuster / risqué

**Gain réel** : pièces indépendantes, moins de rampes volume. **Trous dans le design** :

- `_async_send_alexa` envoie **un seul** `notify.alexa_media` avec une **liste** de cibles (`alexa.py` ~277–281). Multi-cibles ⇒ N verrous en ordre trié (sinon deadlock) ou N envois non synchronisés.
- Avec plusieurs serials, alexapy regrouperait par compte et pourrait paralléliser volume et TTS d'une même Echo *(upstream, non testé)*.
- `entity_id` ≠ appareil physique (groupes WHA, paires stéréo ; docstring alexa_media « Does not work on WHA Groups » — *upstream*).
- Risque de famine du restore → maintien maximal nécessaire ; propriété des workers multi-entry + annulation à l'unload.

**Alternative moins chère** : garder le verrou global (`DATA_ALEXA_LOCK`, docstring `__init__.py` : « one volume save/restore lock for the house ») et **coalescer** (si un cycle attend déjà le verrou, ne pas restaurer/resauvegarder entre les deux).

### Retry de restore dédupliqué — bonne idée, mauvais déclencheur

Les échecs sont silencieux côté Amazon ; relire `volume_level` ne prouve rien (optimiste). Mieux : **re-restore systématique ~15–30 s** après le cycle, un seul par Echo, annulé par un nouveau cycle ou l'unload (`async_call_later` + `entry.async_on_unload`) ; sauté si `volume_level` n'est ni la valeur restaurée ni le niveau TTS (changement manuel). 1–2 renvois suffisent, pas 3.

Aujourd'hui : un seul retry immédiat dans `_async_restore_volumes` (`alexa.py` ~117–128) — conforme aux cas A/D de la matrice Partie 1.

### Estimation de durée — bonne, à ajuster

Ajouter un offset (queue_delay alexapy ~1,5 s + latence cloud — *upstream*), compter en caractères, retirer le SSML, plafond, plancher = `alexa_post_tts_delay`, débit à calibrer. Passer la même estimation à `_record_alexa_emission` (`speech_estimate`, aujourd'hui = `alexa_post_tts_delay` dans `alexa.py` ~184).

### Last-known-good persisté + recovery au boot — bon fond, détail risqué

- API : utiliser `homeassistant.helpers.storage.Store` (`async_load` / `async_save` / `async_delay_save`), pas `hass.helpers.storage` (API obsolète / absente — *HA core*).
- Au setup, les entités alexa_media peuvent ne pas être prêtes → attendre `EVENT_HOMEASSISTANT_STARTED` + entité disponible avec volume valide, avec timeout.
- Faux positif : la règle « volume ≈ TTS et ≠ sauvegardé » baisse à chaque boot une Echo volontairement à 0,7. **Mieux** : persister un marqueur « cycle en cours » (repos + TTS) **avant** la montée, l'effacer après le restore ; au boot, ne corriger que les Echo marquées (crash / coupure).
- « N'écrire que sur une lecture réussie » : correct et indispensable (cf. item 4).
- Multi-entry : cache partagé sous `hass.data[DOMAIN]` (`__init__.py` ~126–128) — **ne jamais le vider** à l'unload d'une entrée (contredit l'item 6 Partie 1 « purger le cache »).
- Nuance cas B : à l'arrêt normal HA attendrait les tâches `hass.async_create_task` (jusqu'à ~100 s) ; B viendrait surtout des crashs / arrêts forcés ou d'alexa_media qui s'arrête en parallèle *(HA core / non vérifié sans logs)*.

### Unload / reload (absent du design Partie 1)

Avec une seule entrée (cas typique), `async_unload_entry` fait `hass.data.pop(DOMAIN)` quand plus aucune entrée reste (`__init__.py` ~192–199) → verrou, cache et journal perdus. La tâche en vol (créée via `hass.async_create_task` dans `notify.py` ~79–86) garde l'ancien verrou ; la nouvelle entrée en crée un autre → chevauchement (cas E) ; cache perdu à chaque reconfigure (équivalent cas B).

**Fix** : `entry.async_create_task(hass, ...)` pour les tâches Alexa (HA attend ~10 s au unload — *HA core*).

### Annulation mi-cycle

Dans `_async_send_alexa`, pas de `try/finally` autour des étapes 2–5 : une annulation pendant le `asyncio.sleep` (étape 4) saute l'étape 5 (cas C). Proposition : `try/finally` + `asyncio.shield` sur le restore.

### Chemin anglais

Marqué inutilisé par le mainteneur dans `notify.py` ~88–89. `_async_send_alexa_en` (`alexa.py` ~377–410) **ne gère ni volume ni verrou**. Ne pas l'inclure dans le design volume (contredit « comportement identique » Partie 1).

## 2.3 Réponses recommandées aux points ouverts

| # | Sujet | Verdict Grok Bot |
|---|--------|------------------|
| 1 | Échecs `volume_set` | Garder les bools + warning groupé ; **jamais** annuler le TTS. Faible valeur, pas prioritaire (échecs Amazon silencieux de toute façon — *upstream*). |
| 2 | Pas de read-back | Documenter. « Pas observé en pratique » est contredit par le modèle multi-cibles alexapy *(upstream)*. Option à tester : ~2 s (> queue_delay) entre volume et TTS. |
| 3 | Sleep sous verrou | Déjà reclassé feature (Partie 1) — OK. |
| 4 | 0,5 sur Echo indisponible | Ne pas écrire dans le cache si `reported is None` — changer aussi `alexa.py` **ligne 267** (`last_good[entity_id] = restore_vol`), pas seulement `_resolve_restore_volume`. Sans valeur connue, exclure l'Echo des changements de volume. Adapter `test_unavailable_player_uses_default_volume` (`tests/test_alexa_tts.py`). Le filtre amont (`alexa.py` ~222–224) n'exclut que `unavailable`, pas `unknown`. |
| 5 | Délai 0 | Valider `> 0` dans `async_step_reconfigure_alexa` (`config_flow.py` ~336–338, aujourd'hui `< 0` rejeté) + plancher runtime `max(delay, 2)` pour les entrées existantes. Attention à `test_zero_volume_and_delay_preserved` (`tests/test_guards_and_unload.py`). Rendu en partie caduc par l'estimation de durée. |
| 6 | Unload | `entry.async_create_task` + restore en `finally` protégé ; **ne pas** vider le cache partagé. |

## 2.4 Écarts doc (Partie 1) / code (branche)

| Affirmation Partie 1 | Réalité code |
|----------------------|--------------|
| Item 5 : validation dans « options flow » | Validation `alexa_post_tts_delay` dans `async_step_reconfigure_alexa` ; l'options flow ne gère que le bridge (`config_flow.py` ~436–437). |
| Item 6 : « le cache survit au reload » | Faux avec une seule entrée : `hass.data.pop(DOMAIN)` (`__init__.py` ~199). |
| Anglais : « comportement identique » | Faux : `_async_send_alexa_en` sans volume/verrou. |
| Ordre volume puis TTS garanti | Commentaire étape 2 + design le supposent ; non garanti si alexa_media fire-and-forget *(upstream)*. |
| `hass.helpers.storage` | À remplacer par `homeassistant.helpers.storage.Store` *(HA core)*. |
| Verrou global | Intentionnel (`__init__.py` docstring) — à mettre à jour si files par Echo. |
| Commit « 5 open issues » vs 6 items | Le doc numérote 6 (dont #3 = feature) ; le message de commit parlait de 5. |

**Conformes** : cas A/D (retry immédiat `_async_restore_volumes`) ; cas H (`test_tts_failure_still_restores_volume`) ; mécanisme B via branche « first cycle » de `_resolve_restore_volume` (`alexa.py` ~94–96).

**Non vérifiable ici** : fréquence réelle des cas (logs), comportement réel du bridge avec Bearer, deux TTS qui se chevauchent sur une même Echo.

## 2.5 Fix WhatsApp Bearer (hors volume, présent sur la PR)

- Correct : `_async_send_whatsapp` ajoute `Authorization: Bearer {bridge_token}` (`messaging.py` ~202–205) ; `notify.py` passe `entry.data[CONF_BRIDGE_TOKEN]` (~122–127).
- Cohérent avec `coordinator.py` ~58, `bridge_services.py` ~79 / ~120, `config_flow.py` ~98.
- Historique : retiré volontairement en `6e13d75` (v1.8.0 « migrate to GoWA bridge (no auth…) ») ; la PR le remet.
- Token vide → header `Bearer ` (espace final) ; les autres appels font déjà pareil — comportement bridge **non vérifié** sans le code du bridge.
- Suggestion : n'envoyer le header que si token non vide, via un helper partagé par les 4 appels.
- Test manquant dans `TestSendWhatsapp` (`tests/test_whatsapp.py`) : avec / sans token.
- `a5795f8` avait tronqué `messaging.py` (~33 lignes) ; `567f305` restaure. Vs `main` : trois catégories de diff (header Bearer, docstring, renommage `_LOGGER` → `_LOGGING`). Squash-merge recommandé. Renommage `_LOGGING` à annuler (convention HA = `_LOGGER`).

---

# Partie 3 — Spec révisée et plan

Synthèse pour décision de René. Priorité : corriger les trous qui laissent le volume à 0,7 **sans** réécrire l'architecture.

## 3.1 Décisions proposées

| Sujet | Décision proposée |
|-------|-------------------|
| Cause racine Amazon / alexapy | Documenter comme limite amont ; ne pas compter sur `blocking=True` ni sur `volume_level` comme preuve de succès. |
| Files par Echo | **Reporté.** D'abord coalescing sous le verrou global existant. |
| Retry restore | Re-restore **systématique** ~20 s après le cycle (1–2 fois), annulable ; pas de déclencheur « échec détecté » (silencieux). |
| Durée TTS estimée | Reportée (accompagne les files / coalescing). En attendant : plancher runtime du délai. |
| Persistance | `Store` + marqueur « cycle en cours » ; recovery seulement pour les Echo marquées, après `EVENT_HOMEASSISTANT_STARTED`. |
| Cache last-good | Ne jamais écrire si `reported is None` (dont ligne 267) ; ne pas purger à l'unload. |
| Unload | `entry.async_create_task` + `try/finally` + `asyncio.shield` sur le restore. |
| Chemin EN | Hors scope volume (inutilisé ; pas de save/restore aujourd'hui). |
| Item 1 (bools volume_set) | Reporté — warning groupé OK plus tard, ne jamais bloquer le TTS. |
| WhatsApp Bearer | Garder le fix ; annuler `_LOGGING` → `_LOGGER` ; helper header si token non vide ; ajouter tests ; squash recommandé. |

## 3.2 PR 1 — correctif minimal (~60 lignes)

Fichiers : `alexa.py`, `notify.py` (pas de nouvelle API HA / pas de Store).

1. **Restore garanti** : `try/finally` autour des étapes 2–4 de `_async_send_alexa` ; `asyncio.shield` sur `_async_restore_volumes` (cas C / I).
2. **Tâches trackées** : remplacer `hass.async_create_task` par `entry.async_create_task(hass, ...)` pour Alexa FR (et EN si on le laisse) dans `notify.py`.
3. **Cache sain** : si `reported is None`, ne pas faire `last_good[entity_id] = restore_vol` (ligne 267) ; exclure cette Echo des `volume_set` TTS/restore faute de valeur connue. Adapter `test_unavailable_player_uses_default_volume`.
4. **Re-restore systématique** : ~20 s après le cycle, un job par Echo (`async_call_later` + `entry.async_on_unload`), annulé si nouveau cycle ; sauté si volume ni repos ni TTS.
5. **Plancher délai** : runtime `max(alexa_post_tts_delay, 2)` (entrées déjà à 0) ; optionnellement valider `> 0` dans `async_step_reconfigure_alexa` (et mettre à jour `test_zero_volume_and_delay_preserved` si la sémantique change).

Hors PR 1 mais dans le même train WhatsApp (déjà sur la branche) : revert `_LOGGING` → `_LOGGER` ; test Bearer dans `TestSendWhatsapp`.

## 3.3 PR 2 — Store + marqueur + recovery boot

1. Persister via `homeassistant.helpers.storage.Store` le cache last-known-good **et** un marqueur « cycle en cours » (entity → `{resting, tts}`) écrit **avant** la montée, effacé après restore réussi.
2. Au boot : après `EVENT_HOMEASSISTANT_STARTED`, pour chaque Echo encore marquée, attendre disponibilité + `volume_level` numérique (timeout), puis restaurer vers `resting` (pas la heuristique « ≈ TTS » seule — évite le faux positif 0,7 volontaire).
3. Écriture Store uniquement sur lecture réussie (`reported is not None`).
4. Multi-entry : un Store domaine partagé ; jamais vidé à l'unload d'une entrée.

## 3.4 Reporté

- Files par Echo (+ estimation de durée TTS, calibrage débit / offset queue_delay).
- Coalescing sous verrou global (préalable moins cher aux files).
- Item 1 (collecte bools / skip cibles mortes avant TTS).
- Read-back post-`volume_set` / pause ~2 s (à tester empiriquement).
- Alignement volume du chemin anglais (sauf décision contraire de René).
- Helper Bearer partagé + omission du header si token vide (suggestion qualité).

## 3.5 Questions ouvertes pour René

1. **Confirmer PR 1 puis PR 2** (plutôt que le plan 7 étapes Partie 1) ?
2. **Coalescing** sous verrou global acceptable comme étape avant (ou à la place de) files par Echo ?
3. **Re-restore à ~20 s** (1–2 fois) vs retry exponentiel 30 s / 1 min / 2 min sur échec détecté ?
4. **Marqueur « cycle en cours »** pour le boot recovery — OK, ou préférence pour l'heuristique volume ≈ TTS de la Partie 1 ?
5. **Plancher runtime** du délai (ex. 2 s) : quelle valeur minimale chez toi ? Garder 0 en config + forcer au runtime, ou rejeter 0 dans le reconfigure ?
6. **WhatsApp** : squash des commits Bearer + revert `_LOGGING` dans la même PR, ou branche / commit séparés ?
7. **Chemin EN** : laisser tel quel (inutilisé), supprimer plus tard, ou un jour aligner sur le cycle volume ?
8. As-tu des **logs** de cas A/B/C observés en prod pour prioriser (fréquence réelle non vérifiable ici) ?
