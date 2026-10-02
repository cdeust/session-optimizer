"""Evidence must outlive its session, and evidence lost that way is recoverable.

source: measured 2026-10-02. A worktree whose evidence was the context-guard
checkpoint of its session stayed protected for ever: session end removed the
checkpoint, `dispose` answered "No such file or directory", and no other
session could mark the evidence again.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "plugins/disk-hygiene/hooks")
)
import cleanup_hooks as hooks
import cleanup_operations as h
import disk_hygiene
import session_purge
import test_worktree_cleanup as base
from cleanup_registry import Protected

SID = "5467d88a-0e9d-4995-8678-491441f86a8d"
PURGED = (
    "claude/memories/checkpoints/{sid}.md",
    "claude/projects/project/{sid}.jsonl",
    "claude/projects/project/{sid}/subagents/agent.jsonl",
    "claude/file-history/{sid}/review.md",
    "tmp/claude-{uid}/project/{sid}/scratchpad/review.md",
    "codex/sessions/2026/10/02/rollout-test-{sid}.jsonl",
    "codex/shell_snapshots/{sid}.sh",
)


class DurabilityTests(base.CleanupTests):
    def setUp(self):
        super().setUp()
        roots = {
            "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
            "CLAUDE_CODE_TMPDIR": str(self.root / "tmp"),
            "CODEX_HOME": str(self.root / "codex"),
        }
        env = patch.dict(os.environ, roots)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("DISK_HYGIENE_TRANSCRIPTS", None)
        self.new = "claude:new"

    def session_file(self, pattern, sid=SID):
        target = self.root / pattern.format(sid=sid, uid=os.getuid())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("review evidence")
        return target

    def command_line(self, command, owner=None, **options):
        args = SimpleNamespace(command=command, path=self.path, **options)
        with patch.object(disk_hygiene.Path, "cwd", return_value=Path(self.repo)):
            return disk_hygiene.execute(self.state, owner or self.new, args, {})

    def lose_evidence(self):
        """A record written before the durability check, then its session ends."""
        checkpoint = self.session_file(PURGED[0])
        self.state[self.path]["evidence"] = str(checkpoint)
        self.state[self.path]["evidence_sha256"] = "0" * 64
        self.state["_ended"] = {self.owner: True}
        with patch.dict(os.environ, {"DISK_HYGIENE_TRANSCRIPTS": "delete"}):
            session_purge.purge_ended(
                (session_purge.claude_home(), session_purge.temp_root()),
                SID,
                lambda path: None,
                wait=lambda: True,
            )
        self.assertFalse(checkpoint.exists())

    def test_evidence_the_purgers_select_is_refused(self):
        before = dict(self.state[self.path])
        for pattern in PURGED:
            with self.subTest(pattern):
                evidence = str(self.session_file(pattern))
                with self.assertRaises(Protected) as refusal:
                    h.preserve(self.state, self.owner, self.path, evidence)
                self.assertIn("outlive its session", str(refusal.exception))
        self.assertEqual(self.state[self.path], before)

    def test_refusal_does_not_depend_on_the_retention_policy(self):
        for policy in ("keep", "delete"):
            with (
                patch.dict(os.environ, {"DISK_HYGIENE_TRANSCRIPTS": policy}),
                self.assertRaises(Protected),
            ):
                h.preserve(
                    self.state,
                    self.owner,
                    self.path,
                    str(self.session_file(PURGED[0])),
                )

    def test_project_file_named_after_a_session_is_accepted(self):
        review = self.session_file("main-tasks/review-{sid}.md")
        h.preserve(self.state, self.owner, self.path, str(review))
        self.assertEqual(self.state[self.path]["evidence"], str(review))
        self.assertIn("removed worktree", self.clean()["status"])

    def test_lost_evidence_is_marked_again_and_disposed_by_a_later_session(self):
        self.lose_evidence()
        self.assertIn("No such file", self.clean()["protected"])
        self.command_line("evidence-preserved", evidence=str(self.evidence))
        self.assertEqual(self.state[self.path]["owner"], self.owner)
        result = self.command_line("dispose", dry_run=False)
        self.assertIn("removed worktree and local branch", result[0]["status"])
        self.assertFalse(Path(self.path).exists())
        self.assertNotIn(self.path, self.state)

    def test_active_owner_is_never_acted_for(self):
        self.lose_evidence()
        self.state["_ended"] = {}
        for command, options in (
            ("evidence-preserved", {"evidence": str(self.evidence)}),
            ("dispose", {"dry_run": False}),
        ):
            with self.subTest(command):
                try:
                    result = self.command_line(command, **options)
                except Protected as refusal:
                    result = [{"protected": str(refusal)}]
                self.assertIn("not owned", result[0]["protected"])
        self.assertTrue(Path(self.path).exists())

    def test_another_repository_is_never_acted_for(self):
        self.lose_evidence()
        with (
            patch.object(hooks, "scope", return_value="/another/repo"),
            self.assertRaises(Protected),
        ):
            self.command_line("evidence-preserved", evidence=str(self.evidence))
        self.assertTrue(Path(self.path).exists())

    def test_present_evidence_of_an_ended_owner_is_not_replaced(self):
        self.state["_ended"] = {self.owner: True}
        other = self.root / "other.md"
        other.write_text("replacement")
        with self.assertRaises(Protected) as refusal:
            self.command_line("evidence-preserved", evidence=str(other))
        self.assertIn("recorded file is gone", str(refusal.exception))
        self.assertEqual(self.state[self.path]["evidence"], str(self.evidence))

    def test_evidence_never_marked_stays_the_owner_s_omission(self):
        self.state["_ended"] = {self.owner: True}
        for key in ("evidence", "evidence_sha256", "evidence_head"):
            del self.state[self.path][key]
        with self.assertRaises(Protected):
            self.command_line("evidence-preserved", evidence=str(self.evidence))
        self.assertNotIn("evidence", self.state[self.path])

    def test_replacement_evidence_is_held_to_the_same_rule(self):
        self.lose_evidence()
        with self.assertRaises(Protected) as refusal:
            self.command_line(
                "evidence-preserved",
                evidence=str(self.session_file(PURGED[1], sid=SID.replace("5", "6"))),
            )
        self.assertIn("outlive its session", str(refusal.exception))

    def test_pending_branch_keeps_its_verified_head_when_marked_again(self):
        def fail(argv, cwd=None):
            if "branch" in argv and "-d" in argv:
                raise Protected("injected branch rejection")
            return self.command(argv, cwd)

        with patch.object(h, "run", side_effect=fail):
            self.assertIn("protected", self.clean())
        self.assertTrue(self.state[self.path]["pending_branch"])
        self.evidence.unlink()
        self.state["_ended"] = {self.owner: True}
        again = self.root / "again.md"
        again.write_text("review evidence, saved again")
        self.command_line("evidence-preserved", evidence=str(again))
        self.assertEqual(self.state[self.path]["evidence_head"], self.head)
        result = self.command_line("dispose", dry_run=False)
        self.assertIn("local branch feature", result[0]["status"])
        self.assertEqual(h.git(self.repo, "branch", "--list", "feature"), "")

    def test_startup_instruction_names_where_evidence_belongs(self):
        args = SimpleNamespace(event="SessionStart", host="claude", session="new")
        context = disk_hygiene.hook_result(
            self.state, self.new, args, {"cwd": self.repo}
        )["hookSpecificOutput"]["additionalContext"]
        self.assertIn("project file", context)
        self.assertIn("checkpoint", context)

    def test_end_markers_without_a_registered_path_are_dropped_at_start(self):
        del self.state[self.path]
        self.state["_ended"] = {"codex:gone": True, "claude:gone": True}
        args = SimpleNamespace(event="SessionStart", host="claude", session="new")
        disk_hygiene.hook_result(self.state, self.new, args, {"cwd": self.repo})
        self.assertEqual(self.state["_ended"], {})

    def test_host_without_user_ids_still_refuses_home_files(self):
        checkpoint = str(self.session_file(PURGED[0]))
        with (
            patch.object(os, "getuid", create=True),
            self.assertRaises(Protected),
        ):
            del os.getuid
            h.preserve(self.state, self.owner, self.path, checkpoint)
