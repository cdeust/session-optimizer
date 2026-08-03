# Contributing

Thank you for improving session-optimizer. Please follow the
[Code of Conduct](CODE_OF_CONDUCT.md) and report vulnerabilities through the
private process in [SECURITY.md](SECURITY.md), not a public issue.

## Development setup

Python 3.11+, Bash, and Git are required. ShellCheck 0.11.0 is required for the
shell gate; CI verifies the downloaded archive before installing it.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
```

## Change process

1. Open or reference an issue that states the observable problem.
2. Create a focused branch and keep unrelated changes out of it.
3. Add or update documentation and tests in the same pull request.
4. Run the complete local gate below.
5. Explain the user-visible change, security impact, and test evidence in the
   pull request.

Changes are accepted by pull request. The maintainer records the decision in
the review and merges only after required automated checks pass. Small typo or
link fixes may omit an issue when the pull request is self-explanatory.

## Testing policy

Every change to observable behaviour must carry automated tests in the same
pull request. A bug fix must include a regression test that fails on the
unfixed code. Tests must cover the happy path, each newly introduced input
boundary, and failure behaviour that callers rely on. The measured shipped
Python surface must remain at or above 80% statement coverage; a passing
percentage does not replace behaviour assertions.

```bash
coverage erase
coverage run -m pytest -q
coverage combine
coverage report
bash tests/statusline/test_heat_rgb.sh
bash tests/statusline/test_fit_and_pace.sh
shellcheck plugins/statusline/assets/statusline-command.sh \
  plugins/statusline/assets/statusline-lib/*.sh \
  plugins/statusline/assets/costs.sh \
  tests/statusline/*.sh
```

Validate every changed JSON file with `python -m json.tool FILE`. Release
changes must also pass:

```bash
bash tools/build-release-bundle.sh dist
bash tools/verify-release-bundle.sh \
  dist/session-optimizer.tar.gz \
  dist/session-optimizer.tar.gz.sha256 \
  dist/EXECUTABLE-MANIFEST.sha256
```

## Style and compatibility

- Keep the three plugins independently installable.
- Preserve the portable Agent Skill contract for Codex and Gemini while
  identifying Claude-only hook, transcript, and statusline behavior plainly.
- Prefer standard-library Python and dependency-light shell.
- Treat hook JSON, transcript content, paths, and git output as untrusted input.
- Never add telemetry or a runtime network request without an explicit design
  review and an update to `PRIVACY.md`.

Contributions are provided under the repository's MIT license. The project
does not currently require a Contributor License Agreement or DCO sign-off.
