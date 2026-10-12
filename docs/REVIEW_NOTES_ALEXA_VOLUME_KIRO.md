# Méta-revue — Kiro (12 oct. 2026)

Relecture de `REVIEW_NOTES_ALEXA_VOLUME.md` (Parties 1–3) **et** de
`REVIEW_NOTES_ALEXA_VOLUME_DEVIN.md`, tels qu'ils sont sur la PR #19
(`docs/grok-tesla-review-notes` @ `9a4835e`). Vérifié contre `main` @ 1.9.6
(`e7c4a80`) et, pour les points HA core, contre les sources upstream.

> Rédigé avant l'arrivée de `BUG_FIX_PLAN.md` (`aa216ca`) sur la même branche.
> La §6 fait le rapprochement : 5 points de ce document restent ouverts dans ce
> plan, dont une erreur de raisonnement sur son item A2.

## Verdict

La critique Devin est juste et ses références de ligne sont exactes — je les ai
toutes rouvertes. Le plan « PR 1 correctif minimal, puis PR 2 Store + recovery »
est le bon découpage.

Mais **trois affirmations partagées par les deux revues sont fausses ou
incomplètes**, dont une qui invalide une partie du design de la PR 2, et il
reste **quatre angles morts** côté code. Détail ci-dessous.

---

## 1. Ce qui tient (re-vérifié)

| Affirmation | Vérification |
|---|---|
| Pas de `try/finally` autour des étapes 2–5 | ✅ `alexa.py:241` (`async with lock`) → `:296`, aucun handler |
| `last_good[entity_id] = restore_vol` inconditionnel | ✅ `alexa.py:267` |
| Filtre amont : `unavailable` seulement, pas `unknown` | ✅ `alexa.py:224` |
| `hass.async_create_task` pour les cycles Alexa | ✅ `notify.py:79` et `:91` |
| `hass.data.pop(DOMAIN)` au dernier unload | ✅ `__init__.py:199` |
| `alexa_post_tts_delay = 0` accepté | ✅ `config_flow.py:337` (`< 0` rejeté) |
| Chemin EN sans volume ni verrou | ✅ `alexa.py:377-410` |
| Constantes citées | ✅ `const.py` : TTS 0.7, défaut 0.5, delay 8 s, EN delay 3 s |
| Tests cités existent bien | ✅ `test_alexa_tts.py:116/128/152`, `test_guards_and_unload.py:92` |
| `queue_delay` alexapy ≈ 1,5 s | ✅ corroboré par les logs d'[alexa_media_player #828](https://github.com/custom-components/alexa_media_player/issues/828) (`'queue_delay': 1.5`) |

Devin §1 (retirer `asyncio.shield`) est correct sur le mécanisme : `shield`
transforme le coroutine en tâche indépendante, et si l'`await` du `finally` est
interrompu, le restore continue **hors du verrou**. Conclusion juste.

Devin §2 (compteur de génération par Echo) est le meilleur apport des deux
documents : il couvre les deux courses réelles du job différé, ce que « annulé
par un nouveau cycle » seul ne fait pas.

---

## 2. Trois corrections aux deux revues

### 2.1 `entry.async_create_task` n'annule pas — il **attend**

Les deux documents décrivent le changement comme « HA traque / annule les tâches
à l'unload » (`notify.py:102` le dit déjà, à tort, dans le code actuel).
Source HA `config_entries.py`, `_async_process_on_unload` :

- seules les tâches **background** sont annulées (`task.cancel(cancel_message)`) ;
- les tâches normales (`entry.async_create_task`) sont **attendues** :
  `asyncio.wait([...], timeout=10)`, puis les restantes sont simplement
  **loguées** (« Task %s did not complete in time ») — jamais annulées.

Trois conséquences que ni Grok Bot ni Devin ne tirent :

1. **Le cas I est réglé par le changement de tâche seul.** Le cycle en vol va
   jusqu'au bout et restaure. Ce n'est pas le `try/finally` qui le corrige.
