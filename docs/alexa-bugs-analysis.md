# Analyse des bugs Alexa restants

Date : 14 octobre 2026
Branche : fix/alexa-bugs

## Bug 1 : `asyncio.gather` sans `return_exceptions=True` dans `_async_restore_volumes`

### Problème

Dans `alexa.py`, la fonction `_async_restore_volumes` fait un `asyncio.gather` sur les tâches `restore_one` sans passer `return_exceptions=True`. Chaque `restore_one` fait un `volume_set` bloquant, et si ça échoue il attend `ALEXA_RESTORE_RETRY_DELAY` puis retente.

Si une tâche lève une exception autre qu'une `CancelledError` — par exemple une erreur non catchée du service `volume_set` — le `gather` propage l'exception immédiatement et les autres enceintes ne sont jamais restaurées.

### Fix proposé

Passer `return_exceptions=True` au `gather`, puis itérer sur les résultats pour logger chaque échec sans bloquer les autres :

```python
results = await asyncio.gather(
    *(restore_one(target, volume) for target, volume in targets),
    return_exceptions=True,
)
for result in results:
    if isinstance(result, Exception):
        _logger.error("Restore failed: %s", result)
```

Ça garantit que toutes les enceintes sont tentées même si l'une plante.

## Bug 2 : jobs de re-restore planifiés après le `finally` dans `_async_send_alexa`

### Problème

Dans `_async_send_alexa`, le bloc `try` envoie le TTS et attend le `post_tts_delay`. Le `finally` restaure les volumes. Juste après le `finally`, si l'entry est encore `LOADED`, on schedule les `_async_rerestore` avec `async_call_later` à `ALEXA_RERESTORE_DELAY`.

Sauf que si une `CancelledError` est levée dans le `try` — par exemple un unload de Home Assistant — le handler log, relance, et le `finally` s'exécute, mais le code après le `finally` ne s'exécute jamais. Résultat : les jobs de re-restore ne sont pas créés, et une enceinte qui reste bloquée au volume TTS après un arrêt de HA n'est corrigée que par le boot recovery soixante secondes plus tard.

### Options

1. Déplacer le scheduling des re-restore à l'intérieur du `finally`, juste après la restauration des volumes.
2. Accepter le boot recovery comme seul filet de sécurité.

Le choix dépend de si tu préfères un re-restore immédiat ou une logique plus simple.

## Note

Le boot recovery à soixante secondes couvre déjà le cas d'un arrêt de HA en pleine annonce, donc l'option 2 est acceptable en attendant une décision.
