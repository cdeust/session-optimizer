# Security assurance case

This document states what the project is trying to protect, how its controls
support that claim, and where those controls stop. It is an argument backed by
tests and repository configuration, not an independent certification.

## 1. Threat model

The protected assets are the user's local session data, prompts, filesystem,
host configuration, and confidence that installed executable files match the
reviewed release.

| Threat | Attacker capability | Intended protection |
|---|---|---|
| Malicious release or dependency | can modify an artifact or CI reference | pinned actions, least-privilege workflows, checksums, executable manifest, SBOM, and provenance attestation |
| Crafted hook payload or transcript | can supply malformed, large, or adversarial local input | parsing, bounded reads already present in hook implementations, explicit fallback behavior, and regression tests |
| Unsafe archive | can replace a downloaded bundle and companion files | checksum-before-extract, path-traversal rejection, and per-executable hashes |
| Accidental maintainer error | can merge a defect or stale security claim | pull-request CI, 80% coverage gate, CodeQL, ShellCheck, Scorecard, and documented review evidence |

Out of scope: containing a hostile host process that already runs with the
user's permissions; protecting a machine or GitHub account that is already
compromised; and guaranteeing the correctness of model-generated advice.

## 2. Trust boundaries

1. **Repository to CI runner.** Workflow source and action references cross
   into GitHub-hosted execution. Actions are pinned to full commits and jobs
   receive only stated permissions. This does not protect a compromised GitHub
   account with authority to change the workflow.
2. **Release service to installer.** The archive, checksums, manifest, SBOM,
   and attestation cross the network. Verification detects modification after
   publication; it cannot make unreviewed source safe.
3. **Host to hook.** Host JSON crosses standard input into Python or shell.
   Parsers validate required shapes and tests exercise malformed inputs. The
   hook still runs with the user's filesystem permissions.
4. **Transcript/configuration files to renderer.** Local content and git output
   enter parsers and shell formatting. Values are treated as data, not sourced
   as shell code. This does not make the surrounding host transcript private
   from other local processes running as the same user.
5. **Portable skill to model.** Host-neutral instructions enter an agent's
   context. The skill can structure decisions but cannot enforce a sandbox or
   prove the model followed every instruction.

## 3. Secure design principles

- **Least privilege:** CI begins read-only; only Scorecard SARIF upload,
  CodeQL, release publication, and attestation receive the additional
  permissions they require.
- **Economy of mechanism:** the three plugins remain installable and testable
  independently, and release verification is a small standalone script.
- **Complete mediation at artifact boundaries:** the bundle is hashed before
  extraction and every executable listed in the release manifest is rehashed
  after extraction.
- **Fail-safe release defaults:** malformed checksums, unsafe archive members,
  missing executables, and mismatches terminate verification with failure.
- **Transparent runtime limits:** host-specific behavior is labeled rather
  than inferred from the portable skill package; privacy and local file access
  are documented.
- **Defence in depth:** tests, static analysis, dependency updates, Scorecard,
  checksums, SBOM, and attestation address different failure modes. No one
  layer is described as sufficient.

## 4. Common implementation weaknesses

| Weakness | Control | Limit |
|---|---|---|
| command or expression injection | ShellCheck, CodeQL, quoted paths, and tests with hostile values | static analysis does not prove all shell composition safe |
| unsafe deserialization or malformed JSON | standard JSON parsers, shape checks, and malformed-input tests | the host remains responsible for transport framing |
| path traversal during installation | archive member validation before extraction | verification must actually be run by the installer/user |
| dependency substitution | hash-locked Python test dependencies and full-SHA actions | the shipped runtime intentionally uses the standard library but relies on the host OS and Python |
| unreviewed executable drift | executable manifest and per-file hashes | non-executable documentation is covered by the archive checksum, not the executable manifest |
| resource exhaustion | existing bounded transcript/event reads and regression tests | a same-user hostile process can still consume machine resources externally |
| silent security regression | CI coverage gate, behavior tests, CodeQL, and Scorecard | a green gate proves only the encoded checks |
| release tampering | SHA-256, Sigstore provenance, SBOM, and verification tool | historical releases gain no attestation retroactively |

The author currently performs both implementation and review, and the controls
have not been evaluated by an independent security assessor. That concentration
of authority keeps the bus factor at 1 even though the public MIT repository
and credential-free fork/release path provide operational continuity.
