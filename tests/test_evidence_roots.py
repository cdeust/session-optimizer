"""The durability check answers for the owner's host, not for the calling process.

source: issue #55. `purge_target` read CLAUDE_CONFIG_DIR and CODEX_HOME from the
process that ran `evidence-preserved`; with other roots than the host session a
file under the host's home, which session end removes, was accepted as durable.
"""

import hashlib
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "plugins/disk-hygiene/hooks")
)
import cleanup_registry
import test_evidence_durability as durability
from cleanup_registry import Protected

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

    def legacy_record(self):
        """A record written by 0.1.1: registered, evidence marked, no roots."""
        record = self.state[self.path]
        del record["roots"]
        return dict(record)

    def project_file(self):
        project = self.root / "main-tasks" / "review.md"
        project.parent.mkdir()
        project.write_text("review evidence")
        return project

    def register_again(self, owner=None):
        h.register_worktree(
            self.state, owner or self.owner, self.path, {"repo": self.repo, "pr": None}
        )

    def test_a_record_without_roots_is_refused_at_evidence_preserved(self):
        before = self.legacy_record()
        reason = self.refusal(self.project_file())
        self.assertIn(self.path, reason)
        self.assertIn("register-worktree", reason)
        self.assertIn("owning session", reason)
        self.assertEqual(self.state[self.path], before)

    def test_a_record_without_roots_is_refused_at_dispose(self):
        before = self.legacy_record()
        result = self.clean()
        self.assertIn(self.path, result["protected"])
        self.assertIn("register-worktree", result["protected"])
        self.assertTrue(Path(self.path).exists())
        self.assertEqual(self.state[self.path], before)

    def test_registering_again_records_the_roots_and_clears_the_evidence(self):
        before = self.legacy_record()
        self.register_again()
        record = self.state[self.path]
        self.assertEqual(record["roots"]["claude_home"], str(self.root / "claude"))
        for key in h.EVIDENCE_KEYS:
            self.assertNotIn(key, record)
        kept = {k: v for k, v in before.items() if k not in h.EVIDENCE_KEYS}
        self.assertEqual({k: v for k, v in record.items() if k != "roots"}, kept)
        self.assertEqual(record["pr"], self.pr)
        self.assertIn("not been explicitly marked", self.clean()["protected"])
        h.preserve(self.state, self.owner, self.path, str(self.evidence))
        self.assertIn("removed worktree", self.clean()["status"])

    def test_evidence_a_session_purges_is_not_re_trusted_on_re_registration(self):
        """The reviewer's probe: 0.1.1 marked the owner's checkpoint as evidence."""
        checkpoint = self.session_file(durability.PURGED[0])
        record = self.state[self.path]
        record["evidence"] = str(checkpoint)
        record["evidence_sha256"] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        self.legacy_record()
        self.register_again()
        self.assertIn("not been explicitly marked", self.clean()["protected"])
        self.assertTrue(Path(self.path).exists())
        self.assertIn("outlive its session", self.refusal(checkpoint))
        self.assertIn("not been explicitly marked", self.clean()["protected"])
        h.preserve(self.state, self.owner, self.path, str(self.project_file()))
        self.assertIn("removed worktree", self.clean()["status"])

    def test_registering_again_is_refused_for_another_owner_or_recorded_roots(self):
        self.legacy_record()
        with self.assertRaisesRegex(Protected, "cannot be reassigned"):
            self.register_again("claude:other")
        self.assertNotIn("roots", self.state[self.path])
        self.register_again()
        with self.assertRaisesRegex(Protected, "already registered"):
            self.register_again()

    def test_a_vanished_record_without_roots_is_still_dropped(self):
        self.legacy_record()
        shutil.rmtree(self.path)
        h.git(self.repo, "worktree", "prune")
        self.assertIn("local branch", self.clean()["protected"])
        self.assertIn(self.path, self.state)
        h.git(self.repo, "branch", "-D", "feature")
        self.assertIn("dropped ledger entry", self.clean()["status"])
        self.assertNotIn(self.path, self.state)

    def test_a_pending_branch_without_roots_is_refused_until_it_is_gone(self):
        self.state[self.path]["pending_branch"] = True
        self.legacy_record()
        shutil.rmtree(self.path)
        h.git(self.repo, "worktree", "prune")
        result = self.clean()
        self.assertIn(self.path, result["protected"])
        self.assertIn("register-worktree", result["protected"])
        self.assertEqual(h.git(self.repo, "rev-parse", "feature"), self.head)
        h.git(self.repo, "branch", "-D", "feature")
        self.assertIn("dropped ledger entry", self.clean()["status"])
