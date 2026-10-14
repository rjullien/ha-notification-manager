# AGENT.md

Règles de travail pour les agents (Kiro, Devin, Grok, Claude, ...) qui relisent
ou modifient ce dépôt. À lire avant toute revue de code et avant toute analyse
de branche ou de PR.

## Règle 1 : vérifier l'arbre Git avant d'affirmer qu'une chose est absente

Toute affirmation du type « ce code n'existe pas », « ce correctif n'a jamais
été appliqué » ou « cette logique a été supprimée » doit être vérifiée contre un
arbre Git **explicitement nommé**, après `git fetch` :

```bash
git fetch origin
git grep -n "<motif>" origin/main -- custom_components/notification_manager/<fichier>.py
```

Contraintes :

1. **Jamais** de recherche sur une copie locale non synchronisée (`main` local
   en retard, worktree d'une ancienne session, export partiel de fichiers,
   fichier récupéré via un outil MCP). Un `git fetch` est obligatoire avant la
   vérification.
2. L'arbre interrogé doit être **cité dans l'affirmation** : « absent de
   `origin/main` @ `<sha>` » et non « absent du code ».
3. Si la recherche par motif ne renvoie rien, tenter au moins un second motif
   (nom de symbole, constante, texte de log) avant de conclure. Le
   reformatage (Black, retours à la ligne) casse les motifs multi-tokens :
   `rejected = (400 <= resp.status < 500` ne matche pas le code réel, découpé
   sur trois lignes.

### Incident de référence

`docs/DIAGNOSTIC_RESTANT.md` (PR #27) §1 affirme « Ma recherche de code n'a
trouvé aucune trace » de la logique de non-retry sur 4xx du lot B et en fait la
priorité 1 à réimplémenter. La logique était intégralement présente sur
`main` :

| Élément | Emplacement sur `origin/main` |
|---|---|
| Classification 4xx non retryable | `custom_components/notification_manager/messaging.py:291-295` (`400 <= resp.status < 500 and resp.status not in (408, 429)`) |
| Alerte distinguant refus bridge et bridge indisponible | `messaging.py:254-256` (`message refusé par le bridge (HTTP {status_text})`) |
| Garde message vide | `messaging.py:202-203` |
| `_WhatsAppSendResult.refused_status` | `messaging.py:30` |

La recherche avait été lancée sur une copie locale en retard de plusieurs heures
sur `origin/main`. Trois autres items du même document étaient déjà livrés pour
la même raison : télémétrie A3/A6 (`alexa.py:210`, `:258`, `:457`),
`update_config` déjà supprimé, et `alexa_tts_volume = 0.0` déjà rejeté
(`config_flow.py:418`, `if not 0.0 < alexa_tts_volume <= 1.0`).

## Règle 2 : lire les diffs de branche contre leur merge base

Une branche empilée (stacked) ou de longue durée ne se lit **jamais** avec un
diff à deux points contre `main`, qui affiche comme des suppressions tous les
commits que la branche ne fait que ne pas avoir.

```bash
# Ce que la branche change réellement
git diff $(git merge-base origin/main <branche>)..<branche>
# ou, équivalent, le diff à trois points
git diff origin/main...<branche>

# Ce que donnerait la fusion dans main, conflits inclus
git merge-tree --write-tree origin/main <branche>
```

La mergeabilité se calcule avec `git merge-tree`, **pas** avec le champ
`mergeable` de l'API GitHub : celui-ci est évalué contre la branche de base de
la PR (pas forcément `main`) et peut être périmé.

### Incident de référence

Un `git diff origin/main..origin/devin/1791839381-alexa-volume-persistence`
semblait montrer que la PR #22 revertait tout le lot B (header Bearer,
`.get("mobile")`, classification 4xx, chat IDs du watchdog). Elle ne revertait
rien : la branche était empilée sur une base antérieure à ces commits.

En parallèle, GitHub annonçait `mergeable = clean` pour la PR #22 alors que
`git merge-tree --write-tree origin/main <branche>` signalait des conflits de
contenu dans `README.md`, `__init__.py`, `alexa.py`, `const.py` et
`tests/test_guards_and_unload.py` : la PR #20 avait été squash-mergée et la
branche ciblait toujours sa branche parente `devin/...`. Cet empilement a aussi
empêché le déclencheur `pull_request: branches: [main]` de `tests.yaml` de
s'exécuter, si bien que les PR #22 et #23 n'ont porté aucun check-run jusqu'à
leur retargeting sur `main`.

## Règle 3 : auditer la config par canal

Avant d'analyser une performance ou un bug sur un site, comparer les canaux
réellement configurés dans `entry.data` et
`/config/notification_manager_private.py` aux usages des automatisations.
Lorsqu'un canal n'est pas utilisé, le vider dans la configuration plutôt que
d'ajouter un contournement dans le code.

## Checklist avant de publier une revue ou un diagnostic

- [ ] `git fetch origin` exécuté dans la session courante.
- [ ] Chaque affirmation d'absence est accompagnée de la commande `git grep` et
      de l'arbre interrogé (`origin/main` @ `<sha>`).
- [ ] Au moins deux motifs de recherche essayés avant toute conclusion négative.
- [ ] Tout diff de branche est pris contre `git merge-base` (ou en trois points).
- [ ] La mergeabilité annoncée vient de `git merge-tree`, pas de l'API GitHub.
- [ ] La branche de base de chaque PR citée a été vérifiée (une PR empilée ne
      déclenche pas la CI configurée sur `main`).

## Conventions du dépôt

- Travail sur une branche puis Pull Request vers `main` ; pas de push direct sur
  `main`.
- Messages de commit en anglais, Conventional Commits (`docs:`, `fix(alexa):`,
  `feat(alexa):`, `chore:`).
- Tests : `pytest` à la racine (`pytest.ini`) ; la CI (`.github/workflows/tests.yaml`)
  ne se déclenche que sur `push`/`pull_request` vers `main`.
- `docs/grok-tesla/` est un sous-arbre d'archive conservé pour traçabilité : ne
  plus le modifier.
