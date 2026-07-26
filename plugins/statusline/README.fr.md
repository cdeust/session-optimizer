> English version: [README.md](README.md)

# statusline — Claude Code

Statusline multi-lignes avec barre heat-track discrète, un registre de coûts
unique, télémétrie par session et jauges de rate-limit avec rythme de
consommation.

Chaque ligne s'ouvre sur un mot nommant son sujet, et chaque valeur est précédée
du mot qui dit ce qu'elle mesure — aucun emoji, aucun état encodé en glyphe. Un
glyphe s'apprend et reste ambigu d'une police de terminal à l'autre (plusieurs
s'affichent en double largeur et cassent les budgets de colonnes) ; un mot se lit
pareil partout. L'état est porté par la couleur et le nombre, rien d'autre.

## Installation

```
/plugin marketplace add cdeust/session-optimizer
/plugin install statusline@session-optimizer-marketplace
```

Puis demander à Claude d'**« installer la statusline »** — le skill
`statusline` embarqué copie les fichiers dans `~/.claude/` et déclare
`statusLine` dans `~/.claude/settings.json` (sauvegardes incluses, fichiers
de config jamais écrasés). Redémarrer Claude Code pour activer. Requiert
`jq` et `python3`.

Un hook `SessionStart` maintient les fichiers de code à jour après
`plugin update` ; `statusline-budget.json` et `ctxguard-thresholds.json`
ne sont jamais touchés automatiquement.

<details>
<summary>Installation manuelle (sans le système de plugins)</summary>

1. Copier tout le contenu de `assets/` dans `~/.claude/` — y compris le
   répertoire `statusline-lib/` entier, qui doit se trouver à côté du moteur de
   rendu — puis `chmod +x ~/.claude/statusline-command.sh ~/.claude/costs.sh`.
2. Déclarer la statusline dans `~/.claude/settings.json` :
   ```json
   { "statusLine": { "type": "command", "command": "bash ~/.claude/statusline-command.sh", "padding": 1, "refreshInterval": 10 } }
   ```
3. Adapter `statusline-budget.json` à ses propres préférences.

</details>

## Fichiers (embarqués sous `assets/`)

| Fichier | Rôle |
|---|---|
| `statusline-command.sh` | Point d'entrée du rendu — la racine de composition, appelée par Claude Code à chaque refresh. |
| `statusline-lib/*.sh` | Les modules du moteur de rendu, un sujet par fichier. À installer à côté du moteur. |
| `costs.sh` | CLI du registre de coûts sur `~/.claude/statusline-costs.jsonl` — source unique de chaque montant. |
| `pricing.json` | Prix par modèle utilisés par `costs.sh`. |
| `statusline-transcript.py` | Télémétrie par session (tok/s, compactions, âge réponse, last_ts) — reverse-tail + scan incrémental, cache court (15 s, en arrière-plan). |
| `statusline-budget.json` | Config **personnelle** : TTL cache, taille d'affichage. |
| `ctxguard-thresholds.json` | Seuils de contexte par modèle — **partagés** avec le plugin context-guard (voir plus bas). |

Le moteur résout `statusline-lib/` relativement à son propre chemin (surchargeable
par `$STATUSLINE_LIB`) et s'arrête en nommant le fichier manquant si un module est
absent, plutôt que d'afficher une statusline partielle.

| Module | Responsabilité unique |
|---|---|
| `platform.sh` | Différences d'orthographe BSD/GNU (`stat`, `date`) |
| `palette.sh` | Jetons de couleur, barre heat-track |
| `fit.sh` | Mesure de largeur visible et rognage |
| `severity.sh` | L'échelle ok/warn/danger unique et ses seuils |
| `format.sh` | Nombres et durées tels que lus |
| `config.sh` | Les deux fichiers de config JSON |
| `gitctx.sh` | Les faits du dépôt |
| `session_state.sh` | Registre de coûts, télémétrie transcript, tracker de sous-agents |
| `layout.sh` | Sonde de largeur du terminal, preset de verbosité |
| `render.sh` | Une fonction par ligne de statut |

## Segments

```
model      model NOM | dir NOM | effort NIVEAU | thinking on
branch     branch NOM modified | ahead N | behind N | mod N add N del N
context    ███░░░ N% Nk tokens | session $N | elapsed NmNs | edits +N/-N
throughput N tokens/s | idle NmNs | cache warm NmNs | compactions N
quota 5h   ███░░░ N% | pace N.Nx | resets in NhNm
spend      today $N | month $N | average $N/day
```

- **Identité** : modèle, dossier, effort, thinking. Le dossier vient en second
  volontairement — l'ordre des segments est l'ordre de priorité (voir *Rognage*
  plus bas), et sur un terminal étroit « dans quel arbre suis-je » vaut plus que
  les réglages de session.
- **Git** : branche + `modified`, avance/retard vs upstream, conflits, et une
  décomposition `mod/add/del/untracked` (m+). Repli `NOM@repo` sur le sous-repo
  le plus récemment touché quand le cwd n'est pas un dépôt.
