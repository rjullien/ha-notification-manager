# Plan de correction des bugs — méta-analyse (12 oct. 2026)

Fusion des analyses existantes (Grok Tesla, Grok Bot, spec révisée, ma critique, puis méta-revue Kiro), confrontée au code de `main` @ 1.9.6 et complétée par un audit complet des 16 modules. Toutes les références `fichier:ligne` ont été vérifiées sur `main`.

## 1. Analyse de l'analyse

| Source | Ce qui tient | Ce qui ne tient pas |
|---|---|---|
| **Grok Tesla** (Partie 1 de `REVIEW_NOTES_ALEXA_VOLUME.md`) | Diagnostic des cas A–J juste : volume bloqué à 0.7 sur annulation, reload, restart, cache empoisonné | Remède surdimensionné (une file par Echo, heuristiques de détection « 0.7 volontaire ») — trop de code pour le gain |
| **Grok Bot** (Partie 2) | Batching amont alexapy (`queue_delay` 1.5 s, confirmé doc alexapy), échecs Amazon silencieux → re-restore systématique, squash du fix WhatsApp | Recommande `asyncio.shield` dans le `finally` : faux — le restore sort du verrou si la tâche est déjà annulée |
| **Spec révisée** (Partie 3) | Bonne forme : PR minimale puis persistance | 5 trous (shield, courses du re-restore, recovery au boot, couplage des deux règles du cache, emplacement du plancher) — détaillés dans `REVIEW_NOTES_ALEXA_VOLUME_DEVIN.md` |
| **`TODO_BUGS.md` + 5 notes de migration** | Tous les items sont corrigés sur `main` (vérifié un par un) | — historique seulement |
| **Ma critique** (`REVIEW_NOTES_ALEXA_VOLUME_DEVIN.md`) | Corrections confirmées par le code | Limitée au volume Alexa, comme toutes les analyses précédentes. Non vérifié : mise à jour optimiste de `volume_level` par alexa_media, fréquence réelle des cas en prod |

**Angle mort commun :** toutes les analyses ont porté sur le volume Alexa. L'audit complet trouve 4 bugs côté messagerie/watchdog que personne n'avait relevés (section 3, lot B).

## 2. Priorités

| Lot | Contenu | Impact | Taille |
|---|---|---|---|
| **A** | Volume Alexa ne reste plus bloqué (hors restart) | Fort, fréquent — enceintes à 0.7 | ~80 lignes + tests |
| **B** | Messagerie et watchdog | Moyen — messages perdus ou fausses alertes | ~40 lignes + tests |
| **C** | Persistance volume + recovery au boot | Fort mais rare (restart en plein TTS) | ~100 lignes + tests |
| **D** | Validation config + nettoyage | Faible — erreurs de saisie | variable |

Ordre conseillé : **A → B → C → D**. Documents sources déplacés dans `docs/` (hors du répertoire livré par HACS). A et B sont indépendants (fichiers disjoints sauf `notify.py`), donc parallélisables ; C dépend de A.

## 3. Détail

### Lot A — volume Alexa (une PR)

L'ordre compte : A1 doit précéder A2, sinon annuler les tâches au unload casse le verrou.

