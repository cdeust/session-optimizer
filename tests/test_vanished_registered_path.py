"""A registered path that is already gone: drop the entry unless Git still holds it.

source: measured 2026-10-08 08:56. `dispose --path` on a registered worktree whose
directory, Git listing and local branch were all gone answered
`[Errno 2] No such file or directory` and kept the entry for ever.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_worktree_cleanup as base

h = base.h


class VanishedPathTests(base.CleanupTests):
    def remove_directory(self):
        shutil.rmtree(self.path)

    def forget_worktree(self):
        h.git(self.repo, "worktree", "prune")

    def delete_branch(self):
        h.git(self.repo, "branch", "-D", "feature")

    def test_unlisted_worktree_without_branch_drops_its_entry(self):
        self.remove_directory()
        self.forget_worktree()
        self.delete_branch()
        self.assertIn("ledger entry", self.clean()["status"])
        self.assertNotIn(self.path, self.state)

    def test_dry_run_keeps_the_entry(self):
        self.remove_directory()
        self.forget_worktree()
        self.delete_branch()
        self.assertIn("would drop", self.clean(dry_run=True)["status"])
        self.assertIn(self.path, self.state)

    def test_worktree_git_still_lists_stays_protected(self):
        self.remove_directory()
        result = self.clean()
        self.assertIn("still lists", result["protected"])
        self.assertIn(self.path, self.state)

    def test_surviving_local_branch_stays_protected(self):
        self.remove_directory()
        self.forget_worktree()
        result = self.clean()
        self.assertIn("local branch", result["protected"])
        self.assertIn("survives", result["protected"])
        self.assertIn(self.path, self.state)
        self.assertEqual(h.git(self.repo, "rev-parse", "feature"), self.head)

    def test_pending_branch_deleted_elsewhere_drops_its_entry(self):
        self.state[self.path]["pending_branch"] = True
        self.remove_directory()
        self.forget_worktree()
        self.delete_branch()
        self.assertIn("ledger entry", self.clean()["status"])
        self.assertNotIn(self.path, self.state)

    def test_gone_temp_directory_drops_its_entry(self):
        scratch = self.root / "scratch-parent"
        scratch.mkdir()
        created = h.create_temp(self.state, self.owner, str(scratch))["created"]
        shutil.rmtree(created)
        result = h.dispose(self.state, self.owner, created)[0]
        self.assertIn("ledger entry", result["status"])
        self.assertNotIn(created, self.state)

    def test_another_owner_cannot_drop_the_entry(self):
        self.remove_directory()
        self.forget_worktree()
        self.delete_branch()
        result = h.dispose(self.state, "claude:other", self.path)[0]
        self.assertIn("not owned", result["protected"])
        self.assertIn(self.path, self.state)