2. **Chaque reload d'entrée est maintenant bloqué jusqu'à 8 s** (le délai par
   défaut), et tout reconfigure déclenche un reload
   (`__init__.py` → `_async_update_listener` → `async_reload`). Avec plusieurs
   annonces sérialisées derrière le verrou global, on dépasse les 10 s et on
   récolte un warning HA à chaque reload. Effet de bord visible, à assumer
   explicitement.
3. Donc le `try/finally` reste utile — pour les **exceptions** et l'**arrêt
   complet de HA** — mais il ne faut pas le vendre comme le correctif du cas I.

**Et surtout, l'ajout hors spec de Devin (« passer le chemin EN en
`entry.async_create_task` pour cohérence ») va dans le mauvais sens** : il
ajoute 3 s de sleep + un appel TTS au chemin critique de l'unload, pour un
chemin inutilisé et sans état volume à restaurer. La bonne prescription est
asymétrique :

- **FR → `entry.async_create_task`** : attendu, le cycle finit, le restore passe.
- **EN → `entry.async_create_background_task`** : annulé à l'unload, ce qui est
  exactement le comportement voulu pour un fire-and-forget sans volume.

### 2.2 Le marqueur de la PR 2 est incompatible avec `async_delay_save`

Grok Bot recommande explicitement `Store.async_load / async_save /
async_delay_save`. Or, source HA `helpers/storage.py` : une écriture différée
n'est flushée que par `EVENT_HOMEASSISTANT_FINAL_WRITE`
(`_async_callback_final_write`), c'est-à-dire un **arrêt propre**.

Le marqueur « cycle en cours » existe pour survivre à un **crash ou une coupure
de courant** — précisément le cas où ce flush n'arrive jamais. Un marqueur en
`async_delay_save` est donc inutile dans son unique scénario d'usage.

Il faut un `await store.async_save()` **immédiat avant chaque montée de
volume** : une écriture JSON sur la carte SD par annonce, sur le chemin chaud,
sous le verrou. Latence + usure flash sur un Pi, pour 15 annonces d'affilée.

Corollaire : seul le cache last-known-good peut utiliser `async_delay_save`
(il bouge rarement). Le marqueur, non.

**Alternative à chiffrer avant de coder la PR 2** — un service
`notification_manager.restore_alexa_volumes` qui remet chaque player connu à son
last-known-good, appelable depuis une automatisation sur `homeassistant_start`
et/ou un bouton. Ça supprime d'un coup : le marqueur, l'écriture hot-path,
l'attente de `EVENT_HOMEASSISTANT_STARTED`, la détection de disponibilité
d'entité, le timeout, et le faux positif « 0,7 volontaire » (c'est l'utilisateur
qui décide de déclencher). Ça couvre aussi les cas A et D manuellement le jour
où le job différé échoue. Si on veut l'automatique, persister **uniquement** le
cache et laisser l'automatisation de démarrage appeler le service.

### 2.3 Le « retry once » crédité comme parade aux cas A/D ne vaut presque rien

La matrice Partie 1 note « retry-once » comme mitigation existante des cas A et
D, et Devin la valide. Mais `_async_restore_volumes` (`alexa.py:117-128`)
réessaie **sans aucun délai** : l'appel suivant part immédiatement, contre la
même condition transitoire qui vient d'échouer.

Donc le « re-restore systématique ~20 s » n'est pas une amélioration marginale
sur l'existant : c'est la **première** vraie parade. À reformuler, sinon on
sous-priorise l'item.

---

## 3. Angles morts des deux revues

### 3.1 Le restore est séquentiel, la montée est parallèle

Étape 2 : `asyncio.gather` sur toutes les cibles (`alexa.py:270-272`).
Étape 5 : boucle `for` avec `await`, jusqu'à 2 appels par entity
(`alexa.py:117-128`). Avec 6 enceintes et un aller-retour Amazon lent, le
restore prend 6× (voire 12×) le temps de la montée — tout sous le verrou
global. C'est à la fois une fenêtre « volume haut » rallongée et une rétention
de verrou gratuite. Passer l'étape 5 en `gather` : 2 lignes, proposé par
personne.

