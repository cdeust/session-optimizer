# disk-hygiene

Session-owned disk cleanup for [Claude Code](https://code.claude.com) and
[Codex](https://github.com/openai/codex). Agent sessions leave git worktrees,
build output, scratch files and runtime state behind; on a busy day that fills a
disk. This plugin removes what a session created and can prove is safe to lose,
and nothing else.

It is independent of any memory server. It uses Python 3 (standard library),
Git, an authenticated GitHub CLI (`gh`) and `lsof`. A missing safety check
protects the path instead of authorising its removal.

## Install

Claude Code:

```
/plugin marketplace add cdeust/session-optimizer
/plugin install disk-hygiene@session-optimizer-marketplace
```

Codex:

```
codex plugin marketplace add cdeust/session-optimizer
codex plugin add disk-hygiene@session-optimizer-codex
```

## Switches

| Variable | Values | Effect |
|---|---|---|
| `DISK_HYGIENE_CLEANUP` | `on` (default), `off` | `off` disables every hook and every mutating command. `status` still works. Any other value is rejected. |
| `DISK_HYGIENE_TRANSCRIPTS` | `keep` (default), `delete` | `delete` opts into removing transcripts, nested subagent transcripts, file history and the context-guard checkpoint of ended sessions. Any other value is rejected before anything is removed. |
| `CLAUDE_CONFIG_DIR` | path, default `~/.claude` | Claude home; the default ledger lives in `<home>/disk-hygiene/`. |
| `CODEX_HOME` | path, default `~/.codex` | Codex home. |

Transcript deletion removes native resume and rewind history. Keep the default
unless you have another archive.

## Register disposable work

Every path is claimed explicitly by the session that created it. Nothing is
inferred from the current directory.

```sh
CLEANUP=<plugin root>/hooks/disk_hygiene.py
python3 "$CLEANUP" --host claude --session "$SESSION_ID" register-worktree --repo /abs/main --path /abs/main/.claude/worktrees/task
python3 "$CLEANUP" --host claude --session "$SESSION_ID" link-pr --path /abs/main/.claude/worktrees/task --pr https://github.com/owner/repo/pull/123
# Save the review evidence outside the disposable tree, then:
python3 "$CLEANUP" --host claude --session "$SESSION_ID" evidence-preserved --path /abs/main/.claude/worktrees/task --evidence /abs/main/tasks/review.md
python3 "$CLEANUP" --host claude --session "$SESSION_ID" dispose --dry-run
python3 "$CLEANUP" --host claude --session "$SESSION_ID" dispose
```

Use `--host codex --session "$CODEX_THREAD_ID"` on Codex. `create-temp --parent
<dir>` creates and registers a disposable directory. Nested git repositories are
protected: use a registered linked worktree for review checkouts. `status` lists
the current owner's paths. The SessionStart hook prints the exact command for the
installed copy.

### Evidence

Evidence is a file the session keeps after the worktree is gone, so it must
outlive the session. Save it in the project, for example
`/abs/main/tasks/review.md`. `evidence-preserved` refuses a file that session
cleanup removes under any transcript policy: a context-guard checkpoint
(`memories/checkpoints/<session>.md`), a transcript, file history, a scratchpad
or another session-keyed file of Claude Code or Codex. The file is recognised by
identity, not by spelling: on a file system that ignores letter case,
`.CLAUDE/memories/checkpoints/<SESSION>.md` is refused like the checkpoint it
opens. The same holds for a file inside a registered disposable directory.

A record written by an earlier version may name such a file. Once that file is
gone and the owner's session end is recorded, a later session working in the
same main checkout finishes the cleanup:

```sh
cd /abs/main
python3 "$CLEANUP" --host claude --session "$SESSION_ID" evidence-preserved --path /abs/main/.claude/worktrees/task --evidence /abs/main/tasks/review.md
python3 "$CLEANUP" --host claude --session "$SESSION_ID" dispose --path /abs/main/.claude/worktrees/task
```

Ownership does not change and every check listed below still applies.
`evidence-preserved` stays refused for evidence that still exists and for
evidence that was never marked. Both commands stay refused for a path of an
active owner and for a path of another repository.

`dispose --path` does not require the evidence to be lost. It finishes any path
of an ended owner registered for the same main checkout, with the evidence that
owner marked: this is the command form of what SessionStart recovery already
attempts for those paths.

The ledger defaults to `<CLAUDE_CONFIG_DIR>/disk-hygiene/worktree-cleanup.json`;
`--state` selects another one. Both hosts must share a ledger for ownership checks.

## What is removed, and when

A registered worktree is removed only if all of these hold:

- it is clean, including untracked and ignored files, unlocked, and unused by any process;
- its linked PR's live head contains every local commit (checked again after the network fetch);
- the preserved evidence file still matches its recorded hash;
- git removes the worktree and then the local branch without force.

When a registered path is already gone, `dispose` only drops its ledger entry:
a worktree Git still lists, or whose local branch survives, stays protected and
is reported with the reason. A removed worktree whose branch survives is handled
by the branch disposal, as before.

Remote PRs and remote branches are never touched. Dirty trees, unpushed commits,
replaced directories, main checkouts and other sessions' paths stay in place and
are reported.

Runtime files of a session (todos, debug logs, session env, security and
statusline state) are removed after a recognised push. The scratchpad is not: the
session that pushed is still running, and so are its subagents, whose contract and
verdict files live there. Everything of an ended session, scratchpad included, is
removed at session end. Files still open in a process are kept
and retried at later events. No periodic background sweep runs.

A push is recognised only from the command of a shell tool (`git push`,
`gh pr create`) or an MCP `create_pull_request` call. File content, grep
patterns, commit messages, heredoc bodies and `--dry-run` do not count.

## Codex transcripts

A Codex rollout is deleted only when `DISK_HYGIENE_TRANSCRIPTS=delete`, the
session ended, no writer lock is active, and a completion receipt exists at
`<CLAUDE_CONFIG_DIR>/methodology/session-end-queue/completed/`. A receipt is
written by a session-end reader such as the Cortex memory server; without one,
the rollout is kept.

## Concurrency and failure handling

Hooks that arrive together wait up to 10 seconds for the ledger and then report
`ledger busy` without changing anything. Ordinary tool calls take no lock and
write nothing. An unreadable or invalid end-of-session intake file, or one whose
session id is not a UUID, is reported on stderr and skipped; it does not block
other sessions. An intake file recorded under different Claude or Codex roots is
left untouched and silently ignored by this configuration; the configuration
that recorded it can still process it.

## Migrating from the Cortex hooks

If you used the cleanup hooks shipped with the Cortex plugins (Cortex PR 645):

1. Install `disk-hygiene` here.
2. Finish tasks registered in the old ledger (`~/.claude/methodology/worktree-cleanup.json`)
   with the old command, then remove the `disk_hygiene.py --host ... hook ...` entries from
   Cortex's `SessionStart`, `PostToolUse`, `Stop` and `SessionEnd` hooks (Claude Code) and
   from the Cortex Codex plugin's `hooks.json`.
3. Rename `CORTEX_CLEANUP_TRANSCRIPTS` to `DISK_HYGIENE_TRANSCRIPTS`, and
   `CORTEX_CLAUDE_DIR` to `CLAUDE_CONFIG_DIR`. The ledger moves to
   `<CLAUDE_CONFIG_DIR>/disk-hygiene/worktree-cleanup.json`; records in the old
   ledger are not carried over, and nothing registered there is removed by this plugin.
4. Do not run both: two copies would apply the same cleanup twice.

An independently installed older standalone hook can still delete transcripts;
this plugin's setting does not disable other commands.

## Limits

Process checks are point-in-time observations. An abrupt host termination
without a lifecycle event cannot mark a session as ended. The Claude Code
SessionEnd hook runs the purge synchronously, so a very large scratch tree may
be finished at a later event. Codex SessionEnd only records the end (its
budget is three seconds); the purge happens at the next hook event.
