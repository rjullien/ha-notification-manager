# Analyse des bugs Alexa — restore volumes

Date : 14 octobre 2026
Branche : fix/alexa-bugs

## Bug 1 : `asyncio.gather` sans `return_exceptions=True` dans `_async_restore_volumes`

### Contexte

Dans `alexa.py`, la fonction `_async_restore_volumes` fait un `asyncio.gather` sur les tâches `restore_one` pour chaque enceinte Alexa. Chaque `restore_one` fait un `volume_set` bloquant, et en cas d'échec il attend `ALEXA_RESTORE_RETRY_DELAY` (une seconde) puis retente.

### Problème

Sans `return_exceptions=True`, si une tâche lève une exception autre qu'une `CancelledError` — par exemple une erreur non catchée du service `volume_set` — le `gather` propage l'exception immédiatement. Les autres enceintes ne sont alors jamais restaurées : elles restent bloquées au volume TTS.

### Fix proposé

```python
results = await asyncio.gather(
    *(restore_one(entity_id) for entity_id in targets),
    return_exceptions=True,
)
for entity_id, result in zip(targets, results):
    if isinstance(result, Exception):
        _logger.error(
            "Échec restauration volume pour %s : %s", entity_id, result
        )
```

Ça garantit que toutes les enceintes sont tentées même si l'une plante, et les échecs sont loggés au lieu de faire échouer le cycle entier.

### Note

Le code de ce fix n'est pas inclus ici volontairement : à implémenter manuellement pour garder le contrôle sur le diff.

---

## Bug 2 : jobs de re-restore planifiés après le `finally` dans `_async_send_alexa`

### Contexte

Dans `_async_send_alexa`, le bloc `try` envoie le TTS et attend le `post_tts_delay`. Le `finally` restaure les volumes. Juste après le `finally`, si l'entry est encore `LOADED`, on schedule les `_async_rerestore` avec `async_call_later` à `ALEXA_RERESTORE_DELAY` (vingt secondes).

### Problème

Si une `CancelledError` est levée dans le `try` — par exemple lors d'un unload de Home Assistant — le handler la log, la relance, et le `finally` s'exécute bien (restauration des volumes). Mais le code situé après le `finally` ne s'exécute jamais : les jobs de re-restore ne sont donc pas créés.

Conséquence : une enceinte qui reste bloquée au volume TTS après un arrêt de HA n'est corrigée que par le boot recovery, soixante secondes plus tard. C'est un fallback acceptable, mais pas idéal.

### Options de fix

1. **Déplacer le scheduling des re-restore à l'intérieur du `finally`**, juste après la restauration des volumes. Avantage : les jobs sont créés même en cas d'annulation. Inconvénient : le `finally` devient plus chargé et moins lisible.
2. **Accepter le boot recovery comme seule filet** et documenter ce comportement. Avantage : logique plus simple. Inconvénient : latence de soixante secondes dans le pire cas.

Le choix dépend de si tu préfères un re-restore immédiat ou une logique plus simple.

### Note

Comme pour le bug 1, le code n'est pas inclus : à implémenter selon le choix retenu.
