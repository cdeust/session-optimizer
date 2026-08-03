# OpenSSF Scorecard policy

`.github/workflows/scorecard.yml` runs OpenSSF Scorecard weekly, on changes to
`main`, when branch protection changes, and on manual request. It publishes
SARIF to GitHub code scanning and public aggregate results to the Scorecard API.

Findings are handled as evidence, not as a target score:

- actionable repository or workflow defects are fixed in a pull request;
- findings that require a project property we do not have are documented here
  and left unsatisfied; and
- a criterion is never marked met solely because a workflow file exists.

## Known dispositions

| Area | Current disposition |
|---|---|
| Branch protection and code review | Desired, but a sole-maintainer project cannot provide independent approval today. Required status checks should be enabled after the new workflows have completed successfully on `main`. |
| Contributors / bus factor | Unmet: `@cdeust` is the only administrator and maintainer. See `GOVERNANCE.md`. |
| Signed releases | Workflow prepared; unmet until a new tagged release completes and the public attestation is verified. |
| Fuzzing | Not currently run. Parser behavior is covered by deterministic tests; fuzzing will be adopted only with a maintained target and reproducible evidence. |
| Packaging | Source bundle, checksums, executable manifest, CycloneDX SBOM, and provenance attestation are produced by the release workflow. |
| Token permissions | Default workflow permissions are read-only; write scopes are job-local and purpose-specific. |

The latest service result is authoritative once the workflow has landed and
run on `main`: <https://securityscorecards.dev/viewer/?uri=github.com/cdeust/session-optimizer>.
