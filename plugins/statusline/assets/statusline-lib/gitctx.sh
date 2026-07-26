# shellcheck shell=bash
# shellcheck disable=SC2034  # This module's constants and outputs are consumed by
# sibling modules and by the composition root that sources them all; shellcheck
# analyses one file at a time and cannot follow that boundary. What actually
# proves each name is live is tests/statusline (which sources the whole set and
# asserts the exports exist) and the golden render diff, not this warning.
# statusline-lib/gitctx.sh — the repository facts the git line shows.
#
# Single responsibility: asking git where we are. Changes when the git facts on
# display change. Every call is read-only and bounded (one symbolic-ref, one
# rev-list, one status); nothing here writes to a repository.
#
# Depends on: platform.sh (file_mtime).
#
# Sets, via collect_git: git_branch git_dirty git_repo git_root
#                        git_ahead git_behind git_conf nM nA nD nU

# branch_of — echo "branch<TAB>dirty" for the repo rooted at $1.
# post: branch is the symbolic ref, or the short SHA when detached; dirty is "1"
#       when the worktree or the index differs from HEAD, empty otherwise (a
#       boolean flag only — the render prints the word "modified").
branch_of() {
  local root="$1" b d=""
  b=$(git -C "$root" -c core.useBuiltinFSMonitor=false symbolic-ref --short HEAD 2>/dev/null \
      || git -C "$root" -c core.useBuiltinFSMonitor=false rev-parse --short HEAD 2>/dev/null)
  if ! git -C "$root" -c core.useBuiltinFSMonitor=false diff --quiet 2>/dev/null \
     || ! git -C "$root" -c core.useBuiltinFSMonitor=false diff --cached --quiet 2>/dev/null; then
    d="1"
  fi
  printf '%s\t%s' "$b" "$d"
}

# compute_git_extra — ahead/behind vs upstream and a porcelain file-stat
# breakdown for the repo rooted at $1. Pure git; one rev-list + one status call.
# post: sets git_ahead git_behind git_conf nM nA nD nU.
compute_git_extra() {
  local root="$1" lr line xy
  lr=$(git -C "$root" -c core.useBuiltinFSMonitor=false \
       rev-list --left-right --count 'HEAD...@{u}' 2>/dev/null)
  if [ -n "$lr" ]; then
    git_ahead=$(printf '%s' "$lr" | awk '{print $1}')
    git_behind=$(printf '%s' "$lr" | awk '{print $2}')
  fi
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    xy="${line:0:2}"
    case "$xy" in
      '??')          nU=$((nU+1)) ;;
      U*|?U|AA|DD)   git_conf=$((git_conf+1)) ;;   # unmerged / conflict
      *)
        case "$xy" in *M*) nM=$((nM+1)) ;; esac
        case "$xy" in A*)  nA=$((nA+1)) ;; esac
        case "$xy" in *D*) nD=$((nD+1)) ;; esac
      ;;
    esac
  done < <(git -C "$root" -c core.useBuiltinFSMonitor=false status --porcelain 2>/dev/null)
}

# collect_git — resolve every git fact for the working directory $1.
# pre:  $1 the session's current directory (may not be a repository).
# post: sets the git_* and n* globals listed in this file's header; all of them
#       are set to a defined value (empty or 0) even when $1 is not a repo, so
#       no caller has to distinguish "unset" from "none".
# When $1 is not itself a repository (a workspace root, say) the branch shown is
# that of the sub-repo under it whose .git was touched most recently — the one
# being worked in. Bounded to depth 2 for speed.
collect_git() {
  local cwd="$1" newest_git=""
  git_branch=""; git_dirty=""; git_repo=""; git_root=""
  git_ahead=""; git_behind=""; git_conf=0; nM=0; nA=0; nD=0; nU=0

  if git -C "$cwd" rev-parse --git-dir > /dev/null 2>&1; then
    IFS=$'\t' read -r git_branch git_dirty < <(branch_of "$cwd")
    git_root="$cwd"
  else
    newest_git=$(find "$cwd" -maxdepth 2 -name .git -type d -prune 2>/dev/null \
      | while IFS= read -r g; do
          printf '%s %s\n' "$(file_mtime "$g")" "${g%/.git}"
        done | sort -rn | head -1 | cut -d' ' -f2-)
    if [ -n "$newest_git" ]; then
      IFS=$'\t' read -r git_branch git_dirty < <(branch_of "$newest_git")
      git_repo=$(basename "$newest_git")
      git_root="$newest_git"
    fi
  fi
  [ -n "$git_root" ] && compute_git_extra "$git_root"
  return 0
}