- **A1 — Le verrou et le cache survivent au reload.** `__init__.py:199` fait `hass.data.pop(DOMAIN)` au dernier unload, ce qui perd `_alexa_lock`, `_alexa_last_good_volumes` et `_alexa_emissions`. Chaque reconfigure recharge l'entrée (`_async_update_listener`) : nouveau verrou pendant qu'un cycle tient encore l'ancien → deux cycles en parallèle → le second sauvegarde 0.7 comme volume de repos. Fix : au dernier unload, retirer seulement les sessions bridge et les services, garder les 3 clés partagées (`setdefault` au setup les réutilise déjà).
- **A2 — Tâches liées à l'entrée.** `notify.py:79-137` : `hass.async_create_task` → `entry.async_create_task` pour FR, téléphone, WhatsApp et groupe ; **EN → `entry.async_create_background_task`**. Au unload, HA **attend** les tâches d'entrée (≤ 10 s, `_async_process_on_unload`) — le cycle FR en vol finit et restaure — et **annule** les tâches background (EN, sans état volume). Effet assumé : un reload pendant une annonce attend jusqu'à ~8 s. *(Corrigé après la méta-revue Kiro : la version initiale disait « HA annule », c'était faux.)*
- **A3 — Restore garanti.** `alexa.py:241-296` : `try/finally` autour des étapes 2–5, `await _async_restore_volumes(...)` **sans** `asyncio.shield` (le restore reste sous le verrou). Couvre l'arrêt de HA (annulation) et les erreurs — le reload, lui, est couvert par A2.
- **A7 — Restore parallèle + retry avec délai.** `_async_restore_volumes` (`alexa.py:117-128`) restaure en série et réessaie sans délai. Passer en `gather` (comme la montée) et attendre 1 s avant le retry. *(Kiro §2.3/§3.1.)*
- **A4 — Cache sain.** `alexa.py:267` : ne plus écrire `last_good` quand `reported is None` (sinon 0.5 par défaut écrase la vraie valeur). Un helper `_player_available` partagé par les **trois** sites : filtre amont (`:222-225`), boucle (`:248`) et résolveur public `_make_alexa_resolver` (`:149`).
- **A5 — Plancher du délai.** `asyncio.sleep(max(delay, 2))` à `alexa.py:293` ; `0` reste accepté en config (test `test_zero_volume_and_delay_preserved` conservé).
- **A6 — Re-restore différé.** Un job ~20 s après le restore, par Echo, avec **compteur de génération** (incrémenté au début de chaque cycle) : le job ne s'exécute que si aucune nouvelle génération n'a démarré, et seulement si le volume lu n'est ni le repos ni déjà corrigé. Couvre les échecs Amazon silencieux. Annulé au unload (`entry.async_on_unload`).

Tests à ajouter : annulation pendant le sleep → restore appelé ; unload puis reload → même objet verrou ; `reported is None` → `last_good` inchangé ; `unknown` filtré ; délai 0 → sleep(2) ; job re-restore sauté si nouvelle génération.

### Lot B — messagerie et watchdog (une PR, aucun ne figurait dans les analyses précédentes)

