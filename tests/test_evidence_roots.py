"""The durability check answers for the owner's host, not for the calling process.

source: issue #55. `purge_target` read CLAUDE_CONFIG_DIR and CODEX_HOME from the
process that ran `evidence-preserved`; with other roots than the host session a
file under the host's home, which session end removes, was accepted as durable.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "plugins/disk-hygiene/hooks")
)
import cleanup_registry
import test_evidence_durability as durability

h = durability.h
SID = durability.SID


class OwnerRootsTests(durability.DurabilityFixture):
    def elsewhere(self):
        """The environment of a process that is not the owner's host."""
        return patch.dict(
            os.environ,
            {
                "CLAUDE_CONFIG_DIR": str(self.root / "other-claude"),
                "CODEX_HOME": str(self.root / "other-codex"),
            },
        )

    def test_registration_records_the_roots_of_the_owner_s_host(self):
        roots = self.state[self.path]["roots"]
        self.assertEqual(roots["claude_home"], str(self.root / "claude"))
        self.assertEqual(roots["codex_home"], str(self.root / "codex"))
        self.assertEqual(roots["claude_tmp"], str(self.root / "tmp"))
        # The ledger refuses any other key set, so the producer must emit this one.
        self.assertEqual(set(roots), set(cleanup_registry.ROOT_KEYS))

    def test_a_file_under_the_host_home_is_refused_from_other_roots(self):
        checkpoint = self.session_file(durability.PURGED[0])
        with self.elsewhere():
            reason = self.refusal(checkpoint)
        self.assertIn("roots", reason)
        self.assertIn("claude_home", reason)
        self.assertEqual(self.state[self.path]["evidence"], str(self.evidence))

    def test_a_project_file_is_refused_from_other_roots_too(self):
        project = self.root / "main-tasks" / "review.md"
        project.parent.mkdir()
        project.write_text("review evidence")
        with self.elsewhere():
            self.assertIn("roots", self.refusal(project))
        self.assertEqual(self.state[self.path]["evidence"], str(self.evidence))

    def test_the_check_uses_the_given_roots_not_the_environment(self):
        checkpoint = self.session_file(durability.PURGED[0])
        roots = dict(self.state[self.path]["roots"])
        with self.elsewhere():
            target = durability.h.purge_target(checkpoint, roots)
        self.assertEqual(Path(target), checkpoint)

    def test_a_record_without_roots_pins_the_calling_process_roots_once(self):
        record = self.state[self.path]
        del record["roots"]
        project = self.root / "main-tasks" / "review.md"
        project.parent.mkdir()
        project.write_text("review evidence")
        h.preserve(self.state, self.owner, self.path, str(project))
        self.assertEqual(record["roots"]["claude_home"], str(self.root / "claude"))
        with self.elsewhere():
            self.assertIn("roots", self.refusal(project))
