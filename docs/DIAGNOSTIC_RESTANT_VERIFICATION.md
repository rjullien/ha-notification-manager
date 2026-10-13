# Vérification du diagnostic Tesla — Kiro (13 oct. 2026)

Contre-vérification de `docs/DIAGNOSTIC_RESTANT.md`, affirmation par affirmation,
contre `main` @ 1.10.0 (`e405890`). Les numéros de ligne ci-dessous sont ceux de
`main`.

**Résultat : 4 des « manques » sont déjà livrés, dont la priorité n°1.**
L'inventaire du livré (lot A en particulier) est en revanche exact.

---

## 1. Faux manques

### §1 — B2 non-retry 4xx : implémenté

Le diagnostic conclut « aucune trace de cette logique dans `messaging.py` », le
classe en priorité n°1 et propose de relire les lignes ~237–275. Les trois
sous-items sont sur `main` :

| Sous-item du plan | Emplacement |
|---|---|
| pas de retry sur 4xx sauf 408/429 | `messaging.py:291-295` — `rejected = (400 <= resp.status < 500 and resp.status not in (408, 429))` |
| alerte distinguant refus et indisponibilité | `messaging.py:254-256` — `reason = f"message refusé par le bridge (HTTP {status_text})"` vs `"bridge indisponible"` |
| message vide → warning sans envoi | `messaging.py:203` — `_LOGGER.warning("WhatsApp message is empty — skipping send")` |

Le transport de l'information est le dataclass `_WhatsAppSendResult`
(`messaging.py:30`, `refused_status`), livré par le lot B (#21).
**Appliquer la priorité n°1 réécrirait du code fonctionnel.**

### §3 — Télémétrie A3 / A6 : implémentée

Le diagnostic dit « rien de tel n'apparaît dans le code ». Les trois warnings
existent :

- `alexa.py:457` — `"Alexa TTS cycle cancelled — restoring volumes for %s"` (A3)
- `alexa.py:258` — `"Alexa %s still at TTS volume %.2f %ds after restore — re-restoring to %.2f"` (A6)
- `alexa.py:210` — `"Alexa %s at TTS volume %.2f after HA start — restoring %.2f"` (recovery boot)

Détail de forme : le diagnostic écrit `_LOGGING.warning`. `_LOGGING` était un
renommage parasite annulé ; la convention HA et celle du dépôt est `_LOGGER`.

### §2 — `update_config` : déjà supprimé

`git grep "def update_config" origin/main -- custom_components/` ne renvoie
rien. Retiré par le lot D (#23).

### §2 — `alexa_tts_volume = 0.0` : déjà interdit

`config_flow.py:418` — `if not 0.0 < alexa_tts_volume <= 1.0`. C'est l'item D2
du lot D, que l'inventaire du lot D ne mentionne pas (il n'en liste que 2 sur 4).
Un `0` déjà enregistré reste lu tel quel au runtime : c'est un écart assumé
entre le flow et le runtime, pas un manque.

---

## 2. Affirmation surévaluée

### §4 — « aucun test ne vérifie qu'une valeur sauvée est bien rechargée »

Le `_make_store` de `tests/conftest.py:32-37` est effectivement inerte
(`async_delay_save = MagicMock()`). Mais `tests/test_alexa_volume_persistence.py`
le remplace par un `FakeStore` local qui capture la charge utile :

```python
def async_delay_save(self, data_func, delay):
    self.delayed_saves.append((data_func, delay))
```

Les deux côtés sont couverts :
`test_setup_merges_valid_cache_once_and_purges_invalid_players` pour le
chargement (fusion + purge des players absents de `alexa_players`), et
`test_alexa_cycle_schedules_delayed_snapshot` pour l'écriture.

Ce qui manque réellement est plus étroit : **l'aller-retour de bout en bout à
travers un vrai `Store`**. La réserve générale sur la couche `MagicMock` reste
valide.

---

## 3. Affirmations confirmées

- **Watchdog `unknown` / cooldown 6 h** : réel. `watchdog.py:131` alerte sur
  `state is None or state.state in ("unavailable", "unknown")`, avec un cooldown
  de 6 h (`:144`) et sans extinction. Le lot D n'a changé que le libellé
  (`introuvable` vs `unavailable`, `:152`), pas la décision. Question ouverte.
- **Config vide impossible** : `_get_runtime_config` fait bien `d.get(x) or const`.
- **Chemin EN** : toujours présent, hors verrou, sans couverture dédiée.
- **Dette docs** `docs/grok-tesla/` et **smoke test manuel manquant** : valides.

---

## 4. Défauts présents dans le code relu, absents du diagnostic

Voir `docs/REVIEW_DEVIN_LOTS_KIRO.md` (PR #25) et l'issue #26 :

1. **`_async_restore_volumes` : `gather` sans `return_exceptions=True`**
   (`alexa.py:146`). Le diagnostic décrit A7 comme « `asyncio.gather` + attente
   1 s avant le retry » sans relever que cette attente est un nouveau point
   d'annulation : une annulation pendant ce `sleep` propage dans le `gather` nu
   et annule les restores des autres enceintes.
2. **Les jobs de re-restore sont planifiés après le `finally`**
   (`alexa.py:455-463`), donc le `raise` du handler `CancelledError` les saute.
   Le diagnostic décrit A6 comme « annulé au unload » sans voir qu'il n'est
   jamais planifié sur le chemin annulé.
3. **`Store.async_delay_save` est une fenêtre glissante**, pas un plafond :
   chaque appel repousse l'écriture, donc la fenêtre de perte sur crash n'est
   pas bornée par les 10 s de `ALEXA_VOLUME_SAVE_DELAY`.

Compte de tests : le diagnostic annonce « ~169+ » ; il y a **190** définitions
`def test_` sur `main`.

---

## 5. Méthode

La cause commune des quatre faux manques est assumée dans le texte : « Ma
recherche de code n'a trouvé aucune trace ». Une recherche négative ne prouve
l'absence que si l'arbre interrogé est explicite — ici les lots B et D étaient
mergés depuis plusieurs heures. Un `git grep` sur `origin/main` plutôt que sur
une copie locale lève les quatre d'un coup.
