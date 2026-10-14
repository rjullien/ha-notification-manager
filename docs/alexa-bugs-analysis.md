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

## Note 5 — Principe général : initialisation conditionnelle par canal (optimisation P3)

### Principe

Le composant gère plusieurs canaux indépendants : Alexa, WhatsApp (bridge + coordinator + sensor), téléphone/Telegram, watchdog. Chacun a son empreinte mémoire et CPU. Sur un P3, on ne doit payer que pour ce qui est réellement configuré.

Règle : si un canal n'est pas déclaré dans la config, son empreinte mémoire et ses ressources ne doivent pas être allouées.

### Application concrète

- **Pas d'Alexa** (`alexa_players` vide) : pas de lock volume, pas de dicts last_good/generations/rerestore, pas de store disque, pas de boot recovery planifié. Le resolver et le log d'émissions peuvent rester (utiles aux consommateurs externes) mais tout le reste est du poids mort.
- **Pas de WhatsApp** (pas de bridge_url configuré) : pas de coordinator de polling, pas de sensor de statut bridge, pas de sessions HTTP dédiées au bridge. Le coordinator poll le endpoint /health toutes les cinq minutes même sans bridge utile — c'est du CPU et du réseau gaspillés.
- **Pas de téléphone/Telegram** : pas de phone_targets à résoudre, mais c'est déjà léger.
- **Watchdog** : ne démarre que s'il y a au moins une entité à monitorer.

### Impact attendu

Sur un P3 sans Alexa ni WhatsApp, le composant se réduit à un handler notify minimal : parse des arguments, dispatch éventuel. C'est l'idéal pour du matériel limité. Le principe s'étend à tout futur canal : chaque ajout doit être conditionnel à sa présence en config.

## Note 6 — Sensor WhatsApp toujours forwardé (optimisation P3)

### Problème

Dans `__init__.py`, `async_forward_entry_setups` forward toujours la plateforme sensor, quel que soit le contenu de la config. Résultat : le coordinator de polling du bridge et le sensor de statut démarrent même sans `bridge_url` configuré. Le coordinator interroge le endpoint /health toutes les cinq minutes pour rien.

### Proposition

Conditionner le forward de la plateforme sensor à la présence d'un `bridge_url` dans `entry.data`. Sans bridge, pas de coordinator, pas de sensor, pas de polling. Au setup, la config se lit directement dans `entry.data` — c'est déjà le pattern utilisé par `_get_runtime_config`.

### Impact attendu

Supprime un polling réseau et une entité sensor inutiles sur une instance sans WhatsApp. Sur un P3, chaque requête évité compte.

## Note 7 — `_get_runtime_config` reconstruit un dict à chaque appel

### Problème

`_get_runtime_config` dans `runtime.py` fusionne `entry.data` avec les constantes à chaque appel. Dans un cycle Alexa, il est appelé au moins trois fois (résolution des targets, volume TTS, post-delay). Le travail est redondant.

### Proposition

Cacher le résultat une fois par cycle et le passer en paramètre aux fonctions qui en ont besoin, ou le mettre en cache au niveau de l'entry avec invalidation sur reload.

### Impact attendu

Négligeable en CPU, mais c'est du travail répété gratuit. Utile sur un P3 pour la propreté.

## Note 8 — Audit de config côté agent : retirer ce qui n'est plus utile

### Instruction pour les agents

Quand un agent travaille sur ce repo, il doit regarder la config des clients (entry.data, const_private, exemples) et vérifier qu'il n'y a pas de choses configurées qui sont inutiles. Par exemple : de l'Alexa configuré sur une instance qui n'en a pas, des contacts WhatsApp morts, des phone targets sans mobile ni chat id, des telegram groups non utilisés. L'objectif est de limiter au maximum l'empreinte mémoire du composant sur du matériel comme un P3.

Concrètement : si le client n'utilise pas Alexa, on peut déconfigurer Alexa (vider alexa_players) pour que les early returns des notes 4 et 5 s'appliquent. Même logique pour WhatsApp : si le bridge n'est plus utilisé, retirer bridge_url pour désactiver coordinator et sensor.

### Impact attendu

Combiné aux notes 4 à 6, ça permet de réduire le composant à son strict minimum fonctionnel par instance.

## Notes générales

- Le boot recovery à soixante secondes couvre déjà le cas du bug 2, donc c'est un fallback acceptable.
- Le bug 1 est le plus sérieux des deux : une exception inattendue sur une seule enceinte peut laisser toutes les autres au volume TTS.
- Le bug 3 est documenté pour traçabilité mais explicitement hors scope.
- La note 4 est une optimisation, pas un bug : le composant fonctionne sans, mais c'est du gaspillage de ressources sur du matériel limité.
- La note 5 généralise le principe de la note 4 à tous les canaux du composant.
- La note 6 cible le forward systématique de la plateforme sensor.
- La note 7 cible la reconstruction répétée du dict runtime.
- La note 8 est une instruction de travail pour les agents : auditer et nettoyer la config client.
