#!/usr/bin/env bash
# source: https://github.com/cdeust/session-optimizer/tree/c1e7a07fb2288084b66dc7ff63336b7062f4cced
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
case "${1:-}" in
  setup)
    python -m pip install --require-hashes --no-deps -r requirements-dev.txt
    python3.9 -c 'import sys; assert sys.version_info[:2] == (3, 9)'
    ;;
  unit)
    python -m coverage erase
    python -m coverage run -m pytest -q
    python -m coverage combine
    python -m coverage report
    ;;
  regression)
    bash tests/statusline/test_heat_rgb.sh
    bash tests/statusline/test_fit_and_pace.sh
    ;;
  e2e) bash tests/statusline/test_session_snapshot.sh ;;
  environment)
    python tools/check-lock-drift.py requirements-dev.in requirements-dev.txt
    python -m pytest -q tests/test_python39_floor.py
    ;;
  installation)
    private_home=$(mktemp -d "${RUNNER_TEMP:?}/statusline-install.XXXXXX")
    trap 'rm -rf "$private_home"' EXIT
    HOME="$private_home" bash plugins/statusline/install.sh install
    HOME="$private_home" bash plugins/statusline/install.sh verify
    bash tools/build-release-bundle.sh "$private_home/dist"
    bash tools/verify-release-bundle.sh "$private_home/dist/session-optimizer.tar.gz" \
      "$private_home/dist/session-optimizer.tar.gz.sha256" "$private_home/dist/EXECUTABLE-MANIFEST.sha256"
    ;;
  release-version)
    python -c 'import json; print("tag=v"+json.load(open(".claude-plugin/marketplace.json"))["metadata"]["version"])'
    ;;
  *) echo 'Usage: run.sh setup|unit|regression|e2e|environment|installation|release-version' >&2; exit 2 ;;
esac
