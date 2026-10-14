# Revue des écarts spec → code (14 oct. 2026)

Chaque point des specs (`docs/REVIEW_NOTES_ALEXA_VOLUME.md` parties 1-3, `docs/REVIEW_NOTES_ALEXA_VOLUME_DEVIN.md`,
`docs/REVIEW_NOTES_ALEXA_VOLUME_KIRO.md`, `BUG_FIX_PLAN.md`, `docs/DIAGNOSTIC_RESTANT*.md`, issue #26,
branche `fix/alexa-bugs`) vérifié par `git grep` contre `origin/main` @ `f7a444c` (v1.10.1).

## Cause de l'écart

La Partie 3 de la spec avait mis 6 sujets dans « 3.4 Reporté ». `BUG_FIX_PLAN.md` (lots A-D) ne les a pas
repris et ne les a pas listés comme reportés : ils ont disparu du suivi sans décision de René.
L'estimation de la durée TTS en fait partie.

## Pas implémenté

| # | Point de spec | Source | État sur `origin/main` | Suite proposée |
|---|---|---|---|---|
| 1 | Durée d'attente estimée depuis la longueur du texte (plancher = `alexa_post_tts_delay`), même valeur pour `speech_estimate` | P1 §« Estimation de durée », P1 chantier 7, P2 §2.2, Devin §5 | Délai fixe : `alexa.py:452` `max(delay, 2)`, `alexa.py:325` `speech_estimate = alexa_post_tts_delay` | **En cours dans la PR 1.11.0** (2 s + 1 s par tranche de 12 caractères, plafond 90 s) |
| 2 | Échecs `volume_set` : récupérer les bools du `gather`, warning groupé, ne jamais bloquer le TTS | P1 item 1, P2 §2.3 #1, Devin « Ajouts » | `alexa.py:430` : résultat du `gather` ignoré | Ajout dans la PR 1.11.0 (petit) |
| 3 | Header `Authorization` seulement si le token est non vide, helper partagé par les appels bridge | P2 §2.5, P3 §3.4 | Fait seulement dans `messaging.py:226`. Toujours `Bearer ` vide dans `bridge_services.py:81`, `:122`, `coordinator.py:58`, `config_flow.py:165` | Ajout dans la PR 1.11.0 (petit) |
| 4 | Coalescing sous le verrou global : sauter le restore intermédiaire quand un cycle suivant attend déjà | P2 §2.2, P3 §3.1/3.4, Devin Q2 | Absent (`git grep -i coalesc` : rien) | Décision René |
| 5 | Une file par Echo (les pièces ne s'attendent plus, une seule montée/descente pour une rafale) | P1 design cible, chantier 6 | Absent, verrou global unique | Décision René (gros chantier) |
| 6 | Pause ~2 s ou relecture entre `volume_set` et TTS (batching alexapy `queue_delay` 1,5 s) | P1 item 2, P2 §2.3 #2 | Absent | À tester sur une vraie Echo avant de coder |
| 7 | Chemin EN : sous verrou, ou supprimé | P2 §2.2, `ALEXA_EN_LOCK_NOTE.md`, plan D | Toujours hors verrou, sans volume, délai fixe 3 s (`alexa.py:559`) | Décision René : supprimer / garder / aligner |
| 8 | « Config vide impossible » : `[]`/`{}` sauvé dans l'UI retombe sur `const_private` | Plan D, DIAGNOSTIC §2 | `runtime.py` : 8 × `d.get(x) or _CONST_…` | Décision René : documenter ou `is not None` |
| 9 | Watchdog : entité supprimée ou `unknown` alerte toutes les 6 h sans fin | Plan D, DIAGNOSTIC §2, Kiro §3 | `watchdog.py:131/144` inchangé | Décision René : alerter sur `unknown` ou non, absente = une seule alerte |
| 10 | Test aller-retour avec un vrai `Store` | DIAGNOSTIC §4, Kiro §2 | `FakeStore` seulement (HA n'est pas installé dans les tests, tout est mocké) | Couvert seulement par le smoke test |
| 11 | Smoke test sur une vraie instance : TTS ciblé, reload pendant le TTS, restart pendant le TTS | Plan §4, Devin, DIAGNOSTIC | Jamais fait (1.10.1 déployée sans faire parler d'Echo) | Après la 1.11.0, avec ton accord (une Echo parle) |

## Écarté volontairement (décision tracée)

| Point | Décision | Où |
|---|---|---|
| Marqueur « cycle en cours » persistant | Remplacé par la vérification au boot (volume lu ≈ TTS) : un marqueur imposerait une écriture SD par annonce | Kiro §2.2, lot C |
| Retry exponentiel 30 s / 1 min / 2 min | Remplacé par le re-restore systématique à +20 s avec compteur de génération | P2 §2.2, Q3 |
| Rejeter `alexa_post_tts_delay = 0` | 0 reste accepté, plancher 2 s au runtime | Devin §5, A5 |
| `asyncio.shield` sur le restore | Retiré : il sortait le restore du verrou | Devin §1 |
| `return_exceptions=True` sur le `gather` | Rejeté, preuve par script | issue #26, branche Tesla bug 1 |
| Re-restore planifié dans le `finally` | Remplacé par le garde `ConfigEntryState.LOADED` | #29, branche Tesla bug 2 |
| Cache de `_get_runtime_config` | Gain négligeable, risque de config périmée après reconfigure | branche Tesla note 7 |

## Implémenté (vérifié)

A1-A7, B1-B4, lot C (Store, purge, boot recovery, service `restore_alexa_volumes`), lot D (validation JSON,
TTS 0 interdit, coordinator `ClientError`, `update_config` supprimé, entité « introuvable »), télémétrie A3/A6
(`alexa.py:210/258/457`), #26 points 2-4 (#29). Branche Tesla notes 4-6 (init conditionnelle) : en cours dans la PR 1.11.0.
