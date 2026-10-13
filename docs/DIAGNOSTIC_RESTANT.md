# Diagnostic — ce qui reste à fixer (post 1.10.0)

Date : 13 octobre 2026. Basé sur `main` @ 1.10.0 (e4058909) et le `BUG_FIX_PLAN.md` du 12 octobre.

## Ce qui est livré ✅

### Lot A — volume Alexa
- **A1** Verrou / cache / émissions survivent au reload : `__init__.py` ne fait plus `hass.data.pop(DOMAIN)` au dernier unload, les 3 clés partagées sont conservées.
- **A2** Tâches liées à l'entrée : FR / téléphone / WhatsApp / groupe en `entry.async_create_task`, EN en `entry.async_create_background_task` (`notify.py`). Comportement HA documenté : les tâches d'entrée sont attendues (≤ 10 s), les background annulées.
- **A3** Restore garanti : `try/finally` autour des étapes 2–5 de `_async_send_alexa`, `await _async_restore_volumes` **sans** `asyncio.shield` (le restore reste sous le verrou).
- **A4** Cache sain : `last_good` non écrit si `reported is None` ; helper `_player_available` partagé par le filtre amont, la boucle et `_make_alexa_resolver`.
- **A5** Plancher du délai : `asyncio.sleep(max(delay, 2))` ; `0` reste accepté en config.
- **A6** Re-restore différé : job ~20 s par Echo, compteur de génération (`DATA_ALEXA_GENERATIONS`), annulé au unload via `DATA_ALEXA_RERESTORE_UNSUBS`.
- **A7** Restore parallèle : `asyncio.gather` + attente 1 s avant le retry.

### Lot B — messagerie / watchdog
- **B1** `mobile = target_cfg.get("mobile")` : plus de KeyError, Telegram envoyé même sans mobile valide.
- **B3** Watchdog lit `bridge_alert_chat_ids` depuis le runtime config (fallback sur la const).
- **B4** `vol.Coerce(int)` sur `limit` de `whatsapp_bridge_logs`.
- **Coordinator** : `except (aiohttp.ClientError, TimeoutError)` — `ServerDisconnectedError` correctement classé `disconnected`.

### Lot C — persistance
- `last_good` persisté dans un `Store` (`async_delay_save`), purgé des players absents de `alexa_players`.
- Service admin `notification_manager.restore_alexa_volumes`.
- Recovery au boot : `EVENT_HOMEASSISTANT_STARTED` + délai, restauration seulement si lecture ≈ volume TTS et `last_good ≠ volume TTS`.
- README mis à jour (plus de « volumes in-memory »).

### Lot D — validation (PR #23)
- Validation structurelle du reconfigure JSON (plus de `"kitchen"` ou `[1,2]` acceptés).
- Traitement des champs cibles nuls comme absents.

## Ce qui manque / reste ouvert ⚠️

> **Contre-vérifié contre `main` @ 1.10.0 — voir `DIAGNOSTIC_RESTANT_VERIFICATION.md`.**
> Quatre items de cette section sont **déjà livrés** et ne doivent pas être
> réimplémentés : §1 (B2 non-retry 4xx, `messaging.py:203/254-256/291-295`),
> §3 (télémétrie A3/A6, `alexa.py:210/258/457`), et dans §2 `update_config`
> (supprimé) et `alexa_tts_volume = 0.0` (interdit, `config_flow.py:418`).
> §4 est surévalué : `test_alexa_volume_persistence.py` utilise son propre
> `FakeStore` qui capture la charge utile ; seul l'aller-retour de bout en bout
> manque. Le reste de la section est confirmé.

### 1. B2 — non-retry 4xx dans messaging.py (À VÉRIFIER)
Le plan prévoyait : pas de retry sur HTTP 4xx (sauf 408/429), alerte distinguant « bridge injoignable » de « message refusé (HTTP n) », et `message` vide → warning sans envoi. Ma recherche de code n'a trouvé **aucune trace** de cette logique dans `messaging.py`. Soit c'est dans un diff que je n'ai pas vu, soit c'est passé à la trappe. **Action : relire `messaging.py` lignes ~237–275 et les tests associés.**

### 2. Items D « au fil de l'eau » (ouverts par design)
- **Config vide impossible** : `_get_runtime_config` fait `d.get(x) or const` — sauvegarder `[]` ou `{}` revient silencieusement au `const_private`. À documenter ou passer à `is not None`.
- **Watchdog** : une entité supprimée (`state is None`) ou légitimement `unknown` alerte toutes les 6 h sans fin. Décision à prendre : alerter sur `unknown` ou non, et signaler une seule fois une entité absente.
- **`alexa_tts_volume = 0.0`** : TTS muet (montée à 0 puis restore). Décider : interdire, ou faire de 0 « ne pas toucher au volume ».
- **Chemin EN** : marqué deprecated mais toujours présent, hors verrou. Supprimer ou passer sous verrou.
- **`update_config`** : code mort (l'entrée est rechargée à chaque modification) — à supprimer.

### 3. Télémétrie prod manquante
Le `BUG_FIX_PLAN` recommandait d'ajouter un `_LOGGING.warning` explicite quand A3 restaure après annulation et quand A6 corrige un volume, pour mesurer la fréquence réelle des cas en prod. Rien de tel n'apparaît dans le code. Le logger prod est encore en `warn` générique.

### 4. Lacunes de tests
- Toute la couche HA est `MagicMock` : les tests verrouillent l'**intention**, pas la sémantique réelle de `entry.async_create_task`, `Store.async_delay_save`, ou des events HA.
- Le `Store` mocké (`_make_store` dans `conftest.py`) : `async_delay_save` est un `MagicMock` qui ne persiste rien — aucun test ne vérifie qu'une valeur sauvée est bien rechargée.
- **Smoke test manuel manquant** sur l'instance de test : un notify TTS ciblé sur une seule enceinte, un reload d'entrée pendant le TTS, un restart pendant le TTS.

### 5. Dettes mineures
- `docs/grok-tesla/` contient encore `AGENT_README.md`, les 5 notes de migration et `TODO_BUGS.md` — utiles pour la traçabilité mais à ne plus toucher (bandeaux « historique » déjà en place).
- Le chemin EN Alexa n'a pas de couverture de tests dédiée au-delà des sélecteurs.

## Priorité suggérée

1. ~~**B2** — vérifier/implémenter le non-retry 4xx (bug latent, impact moyen).~~ **Déjà livré** (lot B / #21).
2. ~~**Télémétrie A3/A6** — 2 lignes de log, gain de mesure immédiat.~~ **Déjà livré** (`alexa.py:210/258/457`).
3. **Décisions D** — config vide, watchdog unknown, volume 0.0, chemin EN : tranché une bonne fois.
4. **Smoke test** sur l'instance réelle avant de tagger une release.
5. Nettoyage `update_config` mort.

## Vérification

- `python3 -m pytest tests/ -q` vert à chaque PR (suite ~169+ tests aujourd'hui).
- Après déploiement : smoke test TTS + reload + restart sur l'instance de test.
