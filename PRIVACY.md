# Privacy Policy — session-optimizer plugins

_Last updated: 2026-11-24_

This policy covers the data processed by the marketplace plugins.

## What the plugins process

- **context-guard** reads your Claude Code session transcripts
  (`~/.claude/projects/**/*.jsonl`) and subagent transcripts to compute token
  usage and spend, and writes checkpoint stub files under your home directory.
  The Stop/SubagentStop hooks run local Python only.
- **refine-gate** reads the prompt you submit (inside the UserPromptSubmit
  hook) and injects binding instructions into the same session. Nothing is
  stored beyond a small local state file used to rate-limit the gate.
- **statusline** reads Claude Code's statusline stdin payload, your session
  transcripts (for telemetry and cost aggregation), local git state, and its
  own config files under `~/.claude/`. It writes local cache files only.
- **disk-hygiene** stores registered paths, explicit session owners, repository
  and branch names, linked PR URLs, evidence digests and verified commit IDs
  in a local ledger.

## What leaves your machine

Context-guard, refine-gate and statusline process data locally. When
disk-hygiene verifies a linked PR, including during a branch migration or
its dry run, GitHub receives a request for that PR through the authenticated
GitHub CLI. Worktree disposal also fetches the linked PR ref through Git.
Evidence contents, filesystem paths and host session IDs stay local.

## Your controls

- Uninstall the plugin(s) to stop all processing.
- Delete `~/.claude/.statusline-*` cache files and checkpoint stubs at any
  time. Set `DISK_HYGIENE_CLEANUP=off` to disable disk-hygiene cleanup.

## Contact

admin@ai-architect.tools · https://github.com/cdeust/session-optimizer/issues
