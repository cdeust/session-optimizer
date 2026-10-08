"""Evidence inside a registered worktree's Git admin directory does not outlive it.

source: issue #54. `git worktree remove` deletes <main>/.git/worktrees/<name>/.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_worktree_cleanup as base

h = base.h
Protected = h.Protected


class AdminDirectoryTests(base.CleanupFixture):
    def test_evidence_in_the_git_admin_directory_of_a_worktree_is_refused(self):
        """`git worktree remove` deletes <main>/.git/worktrees/<name>/."""
        admin = Path(h.git(self.path, "rev-parse", "--absolute-git-dir"))
        self.assertEqual(admin.parent.name, "worktrees")
        evidence = admin / "review.md"
        evidence.write_text("review evidence")
        before = dict(self.state[self.path])
        with self.assertRaises(Protected) as refusal:
            h.preserve(self.state, self.owner, self.path, str(evidence))
        self.assertIn("git worktree remove", str(refusal.exception))
        self.assertEqual(self.state[self.path], before)

    def test_evidence_in_the_main_git_directory_is_not_a_worktree_admin_directory(
        self,
    ):
        evidence = Path(self.repo, ".git", "review.md")
        evidence.write_text("review evidence")
        h.preserve(self.state, self.owner, self.path, str(evidence))
        self.assertEqual(self.state[self.path]["evidence"], str(evidence))
