# Analyse des bugs Alexa — restore volumes

Date : 14 octobre 2026
Branche : fix/alexa-bugs

## Bug 1 — `asyncio.gather` sans `return_exceptions=True` dans `_async_restore_volumes`

### Problème

Dans `alexa.py`, la fonction `_async_restore_volumes` fait un `asyncio.gather` sur les tâches `restore_one` sans passer `return_exceptions=True`. Chaque `restore_one` fait un `volume_set` bloquant, et en cas d'échec il attend `ALEXA_RESTORE_RETRY_DELAY` puis retente.

Si une tâche lève une exception autre qu'une `CancelledError` — par exemple une erreur non catchée du service `volume_set` — le `gather` propage l'exception immédiatement et les autres enceintes ne sont jamais restaurées.

### Fix proposé

Passer `return_exceptions=True` au `gather`, puis itérer sur les résultats pour logger chaque échec sans bloquer les autres :

```python
results = await asyncio.gather(
    *(restore_one(target, generation) for target in targets),
    return_exceptions=True,
)
for result in results:
    if isinstance(result, Exception):
        _logger.error("Restore failed: %s", result)
```

Ça garantit que toutes les enceintes sont tentées même si l'une plante.

## Bug 2 — Re-restore jobs planifiés après le `finally` dans `_async_send_alexa`

### Problème

Dans `_async_send_alexa`, le bloc `try` envoie le TTS et attend le `post_tts_delay`. Le `finally` restaure les volumes. Juste après le `finally`, si l'entry est encore `LOADED`, on schedule les `_async_rerestore` avec `async_call_later` à `ALEXA_RERESTORE_DELAY`.

Sauf que si une `CancelledError` est levée dans le `try` — par exemple un unload de Home Assistant — le handler log, relance, et le `finally` s'exécute, mais le code après le `finally` ne s'exécute jamais. Résultat : les jobs de re-restore ne sont pas créés, et une enceinte qui reste bloquée au volume TTS après un arrêt de HA n'est corrigée que par le boot recovery soixante secondes plus tard.

### Options

1. Déplacer le scheduling des re-restore à l'intérieur du `finally`, juste après la restauration des volumes.
2. Accepter le boot recovery comme seule filet de sécurité.

Le choix dépend de si on préfère un re-restore immédiat ou une logique plus simple.

## Note 3 — `async_delay_save` en fenêtre glissante (non prioritaire)

### Problème

Le `async_delay_save` retarde l'écriture disque de dix secondes après le dernier changement, mais c'est une fenêtre glissante : chaque nouveau changement repousse le timer. En cas de crash brutal (coupure de courant forcée, kill -9), l'état en mémoire peut sauter avant l'écriture, et on perd plus de dix secondes de changements de config.

### Décision

Pas de fix prévu. Le setup est stable : le serveur ne s'éteint pas même en cas de coupure de courant grâce à une sécurisation dédiée. Un reboot propre est géré par le boot recovery. Le risque résiduel ne concerne que les crashs vraiment brutaux, dont la probabilité est jugée négligeable.

## Note 4 — Optimisation : early return quand aucun player Alexa n'est déclaré (cas P3 sans Alexa)

### Contexte

Le composant tourne sur un Raspberry Pi 3, très limité en mémoire et en CPU. Sur une instance sans Alexa déclarée (alexa_players vide), le setup fait quand même tout le chemin Alexa : création du lock, des dicts de volumes (last_good, generations, rerestore unsubs), du store disque, du log d'émissions, et planification du boot recovery. Le watchdog démarre aussi même avec des listes vides — il se contente de logger et de ne rien faire.

### Proposition

Dans `async_setup_entry`, si `alexa_players` est vide :

1. Sauter l'initialisation du store Alexa, du boot recovery et des dicts de volumes associés.
2. Ne pas démarrer le watchdog s'il n'y a aucune entité monitorée (WATCHDOG_ENTITIES et WATCHDOG_CRITICAL_ENTITIES vides) — ou le garder si d'autres entités hors Alexa sont monitorées.

Le resolver Alexa et le log d'émissions restent utiles pour les consommateurs externes, donc on peut les garder, mais le reste est du poids mort sur une machine petite.

### Impact attendu

Gain modeste mais réel au boot : moins d'allocations, pas de lecture disque du store, pas de timer planifié inutile. Sur un P3 c'est le genre de détail qui compte sur la durée.

## Notes générales

- Le boot recovery à soixante secondes couvre déjà le cas du bug 2, donc c'est un fallback acceptable.
- Le bug 1 est le plus sérieux des deux : une exception inattendue sur une seule enceinte peut laisser toutes les autres au volume TTS.
- Le bug 3 est documenté pour traçabilité mais explicitement hors scope.
- La note 4 est une optimisation, pas un bug : le composant fonctionne sans, mais c'est du gaspillage de ressources sur du matériel limité.
