"""An ended owner's evidence that is unusable can be marked again by a later session.

source: issue #53. Evidence that is a dangling symlink, or whose content changed,
fell outside `evidence_lost` (the file is still there): the owner session is gone,
nobody could mark the evidence again, and the worktree stayed protected.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_evidence_durability as durability

h = durability.h
Protected = durability.Protected


class UnusableEvidenceTests(durability.DurabilityFixture):
    def dangle(self):
        self.evidence.unlink()
        self.evidence.symlink_to(self.root / "nowhere.md")
        self.assertTrue(self.evidence.is_symlink())
        self.assertFalse(self.evidence.exists())

    def change(self):
        self.evidence.write_text("edited after it was marked")

    def end_owner(self):
        self.state["_ended"] = {self.owner: True}

    def replacement(self):
        again = self.root / "again.md"
        again.write_text("review evidence, saved again")
        return str(again)

    def test_a_later_session_marks_dangling_evidence_again(self):
        self.dangle()
        self.end_owner()
        self.command_line("evidence-preserved", evidence=self.replacement())
        self.assertEqual(self.state[self.path]["owner"], self.owner)
        result = self.command_line("dispose", dry_run=False)
        self.assertIn("removed worktree and local branch", result[0]["status"])
        self.assertNotIn(self.path, self.state)

    def test_a_later_session_marks_changed_evidence_again(self):
        self.change()
        self.end_owner()
        self.command_line("evidence-preserved", evidence=self.replacement())
        result = self.command_line("dispose", dry_run=False)
        self.assertIn("removed worktree and local branch", result[0]["status"])
        self.assertNotIn(self.path, self.state)

    def assert_dispose_names_the_remedy(self):
        self.end_owner()
        reason = self.protected("dispose", dry_run=False)
        self.assertIn("evidence-preserved", reason)
        self.assertTrue(Path(self.path).exists())

    def test_dispose_by_path_names_the_remedy_for_dangling_evidence(self):
        self.dangle()
        self.assert_dispose_names_the_remedy()

    def test_dispose_by_path_names_the_remedy_for_changed_evidence(self):
        self.change()
        self.assert_dispose_names_the_remedy()

    def test_unusable_evidence_of_an_active_owner_is_not_acted_for(self):
        self.dangle()
        self.assertIn(
            "not owned",
            self.protected("evidence-preserved", evidence=self.replacement()),
        )
        self.assertTrue(Path(self.path).exists())

    def test_usable_evidence_of_an_ended_owner_is_still_not_replaced(self):
        self.end_owner()
        with self.assertRaises(Protected) as refusal:
            self.command_line("evidence-preserved", evidence=self.replacement())
        self.assertIn("recorded file is gone", str(refusal.exception))