### 3.2 Aucun test n'annule de tâche

`grep -rn "CancelledError\|cancel()" tests/` → **zéro résultat**. Le bug phare
de tout le document (cas C/I) n'a aucune couverture, et les deux revues ne
parlent que d'**adapter** des tests existants. (`BUG_FIX_PLAN.md` corrige ce
point : sa liste de tests du lot A inclut bien l'annulation pendant le sleep.)
Les tests que la PR 1 doit créer :

1. annuler la tâche pendant le `asyncio.sleep` de l'étape 4 → asserter que
   `volume_set` a été appelé avec la valeur de repos ;
2. `reported is None` → asserter que `last_good` n'est **pas** écrit ;
3. le job différé tire une fois, et est **sauté** quand un nouveau cycle a
   démarré (le compteur de génération de Devin §2) ;
4. plancher de délai appliqué au runtime alors que la config accepte toujours 0.

Et les passer à la mutation : réintroduire chaque bug, confirmer que le test
correspondant devient rouge, avant de livrer.

### 3.3 `unknown` est filtré de façon incohérente à **trois** endroits

Devin repère le pré-filtre (`alexa.py:224`, `unavailable` seulement) et l'étape
1 sous verrou (`:246`, qui teste bien `("unavailable", "unknown")`). Il manque
le troisième : **`_make_alexa_resolver` (`alexa.py:149`) a le même trou**. C'est
le résolveur public, celui qui répond à « est-ce que ce mot-clé atteint
vraiment une enceinte ? » — il répond donc *oui* pour une Echo en `unknown`,
exactement le faux négatif silencieux qu'il est censé détecter. Un helper
partagé pour les trois.

### 3.4 Le cache `last_good` n'est jamais purgé

Indexé par `entity_id`, il grossit à chaque player renommé ou supprimé. En
mémoire c'est du bruit ; **persisté (PR 2) ça devient du déchet permanent dans
`.storage`**, et une entrée périmée est précisément ce qui alimentera un restore
faux après ré-ajout de l'intégration alexa_media — le scénario des doublons
`_2` déjà mentionné en commentaire ligne 222. Purger contre `alexa_players`
au chargement.

---

## 4. Forme et process

- **312 + 84 lignes de design pour un correctif de ~60 lignes**, en 3 parties,
  3 auteurs et 2 couches de re-litigation. Le plan en 7 étapes de la Partie 1
  est déjà périmé par la Partie 3 ; la Partie 2 contredit l'item 6 de la
  Partie 1 (« purger le cache »), et Devin tranche mais en passant. Avant
  d'implémenter : **réduire à une seule spec courante** (Partie 3 + corrections
  Devin + ce document), et reléguer les Parties 1 et 2 en annexe. Sinon l'agent
  qui implémente a trois jeux d'instructions, dont deux obsolètes.
- La PR #19 dépose **11 fichiers markdown dans
  `custom_components/notification_manager/`** — le répertoire que HACS copie
  chez les utilisateurs. Les mettre dans `docs/` à la racine.
- L'en-tête du tableau Devin annonce « chaque affirmation vérifiée contre
  `main` @ 1.9.6 », mais la ligne alexapy est de la lecture de doc upstream.
  Grok Bot étiquetait scrupuleusement ses claims non testés ; garder cette
  discipline plutôt que de la diluer.

---

## 5. Réponses aux 8 questions, là où je diffère

- **Q1 (PR 1 puis PR 2)** — oui, mais sans attendre que le symptôme disparaisse
  après la PR 1 : son `finally` ne sauvera rien lors d'une coupure de courant,
  qui est le cas B dominant. La vraie valeur est dans la PR 2 / le service de
  restore.
- **Q3 (re-restore ~20 s)** — d'accord, avec §2.3 : c'est la première parade
  réelle, pas un raffinement.