- **Session** : barre de contexte, tokens, coût de la session, durée, churn.
- **Sous-agents** : nombre et volume de tokens pour cette session, lus depuis
  l'agrégat maintenu par le tracker `SubagentStop` du plugin context-guard
  (segment vide quand ce plugin n'est pas installé). Leur **coût** n'est pas
  affiché à part — il est déjà dans le montant de la session, que le registre
  calcule depuis le sous-arbre `subagents/`.
- **Télémétrie** (m+) : débit du dernier tour (wall-clock, inclut la latence
  outils ⇒ borne basse), temps d'inactivité depuis la dernière réponse, compte à
  rebours du cache de prompt (`cache cold` en rouge une fois expiré), compactions
  de contexte.
- **Quota** (l+) : `quota 5h` et `quota 7d` = % du quota rate-limit Pro/Max
  consommé (la vraie contrainte « ne pas dépasser » ; 100 % = lockout), chacun
  avec son **rythme** et son heure de reset. Au preset `m`, version inline
  compacte sur la ligne session — même résolveur, donc les deux rendus d'une même
  fenêtre ne peuvent pas diverger.
- **Dépense** (l+) : aujourd'hui / mois en cours / moyenne par jour, chaque
  montant venant du registre unique et incluant les sous-agents. Informatif, pas
  un plafond.

## Rythme (pace)

Un pourcentage de quota seul ne dit pas s'il y a un problème : 60 % consommés,
c'est sain à quatre heures d'une fenêtre de cinq heures, et alarmant au bout de
dix minutes. Le **rythme** est la vitesse de consommation rapportée à l'horloge
de la fenêtre — % consommé divisé par la part de fenêtre écoulée — ce qui est
aussi la projection linéaire de la consommation au reset : `1.0x` projette
d'atterrir exactement sur le plafond.

Le pourcentage porte le **pire** des deux relevés (absolu et rythme) ; le chiffre
de rythme porte le sien. Couleurs : vert sous 50 % (ou sous 0.8x), jaune à partir
de 50 %, rouge à partir de 80 % ou dès qu'une projection atteint le plafond. Sous
10 % de fenêtre écoulée, aucun rythme n'est affiché — l'extrapolation partirait
dans tous les sens sur une seule rafale.

## Rognage

Chaque ligne est ajustée au terminal. Le preset de verbosité est le réglage
grossier (combien de **lignes**), `fit_line` le réglage fin : il retire les
segments de plus faible priorité en fin de ligne jusqu'à ce qu'elle rentre. Les
lignes sont construites du plus important au moins important, donc **c'est la
queue qui part**.

Le budget est la largeur du terminal moins ce que l'hôte garde pour lui : 4
colonnes de marge du conteneur, plus `statusLine.padding` compté deux fois
(l'hôte l'applique des deux côtés). Ces deux chiffres sont lus dans le rendu de
Claude Code 2.1.220 lui-même, qui enveloppe le bloc dans
`<Box paddingLeft={2} paddingRight={2}>` et chaque ligne dans
`<Text wrap="truncate">` — une ligne trop large est donc tronquée **seule** et ne
coûte jamais une rangée au bloc.

La largeur est sondée depuis `$COLUMNS` d'abord — l'hôte y met la largeur dans
laquelle il rend — puis le tty de contrôle, puis `tput cols` (uniquement quand
stdout est un terminal ; sur un tube il renvoie le 80 aveugle de terminfo). Quand
rien ne répond, le repli est volontairement large. Surchargeable par
`$STATUSLINE_COLS`.

## Seuils partagés avec context-guard

L'échelle vert → jaune → rouge de la barre de contexte est pilotée par
`~/.claude/ctxguard-thresholds.json` — une **convention de fichier partagé**
avec le hook Stop du plugin [context-guard](../context-guard). Un fichier,
deux consommateurs : la statusline est l'alerte visuelle passive, le garde
Stop l'application active ; éditer le fichier déplace les deux d'un coup,
donc ils restent alignés par construction. Le skill d'installation crée le
fichier s'il est absent et n'écrase jamais une copie existante.

## Tailles d'affichage (presets)

`xs` (1 ligne) · `s` (2) · `m` (3) · `l` (5, défaut) · `xl` (5, barres larges + moyenne/mo).

Réglage : variable d'env `STATUSLINE_SIZE`, ou champ `"size"` de `statusline-budget.json`.

## Notes techniques

- `.rate_limits.{five_hour,seven_day}` (comptes Pro/Max) : `used_percentage` est
  déjà un ratio du quota → pilote directement les jauges ; `resets_at` = epoch
  en **secondes**. Pas de budget mensuel absolu : sur un forfait flat-rate, la
  contrainte est le quota, pas une dépense en $/tokens.
- Barres : heat-track **discret** à 4 paliers, chaque cellule colorée par sa
  position sur la largeur totale — sans interpolation, donc une barre plus pleine
  accumule visiblement les paliers de gauche à droite.
- Coûts : un seul registre. `costs.sh` calcule le prix de chaque session depuis
  son propre transcript plus tout le sous-arbre récursif `subagents/`, après
  déduplication sur `message.id:requestId` — Claude Code réenregistre une même
  réponse d'API 2 à 3 fois (streaming / continuation d'outil), et sommer les
  lignes assistant brutes surcompte ~2,2x (mesuré sur 172 transcripts locaux :
  3438,84 $ dédupliqués contre 7645,04 $ bruts). Le `.cost.total_cost_usd` de
  Claude Code ne couvre que le fil principal et reporte la dépense antérieure sur
  une session reprise : il ne sert que de repli, affiché `session main`.
- Télémétrie : le `.py` tourne en arrière-plan (lock + TTL 15 s) et écrit un cache
  par session (clé = `transcript_path`) ; les décomptes d'inactivité et de cache
  sont recalculés en direct à chaque refresh depuis `last_ts`, donc ils restent à
  la seconde entre deux scans. JSONL append-only ⇒ le compte de compactions est incrémental (scan des
  octets ajoutés `[prev_size, size)` uniquement).
- `cache_ttl_min` : 5 (défaut Pro) ou 60 (Max) — source : docs Anthropic
  prompt-caching (TTL 5 min par défaut). Inspirations : `CCometixLine`
  (git ahead/behind + conflits), `claude-hud` (tok/s, compactions, cache TTL).
