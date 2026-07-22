# Privacy Policy — session-optimizer plugins

_Last updated: 2026-07-22_

The three plugins in this marketplace (context-guard, refine-gate, statusline)
are **local-only** tools. This policy states exactly what data they read and
write, and what leaves your machine.

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

## What leaves your machine

**Nothing.** No plugin in this marketplace makes any network call, sends
telemetry, or transmits any content to the author, to Anthropic, or to any
third party. All processing is local subprocesses (bash/Python) reading and
writing files under your home directory.

## Your controls

- Uninstall the plugin(s) to stop all processing.
- Delete `~/.claude/.statusline-*` cache files and checkpoint stubs at any
  time; nothing else is persisted.

## Contact

admin@ai-architect.tools · https://github.com/cdeust/session-optimizer/issues
