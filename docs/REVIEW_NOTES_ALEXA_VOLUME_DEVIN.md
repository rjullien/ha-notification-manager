# Revue critique du design — Devin (12 oct. 2026)

Relecture de `REVIEW_NOTES_ALEXA_VOLUME.md` (Partie 1 : design Grok Tesla ; Partie 2 : critique Grok Bot ; Partie 3 : spec révisée). Chaque affirmation ci-dessous a été vérifiée contre `main` @ 1.9.6.

## Verdict

Le design Partie 3 est la bonne base : deux PRs (correctif minimal puis Store + recovery boot) plutôt que la refonte en files par Echo. Les trous identifiés sont réels et confirmés dans le code. **Cinq corrections à apporter avant implémentation**, quelques ajouts mineurs.

## Confirmé dans le code (`main` @ 1.9.6)

| Item spec | État réel |
|---|---|
| Pas de `try/finally` autour des étapes 2–5 de `_async_send_alexa` | Confirmé — `alexa.py:241-296` ; annulation pendant le `asyncio.sleep` (étape 4) → restore sauté → volume bloqué à 0.7 (Case C/I) |
| `hass.async_create_task` au lieu de `entry.async_create_task` | Confirmé — `notify.py:79-137` ; un reload d'entrée orpheline les cycles en vol |
| `hass.data.pop(DOMAIN)` au dernier unload | Confirmé — `__init__.py:199` ; verrou + cache last-good + emissions perdus à chaque reconfigure (single-entry) |
| `last_good[eid] = restore_vol` inconditionnel | Confirmé — `alexa.py:267` ; `reported is None` → `ALEXA_DEFAULT_VOLUME` (0.5) écrit dans le cache (Case F/J) |
| Filtre amont : exclut `unavailable`, pas `unknown` | Confirmé — `alexa.py:222-225` |
| `alexa_post_tts_delay=0` accepté | Confirmé — `config_flow.py:337` rejette `< 0` seulement |
| Chemin EN sans volume ni verrou | Confirmé — `alexa.py:377-410` ; inutilisé par le mainteneur, hors scope OK |
| Fix WhatsApp Bearer | Correct et cohérent — `coordinator.py:58`, `bridge_services.py:79/120`, `config_flow.py:98` envoient déjà `Authorization: Bearer`. Renommage parasite `_LOGGER`→`_LOGGING` à annuler + test manquant + squash, comme noté par Grok Bot |
| `queue_delay` alexapy ~1,5 s | Confirmé par la doc officielle alexapy (`run_behavior`/`send_sequence`, défaut 1.5 s) — le claim « batching amont » est fondé |

## Corrections à la spec Partie 3

### 1. Retirer `asyncio.shield` du restore

`await asyncio.shield(coro)` dans le `finally` **détache** le restore si la tâche est déjà annulée : l'`await` lève immédiatement `CancelledError`, le `async with lock` se ferme, et le restore continue **hors du verrou** — il peut alors chevaucher le save du cycle suivant. Exactement la classe de bug qu'on corrige.

Mieux, et plus simple : `try/finally` avec `await _async_restore_volumes(...)` **sans shield**. En Python 3.11+, une annulation unique n'empêche pas les `await` du `finally` de se terminer — le restore reste sous le lock, sérialisé. Shield ne protégerait que contre une *seconde* annulation (marginal) au prix d'un restore hors-lock.

### 2. Job re-restore : ajouter un compteur de génération par Echo

La spec dit « un job par Echo, annulé par un nouveau cycle ». Deux courses restent :

- Le job de cycle N peut être **déjà dispatché** quand le cycle N+1 démarre → il restaurerait vers l'ancienne valeur de repos.
- Si le job tire **pendant** que le cycle N+1 parle (verrou tenu), il écrase le volume TTS en pleine annonce.

Fix : compteur de génération par Echo (incrémenté au début de chaque cycle). Le job capture la génération au scheduling et ne s'exécute que si elle n'a pas bougé — couvre les deux courses, en plus de « skip si volume ni repos ni TTS ».

### 3. Boot recovery : marqueur **et** vérification de lecture, pas l'un ou l'autre

Restaurer tout Echo encore marqué au boot risque de taper un volume changé manuellement **pendant que HA était down** (ou un marqueur vieux de plusieurs jours). Règle proposée :

- le **marqueur** dit *quoi* restaurer (`resting`/`tts` par entity) ;
- la **lecture actuelle** dit *si* : ne restaurer que si `volume_level ≈ marker.tts` (ou illisible après timeout raisonnable) ; toute autre valeur = intervention manuelle → effacer le marqueur, ne rien faire.

Ça garde le faux-positif « 0.7 volontaire » hors du champ sans réintroduire l'heuristique fragile de la Partie 1. Ajouter une expiration du marqueur (ex. 24 h) en garde-fou.

### 4. Poison cache : séparer les deux actions