- **Q4 (marqueur + vérification de lecture)** — la vérification de lecture de
  Devin est solide, et pour une raison qu'il ne donne pas : l'état d'un
  `media_player` n'est **pas** restauré au redémarrage de HA, alexa_media
  re-interroge Amazon. La lecture au boot est donc une donnée fraîche, ce qui
  est exactement ce qui rend sa condition fiable. À écrire noir sur blanc. Mais
  voir §2.2 sur le coût du marqueur.
- **Q5 (plancher)** — d'accord pour le point d'appel. Noter que 2 s est en
  dessous de `queue_delay` (~1,5 s) + latence cloud + durée de parole : ça ne
  protège que les entrées déjà enregistrées à 0, ça ne remplace pas
  l'estimation de durée.
- **Q7 (chemin EN)** — laisser, mais en **background task** (§2.1), pas en
  `async_create_task`.

---

## 6. Rapprochement avec `BUG_FIX_PLAN.md` (`aa216ca`)

Ce plan est arrivé sur la branche après la rédaction des §1–5. Il recoupe
plusieurs points (shield retiré, compteur de génération, `last_good` non écrit
si `reported is None`, plancher au point d'appel, test d'annulation) et ajoute
un lot B de 4 bugs messagerie/watchdog hors périmètre volume — bonne trouvaille.
Son item **A1** (garder les 3 clés partagées au dernier unload) règle au
passage un effet de bord que je signalais : un job différé qui tire après un
`pop(DOMAIN)` travaillerait sur un dict remplacé.

**Reste ouvert après ce plan :**

1. **Son item A2 repose sur une prémisse fausse.** Il écrit « au unload, HA
   annule les tâches de l'entrée, et le `finally` de A3 restaure ». Non :
   `_async_process_on_unload` n'annule que les tâches **background** ; les
   tâches `entry.async_create_task` sont **attendues** (`asyncio.wait`,
   `timeout=10`) puis seulement loguées. Voir §2.1. Impacts concrets sur le
   plan :
   - A2 suffit à lui seul pour le cas I ; A3 couvre les exceptions et l'arrêt
     complet de HA, pas le reload ;
   - A2 étend `entry.async_create_task` à **téléphone, WhatsApp et groupe** en
     plus d'Alexa. Ces tâches-là sont déjà `await`-ées par le handler notify
     (`notify.py:149`), mais le cycle Alexa FR va bloquer chaque reload jusqu'à
     8 s, et le chemin EN y ajoute ses 3 s de sleep initial. À assumer, ou
     passer EN en `async_create_background_task`.
2. **§2.2 — le marqueur du lot C et `async_delay_save`.** Le plan dit
   « marqueur écrit avant la montée » et « persister `last_good` (débounce) ».
   Le débounce est correct pour `last_good`, mais le marqueur exige un
   `async_save()` **immédiat** (une écriture SD par annonce, sous le verrou) :
   une écriture différée n'est flushée qu'à l'arrêt propre, jamais au crash que
   le marqueur est censé couvrir. À trancher avant de coder le lot C.
3. **§2.3 — le retry existant n'a aucun backoff.** Le plan présente A6 comme
   couvrant « les échecs Amazon silencieux » ; c'est en réalité la première
   parade *tout court* pour les cas A/D.
4. **§3.1 — restore séquentiel vs montée parallèle.** Absent du plan. 2 lignes,
   dans le même fichier que A3/A5.
5. **§3.3 — `unknown` : troisième site.** Le plan ne corrige que
   `alexa.py:222-225`. `_make_alexa_resolver` (`:149`) garde le trou, donc le
   résolveur public continue de répondre « oui, ça atteint une enceinte » pour
   une Echo en `unknown`.
6. **§3.4 — `last_good` jamais purgé.** Absent du plan, et il devient un vrai
   problème au lot C (déchet permanent dans `.storage`, restore faux après
   ré-ajout d'alexa_media).
7. **§4 — emplacement des fichiers.** `BUG_FIX_PLAN.md` est à la racine, mais
   les 9 notes de migration et `AGENT_README.md` restent dans
   `custom_components/notification_manager/`, livré par HACS.
