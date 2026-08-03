# Roadmap: August 2026 to July 2027

This roadmap expresses priorities, not promises. Evidence from security review,
host API changes, or user reports may reorder it; any change is recorded here.

## August–October 2026

- Land the OpenSSF foundation: harden CI, enable CodeQL, Scorecard and
  Dependabot, publish the governance and assurance documents, and enforce 80%
  statement coverage over shipped Python.
- Exercise the release workflow on the next semantic-version tag and
  independently verify its checksum, executable manifest, SBOM, and Sigstore
  attestation before marking signed releases as satisfied.
- Register the project on OpenSSF Best Practices and publish only criteria that
  have repository or service evidence.

## November 2026–January 2027

- Keep `refine-gate`'s portable Agent Skill validated on Codex and Gemini CLI.
- Document host-neutral behavior separately from Claude lifecycle-hook,
  transcript, and statusline integrations.
- Ratchet tests when defects are found while keeping the enforced coverage
  floor at or above 80%.

## February–April 2027

- Recruit and onboard a second trusted maintainer with tested issue, merge,
  advisory, and release permissions to preserve the existing repository
  identity as well as the documented fork-based continuity path.
- Review whether cryptographically signed version tags add useful assurance on
  top of artifact attestations.
- Reassess parser fuzzing from measured defect history rather than claiming a
  tool that is not being run.

## May–July 2027

- Audit documentation accessibility and internationalization boundaries.
- Review all OpenSSF Best Practices Silver evidence and complete an honest Gold
  gap assessment.
- Re-evaluate host integrations as Codex and Gemini expose new lifecycle APIs.

## Explicitly out of scope

- Claiming that Claude-only hooks work on hosts without equivalent APIs.
- Adding SaaS, telemetry, or runtime network dependencies to local plugins.
- Trading transparent security limits for a higher badge percentage.
