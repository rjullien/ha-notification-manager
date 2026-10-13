# Revue du travail Devin sur les lots A–D — Kiro (13 oct. 2026)

Revue des 6 PRs issues de `BUG_FIX_PLAN.md`. **Tout est mergé** : `main` est en
1.10.0 à `e405890`, CI verte (`Run Tests 3.11`, `Run Tests 3.12`, `Validate`).

| PR | Lot | Volume | État |
|---|---|---|---|
| #19 | docs + fix Bearer WhatsApp | docs + 5 l. | mergée |
| #20 | A — garantie de restore volume | +478/-68 | mergée |
| #21 | B — messagerie / watchdog | +266/-31 | mergée |
| #22 | C — persistance + recovery boot | +545/-8 | mergée |
| #23 | D — validation config | +447/-44 | mergée |
| #24 | bump 1.10.0 | — | mergée |

Tests : 158 → **190** définitions `def test_`.

Méthode : diffs lus **contre la base de merge** (un `git diff main..branche` sur
une branche empilée affiche de faux « reverts »), affirmations sur HA vérifiées
contre les sources upstream (`config_entries.py`, `helpers/storage.py`), état
final de `main` vérifié fichier par fichier. La suite de tests n'a **pas** été
exécutée ici (`pytest` absent du sandbox) ; l'état CI vient de l'API GitHub.

---

## 1. Vérification de l'état mergé

Les branches étaient empilées sur une base antérieure à #19 et #20/#21 ont été
**squash-mergées**, ce qui fait perdre l'identité des commits partagés. Le
risque était qu'un merge « propre » reverte silencieusement un lot antérieur.
Il ne s'est pas matérialisé — contrôlé un par un sur `main` :

| Apport | Origine | Présent sur `main` |
|---|---|---|
| `Authorization: Bearer` sur le chemin d'envoi | #19 | oui |
| `target_cfg.get("mobile")` (cible Telegram seule) | B | oui |
| `_WhatsAppSendResult` + classification 4xx | B | oui |
| Chat IDs watchdog depuis `_get_runtime_config` | B | oui |
| `vol.Coerce(int)` sur `whatsapp_bridge_logs` | B | oui |
| `_player_available` (3 sites) | A | oui |
| `Store` + `_async_boot_recovery` + service | C | oui |

Rien n'a été perdu dans la résolution du conflit de #22. Le `merge-tree` que
j'avais calculé conflictait bien sur 5 fichiers, mais comme le code des deux
versions du lot A était identique, l'arbitrage était mécanique.

Les deux bloquants que j'avais remontés sont donc **résolus** : #22 et #23 ont
été retargetées sur `main`, la CI a tourné, et `main` est cohérent.

---

## 2. Ce qui est bien fait

