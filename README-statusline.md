# Statusline zététique — Claude Code

Statusline multi-lignes (Catppuccin Mocha) avec barres en dégradé RGB, suivi
de coûts mensuels et jauges d'objectifs par personne.

## Fichiers

| Fichier | Rôle |
|---|---|
| `statusline-command.sh` | Script de rendu (appelé par Claude Code à chaque refresh). |
| `statusline-costs.py` | Agrégateur de coûts (scan `~/.claude/projects/**/*.jsonl`, cache 1 h). |
| `statusline-transcript.py` | Télémétrie par session (tok/s, compactions, âge réponse, last_ts) — reverse-tail + scan incrémental, cache court (15 s, en arrière-plan). |
| `statusline-budget.json` | Config **personnelle** : objectifs mensuels, TTL cache, taille d'affichage. |

## Segments

- **Identité** : modèle, effort, thinking 💡, dossier.
- **Git** : branche 🌿 + dirty `✗`, `↑n ↓n` (avance/retard vs upstream), `⚠n`
  (conflits), décompo `!M +A ✘D ?U` (m+). Repli `@repo` sur le sous-repo le plus
  récent quand le cwd n'est pas un dépôt.
- **Session** : barre contexte 🧠, tokens, `💰` coût, `⏱` durée, rate-limits
  🚀/🌟, churn ✏️.
- **Télémétrie** (m+) : `⚡ t/s` (débit du dernier tour — wall-clock, inclut la
  latence outils ⇒ borne basse), `🕑` âge dernière réponse, `❄` compte à rebours
  du cache de prompt (rouge = `cold`), `🗜` compactions de contexte.
- **Quota** (l+) : jauges 🎯 `🚀 5h` et `🌟 7d` = % du quota rate-limit Pro/Max
  consommé (la vraie contrainte « ne pas dépasser » ; 100 % = lockout), avec
  reset. Couleurs : vert < 50, jaune 50–79, rouge ≥ 80. Au preset `m`, version
  inline compacte sur la ligne session. Suivi d'une ligne **référence coût**
  (informative, pas un plafond) : `💰 $/mois · 🤖 $/run`.

## Installation

1. Copier les 4 fichiers dans `~/.claude/`.
2. Déclarer la statusline dans `~/.claude/settings.json` :
   ```json
   { "statusLine": { "type": "command", "command": "~/.claude/statusline-command.sh" } }
   ```
3. Adapter `statusline-budget.json` à ses propres objectifs.

## Tailles d'affichage (presets)

`xs` (1 ligne) · `s` (2) · `m` (3) · `l` (5, défaut) · `xl` (5, barres larges + moyenne/mo).

Réglage : variable d'env `STATUSLINE_SIZE`, ou champ `"size"` de `statusline-budget.json`.

## Notes techniques

- `.rate_limits.{five_hour,seven_day}` (comptes Pro/Max) : `used_percentage` est
  déjà un ratio du quota → pilote directement les jauges 🎯 ; `resets_at` = epoch
  en **secondes**. Pas de budget mensuel absolu : sur un forfait flat-rate, la
  contrainte est le quota, pas une dépense en $/tokens.
- Barres : interpolation RGB continue par cellule (`grad_rgb`) vert→jaune→pêche→rouge.
- Seuils de contexte : `~/.claude/ctxguard-thresholds.json` (partagés avec le hook stop-context-guard).
- Télémétrie : le `.py` tourne en arrière-plan (lock + TTL 15 s) et écrit un cache
  par session (clé = `transcript_path`) ; `🕑` et `❄` sont recalculés en direct à
  chaque refresh depuis `last_ts`, donc le décompte reste à la seconde entre deux
  scans. JSONL append-only ⇒ le compte de compactions est incrémental (scan des
  octets ajoutés `[prev_size, size)` uniquement).
- `cache_ttl_min` : 5 (défaut Pro) ou 60 (Max) — source : docs Anthropic
  prompt-caching (TTL 5 min par défaut). Inspirations : `CCometixLine`
  (git ahead/behind + conflits), `claude-hud` (tok/s, compactions, cache TTL).
