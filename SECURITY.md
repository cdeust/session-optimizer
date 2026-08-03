# Security policy

## Supported versions

Only the latest release is supported with security fixes. Upgrade before
reporting a problem that is already fixed on `main` or in a newer release.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's
[private vulnerability report](https://github.com/cdeust/session-optimizer/security/advisories/new)
and include:

- the affected plugin, version, and host;
- the smallest reproducible input or transcript shape;
- the security impact and the local files or privileges involved; and
- any proposed mitigation, if known.

The maintainer will reproduce and scope the report in the private advisory,
prepare a fix and regression test on a private fork or advisory branch, and
coordinate disclosure with the reporter. A fixed release will identify the
affected versions and credit the reporter unless anonymity is requested. No
response or repair deadline is promised before it has been measured reliably;
progress and any revised disclosure date will be recorded in the advisory.

## Security boundaries

The shipped plugins run locally with the user's permissions. They do not
provide a sandbox and must be reviewed like any other executable hook.

- `context-guard` consumes host hook JSON and reads local transcripts.
- `refine-gate` consumes the submitted prompt. Its portable Agent Skill is
  prose; Claude's optional hook is executable Python.
- `statusline` consumes host status JSON, transcripts, configuration, and local
  git metadata.
- None of the runtime components intentionally makes a network request. See
  [PRIVACY.md](PRIVACY.md).

Malformed host events fail without granting new privileges. Release and
installation integrity checks fail closed: the bundle checksum is verified
before extraction, unsafe archive paths are rejected, and every shipped Python
or shell executable is checked against the release manifest.

The complete threat model, trust boundaries, controls, and their limits are in
[docs/ASSURANCE-CASE.md](docs/ASSURANCE-CASE.md).

## Verifying a release

Tagged releases are produced by `.github/workflows/release.yml`. The workflow
tests the repository, builds the source bundle, publishes SHA-256 checksums, an
executable manifest, a CycloneDX SBOM, and Sigstore build-provenance
attestations.

```bash
sha256sum -c session-optimizer.tar.gz.sha256
gh attestation verify session-optimizer.tar.gz --repo cdeust/session-optimizer
bash tools/verify-release-bundle.sh \
  session-optimizer.tar.gz \
  session-optimizer.tar.gz.sha256 \
  EXECUTABLE-MANIFEST.sha256
```

The workflow is prepared but does not make historical releases signed. The
OpenSSF signed-release criterion remains unmet until a new public tag completes
this workflow and its attestation is independently verified.