- **Les corrections de la méta-revue ont été reprises intelligemment**, pas
  appliquées mécaniquement : `_player_available()` est un helper partagé par les
  **trois** sites `unknown` — dont `_make_alexa_resolver`, que le plan avait
  oublié — le restore est passé en `gather`, le retry a gagné un
  `ALEXA_RESTORE_RETRY_DELAY`, et l'asymétrie des tâches est la bonne : **FR en
  `entry.async_create_task`** (attendue à l'unload), **EN en
  `async_create_background_task`** (annulée).
- **Le lot C tranche mieux que la proposition d'origine** : marqueur « cycle en
  cours » supprimé, `async_delay_save` conservé pour le seul `last_good`,
  service `restore_alexa_volumes` ajouté.
- **`BUG_FIX_PLAN.md` a été corrigé au lieu d'être laissé périmé** : l'item A2
  dit maintenant que HA *attend* les tâches d'entrée et *annule* les tâches
  background, avec la note « la version initiale disait “HA annule”, c'était
  faux », et le lot C documente explicitement l'abandon du marqueur. C'est le
  bon réflexe : le plan reste la source de vérité pour le prochain agent.
- **La validation des données du `Store` est sérieuse** : rejet explicite des
  `bool` (piège de `isinstance(x, int)`), `math.isfinite`, bornes 0–1, filtre
  `entity_id not in allowed_players` — ce qui règle au passage la purge du cache.
- `_async_require_admin` lève bien `Unauthorized` et laisse passer les appels
  sans contexte utilisateur (automatisations).
- Les docs sont sorties du répertoire livré par HACS (`docs/`, `docs/grok-tesla/`).

---

## 3. Ce qui reste ouvert sur `main` @ 1.10.0

### 3.1 `_async_restore_volumes` : `gather` sans `return_exceptions=True`

`alexa.py:146`. `restore_one` attend maintenant
`asyncio.sleep(ALEXA_RESTORE_RETRY_DELAY)` quand un `volume_set` échoue. Avant
le lot A, le chemin de restore n'avait aucune suspension autre que les appels de
service eux-mêmes ; désormais une annulation pendant ce `sleep` propage dans le
`gather` nu et **annule les restores des autres enceintes**. Une seule enceinte
en échec pendant un arrêt de HA laisse donc les autres à 0,7.

**Fix** : `return_exceptions=True` sur ce `gather`.

### 3.2 Les jobs de re-restore sont planifiés après le `finally`

`alexa.py:455-463` :

```python
except asyncio.CancelledError:
    _LOGGER.warning("Alexa TTS cycle cancelled — restoring volumes for %s", targets)
    raise
finally:
    await _async_restore_volumes(hass, original_volumes)

for entity_id, resting in original_volumes.items():   # jamais atteint si annulé
    unsubs[entity_id] = async_call_later(...)
```

Le `raise` relance après le `finally`, donc la planification est sautée : pas de
filet différé dans le cas précis où le restore a le plus de chances d'avoir
échoué. Portée limitée — à l'arrêt complet un job à +20 s ne tournerait pas de
toute façon — mais le README promet plus que ce que le code fait.

**Fix** : planifier les jobs dans le `finally`, après le restore.

### 3.3 `async_delay_save` est une fenêtre glissante, pas un plafond

`homeassistant/helpers/storage.py` :

```python
next_when = self.hass.loop.time() + delay
if self._delay_handle and self._delay_handle.when() < next_when:
    self._next_write_time = next_when
    return
```

Chaque appel **repousse** l'écriture de `ALEXA_VOLUME_SAVE_DELAY` (10 s), et
`_async_send_alexa` l'appelle à chaque cycle. Une maison qui annonce plus
souvent que toutes les 10 s peut différer l'écriture indéfiniment : la fenêtre
de perte sur crash n'est bornée par rien. Peu grave pour un `last_good` stable,
et ça renforce le choix d'avoir abandonné le marqueur — mais la nuance n'est
documentée nulle part dans le dépôt (elle ne vit que dans le corps de la PR #22,
qui l'annonce comme bornée à 10 s).

### 3.4 Scories

- `__init__.py:233` : `handle_restore_alexa_volumes` résout
  `active = _get_primary_entry(hass)`, log une erreur si `None`, puis ne
  l'utilise jamais — `_async_restore_saved_volumes(hass)` ne prend pas d'entry.
  Garde morte copiée par analogie avec `handle_notify`.
- `DATA_ALEXA_GENERATIONS` n'est jamais purgé ; `last_good` ne l'est qu'au
  **chargement** depuis le store (filtre `allowed_players`), pas en mémoire.
- Le test d'annulation (`tests/test_alexa_tts.py`) injecte
  `side_effect=asyncio.CancelledError` sur un mock : il prouve que le `finally`
  tourne quand l'exception traverse le corps, pas la sémantique réelle d'un
  `task.cancel()`. Il ne couvrirait ni §3.1 ni §3.2.

---

## 4. Verdict

Du travail d'ingénieur solide : les revues sont lues, vérifiées dans le code,
tranchées en expliquant, et le plan est corrigé quand il s'avère faux plutôt que
laissé en place. Les deux seuls problèmes réellement bloquants étaient de la
mécanique de PRs empilées (branches non rebasées après squash-merge, donc CI
jamais déclenchée), et ils sont réglés.

Reste à traiter, par ordre de valeur : §3.1 (une ligne, évite un restore
partiel), §3.2 (déplacer une boucle), §3.3 (une phrase de doc), §3.4 au fil de
l'eau. Rien qui justifie de bloquer 1.10.0.

Non vérifié ici, et qui ne peut l'être que sur l'instance : le comportement réel
du recovery au démarrage (60 s après `EVENT_HOMEASSISTANT_STARTED`) et la
fréquence des cas A/B/C en prod. Un smoke test vaudrait le coup — un notify TTS,
un reload d'entrée pendant l'annonce, un restart pendant l'annonce.
