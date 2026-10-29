"""Installation must fail at every CLI boundary in the actual CI adapter.

The shared workflow runs CI_COMMAND using bash -eo pipefail. A for-loop on
the left side of && does not stop at an intermediate failing iteration.
"""

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = "/fixture/workspace with spaces"
CALLS = [
    ["plugin", "marketplace", "add", WORKSPACE],
    ["plugin", "install", "context-guard@session-optimizer-marketplace"],
    ["plugin", "install", "refine-gate@session-optimizer-marketplace"],
    ["plugin", "install", "statusline@session-optimizer-marketplace"],
    ["plugin", "list"],
]
CLI_FIXTURE = r"""
claude() {
  printf 'CALL'
  printf '\t%s' "$@"
  printf '\n'
  if [ "$*" = "$CI_INSTALL_FAIL_CALL" ]; then return 42; fi
  return 0
}
"""


class InstallCommandTests(unittest.TestCase):
    def run_adapter(self, failed_call=None):
        command = json.loads((ROOT / "ci.json").read_text())["install"]
        environment = dict(
            os.environ,
            GITHUB_WORKSPACE=WORKSPACE,
            CI_INSTALL_FAIL_CALL=" ".join(failed_call or []),
        )
        result = subprocess.run(
            ["bash", "-eo", "pipefail", "-c", CLI_FIXTURE + command],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        calls = [line.split("\t")[1:] for line in result.stdout.splitlines()]
        return result, calls

    def assert_stops_at(self, failed_call):
        result, calls = self.run_adapter(failed_call)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(calls, CALLS[: CALLS.index(failed_call) + 1])

    def test_success_installs_every_plugin_and_lists_afterwards(self):
        result, calls = self.run_adapter()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, CALLS)

    def test_marketplace_failure_prevents_installation(self):
        self.assert_stops_at(CALLS[0])

    def test_first_install_failure_prevents_other_plugins_and_list(self):
        self.assert_stops_at(CALLS[1])

    def test_middle_install_failure_prevents_last_plugin_and_list(self):
        self.assert_stops_at(CALLS[2])

    def test_last_install_failure_prevents_list(self):
        self.assert_stops_at(CALLS[3])

    def test_list_failure_is_reported(self):
        self.assert_stops_at(CALLS[4])


if __name__ == "__main__":
    unittest.main()