La spec couple « ne pas écrire `last_good` si `reported is None` » (obligatoire — sinon la valeur 0.5 empoisonnée survit au reboot une fois persistée) et « exclure l'Echo des `volume_set` TTS/restore » (discutable).

Le cas `volume_level` invalide (attribut non numérique, Echo en ligne) mérite réflexion : exclure l'Echo de la montée → l'annonce joue au volume de repos courant, potentiellement inaudible. Options : (a) exclure seulement `unavailable`/`unknown` des volume ops mais monter/restaurer sur défaut sans cacher quand l'attribut est juste invalide ; (b) exclusion uniforme, plus simple à raisonner. Je penche (b) — un attribut non numérique est un signal de santé douteux — mais c'est un vrai arbitrage annonce-qui-se-fait-entendre vs ne-pas-toucher. Les deux tests existants (`test_unavailable_player_uses_default_volume`, `test_invalid_volume_attribute_uses_default`) devront être réécrits selon le choix.

### 5. Plancher du délai : au point d'appel, pas dans `_get_runtime_config`

`max(alexa_post_tts_delay, 2)` doit s'appliquer au `asyncio.sleep` (`alexa.py:293`), pas dans `_get_runtime_config` : sinon `test_zero_volume_and_delay_preserved` casse et la sémantique « 0 = valeur sauvegardée valide » est silencieusement réécrite. Garder `0` accepté en config + plancher runtime. Et noter que 2 s peut encore couper les messages longs — c'est un plancher, l'estimation de durée reste la vraie réponse.

## Ajouts hors spec

- **`_run_logged` propage bien `CancelledError`** (`BaseException`, non catchée par `except Exception`) — compatible avec entry tasks + `finally`, à garder tel quel.
- **Chemin EN** : le passer aussi en `entry.async_create_task` pour cohérence (même classe de tâche orpheline, même si inutilisé).
- **Bools `volume_set`** : pour ~3 lignes, récupérer le gather et loguer un warning groupé des cibles en échec — télémétrie utile dès PR 1, prélude au « skip dead targets » différé.
- **`alexa_tts_volume = 0.0`** est accepté en config (0.0–1.0) et donne un TTS **muet** (montée à 0 puis restore). Si l'intention était « ne pas toucher au volume », ce mode n'existe pas. Question ouverte.
- **Orphelins de `test-pr-access`** : `whatsapp_jid.py` est un doublon déjà mergé via `jid_utils.py` (contenu identique) — ignorer. `alexa_players_validation.py` n'a jamais été câblé, même sur sa branche — `_parse_json` ne valide que la syntaxe JSON, pas la structure liste-de-strings (`"kitchen"` ou `[1,2]` passent et cassent la résolution au runtime). Le câbler dans `async_step_reconfigure_alexa` ou le jeter ; même classe que le `phone_targets` différé.
- **`TODO_BUGS.md` / notes de migration** : tout est corrigé — regroupés sur la branche docs avec un bandeau « historique/appliqué ».
- **Tests** : toute la couche HA est `MagicMock` — rien ne vérifie la vraie sémantique `entry.async_create_task` / `Store` / events. Les tests verrouillent l'intention, pas le comportement HA. Prévoir un smoke test sur la vraie instance après déploiement (un notify TTS + un reload d'entrée + un restart).
- **README** : la ligne « volumes in-memory, restore manuel après restart » (§239) devra être mise à jour quand la PR 2 lande.

## Réponses proposées aux 8 questions ouvertes de la spec

1. **PR 1 puis PR 2** — oui.
2. **Coalescing** sous verrou global — oui, comme étape avant files par Echo. Mécanisme précis : sauter le restore d'un cycle si un cycle suivant attend déjà le verrou (le save suivant lit via last-good ; le restore final du dernier cycle suffit). Les files par Echo restent une vraie amélioration mais pas nécessaire au fix.
3. **Re-restore systématique ~20 s** (×1–2) — d'accord avec Grok Bot : les échecs Amazon sont silencieux, un déclencheur « échec détecté » ne tirerait jamais.
4. **Marqueur** pour le recovery — oui, avec la vérification de lecture §3 ci-dessus.
5. **Plancher runtime 2 s**, `0` conservé en config ; validation `> 0` en reconfigure optionnelle.
6. **WhatsApp** : même PR squashée + revert `_LOGGING` + test Bearer dans `TestSendWhatsapp`.
7. **EN** : laisser tel quel (entry task quand même) ; suppression plus tard.
8. **Logs prod** : à toi — la fréquence réelle des cas A/B/C décide de la priorité PR 1 vs PR 2. Le README documente déjà le restore manuel après restart, donc Case B est un incident connu.

## Non vérifiable ici

- `alexa_media` fire-and-forget / mise à jour optimiste de `volume_level` (claim upstream non testé ; lisible dans le code de l'intégration installée sur la prod si besoin).
- Fréquence réelle des cas en prod (logs).
