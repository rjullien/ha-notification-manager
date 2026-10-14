# Analyse des bugs Alexa (branche fix/alexa-bugs)

Date : 14 octobre 2026

## Bug 1 : `asyncio.gather` sans `return_exceptions=True` dans `_async_restore_volumes`

### Problème

Dans `alexa.py`, la fonction `_async_restore_volumes` fait un `asyncio.gather` sur les tâches `restore_one` sans `return_exceptions=True`. Chaque `restore_one` fait un `volume_set` bloquant, et en cas d'échec il attend `ALEXA_RESTORE_RETRY_DELAY` puis retente.

Si une tâche lève une exception autre qu'une `CancelledError` — par exemple une erreur non catchée du service `volume_set` — le `gather` propage l'exception immédiatement et les autres enceintes ne sont jamais restaurées.

### Fix proposé

Passer `return_exceptions=True` au `gather`, puis itérer sur les résultats pour logger chaque échec sans bloquer les autres :

```python
results = await asyncio.gather(
    *(restore_one(target) for target in targets),
    return_exceptions=True,
)
for result in results:
    if isinstance(result, Exception):
        _logger.error("Restore failed: %s", result)
```

Cela garantit que toutes les enceintes sont tentées même si l'une plante.

## Bug 2 : Re-restore jobs planifiés après le `finally` dans `_async_send_alexa`

### Problème

Dans `_async_send_alexa`, le bloc `try` envoie le TTS et attend le `post_tts_delay`. Le `finally` restaure les volumes. Juste après le `finally`, si l'entry est encore `LOADED`, on schedule les `_async_rerestore` avec `async_call_later` à `ALEXA_RERESTORE_DELAY`.

Sauf que si une `CancelledError` est levée dans le `try` — par exemple un unload de Home Assistant — le handler log, relance, et le `finally` s'exécute, mais le code après le `finally` ne s'exécute jamais. Résultat : les jobs de re-restore ne sont pas créés, et une enceinte qui reste bloquée au volume TTS après un arrêt de HA n'est corrigée que par le boot recovery soixante secondes plus tard.

### Fix proposé

Deux options :

1. Déplacer le scheduling des re-restore à l'intérieur du `finally`, juste après la restauration des volumes. Comme ça, même en cas de `CancelledError`, les jobs sont créés.
2. Accepter le boot recovery comme seule filet de sécurité, pour garder une logique plus simple.

Le choix dépend de si tu préfères un re-restore immédiat ou une logique plus simple.

## Note

Le code n'a pas été modifié sur cette branche : c'est de la documentation uniquement pour que les agents comprennent le contexte avant d'implémenter.