- **B1 — Cible téléphone sans `mobile` → Telegram perdu.** `messaging.py:57-58` : `target_cfg["mobile"]` lève `KeyError` (et `split(".", 1)` lève `ValueError` si la valeur n'a pas de point) **avant** l'envoi Telegram. Une personne configurée Telegram seul ne reçoit donc rien. Fix : `mobile = target_cfg.get("mobile")`, push seulement si valide, Telegram dans tous les cas. Latent aujourd'hui (les 4 cibles de prod ont un `mobile`), mais la config UI l'autorise.
- **B2 — Fausse alerte « bridge indisponible » sur une erreur client.** `messaging.py:237-275` : un HTTP 4xx (message vide, numéro invalide, 401) est retenté 3 fois (≈ 6 s de backoff) puis déclenche l'alerte Telegram « WhatsApp bridge indisponible ». Cas concret : un notify WhatsApp avec `message_tel` vide (documenté comme transmis tel quel). Fix : pas de retry sur 4xx (sauf 408/429), alerte qui distingue « bridge injoignable » de « message refusé (HTTP n) », et `message` vide → warning + rien n'est envoyé.
- **B3 — Le watchdog ignore les chat IDs configurés dans l'UI.** `watchdog.py:187` lit `BRIDGE_ALERT_CHAT_IDS` (const) au lieu de `_get_runtime_config(entry)["bridge_alert_chat_ids"]`. Une modification via reconfigure n'est jamais prise en compte par le watchdog. Fix : fallback runtime config.
- **B4 — `whatsapp_bridge_logs` refuse `limit` en chaîne.** `bridge_services.py:54` : `vol.All(int, …)` → appel depuis l'UI Outils dev (`"100"`) rejeté. Fix : `vol.Coerce(int)`.

### Lot C — persistance (une PR, après A)

Design revu après la méta-revue Kiro §2.2 : un marqueur « cycle en cours » n'a de sens que s'il survit à un crash, donc il exigerait un `async_save()` immédiat (une écriture SD par annonce, sous le verrou) — `async_delay_save` n'est flushé qu'à l'arrêt propre. **Pas de marqueur.**

- Persister `last_good` dans un `Store` (`async_delay_save`, il bouge rarement), purgé au chargement des players absents de `alexa_players` (Kiro §3.4). Le délai de 10 s est un debounce : chaque appel repousse l’écriture, donc des annonces espacées de moins de 10 s la différeront; HA flush à l’arrêt propre. Un crash ne perd que les derniers volumes de repos, et la récupération au démarrage relit de toute façon les volumes courants.
- Service `notification_manager.restore_alexa_volumes` (admin) : remet chaque player connu à son `last_good`.
- Au démarrage (`EVENT_HOMEASSISTANT_STARTED` + délai pour qu'alexa_media remonte les volumes) : pour chaque player, restaurer **seulement si** la lecture ≈ volume TTS et `last_good` ≠ volume TTS. La lecture est fraîche (alexa_media réinterroge Amazon, l'état n'est pas restauré par HA), et si le volume de repos voulu est 0.7, `last_good` vaut 0.7 → rien n'est touché.
- Mettre à jour le README (§ « volumes in-memory, restore manuel après restart »).

### Lot D — validation et nettoyage (au fil de l'eau)

- **Validation structurelle du reconfigure** : `_parse_json` (`config_flow.py:46`) ne vérifie que la syntaxe. `"kitchen"` ou `[1, 2]` pour `alexa_players`, ou une cible sans `mobile`/`telegram_chat_id`, passent et cassent au runtime. Câbler `alexa_players_validation.py` (orphelin de `test-pr-access`) et un équivalent pour `phone_targets` / `whatsapp_contacts`.
- **Config vide impossible** : `_get_runtime_config` fait `d.get(x) or const` — sauvegarder `[]` ou `{}` revient silencieusement au `const_private`. À documenter, ou passer à `is not None`.
- **Watchdog** : une entité supprimée (`state is None`) ou légitimement `unknown` alerte toutes les 6 h sans fin. Décider : alerter sur `unknown` ou non, et signaler une seule fois une entité absente.
- **Coordinator** : `coordinator.py:287` n'attrape que `ClientConnectorError`/`TimeoutError` ; un `ServerDisconnectedError` passe en `UpdateFailed` (capteur `unavailable` au lieu de `disconnected`). Attraper `aiohttp.ClientError`. `update_config` est du code mort (l'entrée est rechargée à chaque modification).
- **`alexa_tts_volume = 0.0`** donne un TTS muet. Décider : interdire, ou faire de 0 « ne pas toucher au volume ».
- **Chemin EN** : supprimer, ou au minimum le passer sous verrou.

## 4. Vérification

- `python3 -m pytest tests/ -q` vert à chaque PR (169 tests aujourd'hui).
- Les tests reposent sur un `MagicMock` de HA : ils vérifient l'intention, pas la sémantique réelle de `entry.async_create_task` / `Store`. Après chaque déploiement, smoke test sur l'instance de test : un notify TTS (`notification_alexa` ciblé sur une seule enceinte), un reload d'entrée pendant le TTS, un restart pendant le TTS (lot C).
- Mesure manquante (question 8 de la spec) : fréquence réelle des cas A/B/C en prod. Le logger prod est en `warn` ; ajouter un `_LOGGER.warning` explicite quand A3 restaure après annulation et quand A6 corrige un volume, pour mesurer après déploiement.
