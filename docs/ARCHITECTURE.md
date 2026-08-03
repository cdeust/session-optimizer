# Architecture

session-optimizer is a distribution repository for three independent tools.
There is no resident server and no shared runtime dependency.

## Components

| Component | Portable surface | Host-specific surface | Local data |
|---|---|---|---|
| `refine-gate` | Agent Skill instructions for Codex, Gemini CLI, and compatible hosts | Claude `UserPromptSubmit` hook | submitted prompt and a small local rate-limit state file |
| `context-guard` | checkpoint protocol concepts | Claude `Stop` and `SubagentStop` hooks and transcript layout | host events, transcripts, checkpoints, and `/tmp` session counters |
| `statusline` | none today | Claude statusline payload, transcript layout, and install hook | host payload, transcripts, git metadata, config, and caches |

The portable and Claude packages reference the same `refine-gate` skill source.
The marketplace manifests select which surfaces each host installs; they do not
make a host-specific executable portable.

## Runtime flow

1. The host invokes a hook or statusline executable with JSON on standard
   input, or an agent loads the portable skill text.
2. The executable validates the event shape and reads only the local files its
   component documents.
3. It returns hook JSON or rendered text on standard output and may update its
   documented local state.
4. It makes no intentional network request and does not call another plugin.

`context-guard` and `statusline` may read the same threshold file and session
spend record. This is an optional file contract: either plugin continues when
the other is absent.

## Build and release flow

The source files are the product. `tools/build-release-bundle.sh` creates a
source archive, SHA-256 executable manifest, and CycloneDX file inventory.
`tools/verify-release-bundle.sh` verifies the archive before extraction,
rejects unsafe paths, and compares extracted executables with the manifest.
The release workflow tests first, then attests and publishes those artifacts.

See [ASSURANCE-CASE.md](ASSURANCE-CASE.md) for the security boundaries and
[PRIVACY.md](../PRIVACY.md) for the local data contract.
