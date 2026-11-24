import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "plugins/disk-hygiene/hooks")
)
import cleanup_hooks
import cleanup_operations as h
import disk_hygiene as cli
import test_worktree_cleanup as base
from cleanup_hooks import acting_owner
from cleanup_registry import valid_oid, validate


class BranchMigrationTests(base.CleanupFixture):
    def setUp(self):
        super().setUp()
        self.real_run(
            [
                "git",
                "-C",
                self.repo,
                "remote",
                "set-url",
                "origin",
                "git@github.com:example/repo.git",
            ]
        )
        self.args = SimpleNamespace(
            command="migrate-pr-branch",
            path=self.path,
            pr=self.pr,
            pr_branch="fix/feature",
            dry_run=False,
        )
        self.response = {
            "url": self.pr,
            "headRefName": "fix/feature",
            "headRefOid": self.head,
        }

    def command(self, argv, cwd=None):
        if argv[0] == "gh":
            self.calls.append(argv)
            return json.dumps(self.response)
        return super().command(argv, cwd)

    def migrate(self, **kwargs):
        return cli.execute(self.state, kwargs.get("owner", self.owner), self.args, {})

    def refused(self, reason, **kwargs):
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(h.Protected, reason):
            self.migrate(**kwargs)
        self.assertEqual(self.state, before)
        self.assertTrue(Path(self.path).is_dir())

    def test_dirty_worktree_migrates_only_the_pr_binding_and_retains_local_ref(self):
        record = self.state[self.path]
        before = copy.deepcopy(record)
        Path(self.path, "active-work").write_text("keep")
        self.args.dry_run = True
        result = self.migrate()
        self.assertEqual(record, before)
        self.assertTrue(result["dry_run"])
        self.args.dry_run = False
        expected = {"url": self.pr, "ref": "refs/heads/fix/feature", "sha": self.head}
        self.assertEqual(
            self.migrate(),
            {
                "path": self.path,
                "local_ref": "refs/heads/feature",
                "pr_head": expected,
                "dry_run": False,
            },
        )
        self.assertEqual(record, {**before, "pr_head": expected})
        self.assertEqual(self.migrate()["pr_head"], expected)
        self.assertIn("uncommitted", self.clean(dry_run=True)["protected"])
        Path(self.path, "active-work").unlink()
        self.assertIn("would remove worktree", self.clean(dry_run=True)["status"])
        self.assertIn("and local branch feature", self.clean()["status"])
        self.assertFalse(Path(self.path).exists())

    def test_owner_inode_kind_pending_and_exact_link_are_required(self):
        self.state["_ended"] = {self.owner: True}
        self.refused("not owned", owner="claude:test")
        self.refused("not owned", owner="codex:other")
        for change, reason in [
            ({"identity": [0, 0]}, "replaced"),
            ({"kind": "temp"}, "existing linked worktree"),
            ({"pending_branch": True}, "existing linked worktree"),
            ({"pr": None}, "already linked"),
            ({"pr": self.pr + "0"}, "already linked"),
            ({"branch": "refs/heads/other"}, "branch changed"),
        ]:
            with self.subTest(change=change), patch.dict(self.state[self.path], change):
                self.refused(reason)
        self.assertFalse(any(cmd[0] == "gh" for cmd in self.calls))

    def test_pr_url_branch_sha_local_origin_and_git_lock_must_match(self):
        h.git(
            self.repo, "remote", "set-url", "origin", "git@github.com:Example/RePo.git"
        )
        self.args.dry_run = True
        self.assertEqual(self.migrate()["pr_head"]["sha"], self.head)
        for change, reason in [
            ({"url": self.pr + "0"}, "PR identity"),
            ({"headRefName": "other"}, "PR identity"),
            ({"headRefOid": "a" * 41}, "valid live head"),
            ({"headRefOid": self.base}, "same SHA"),
        ]:
            with self.subTest(change=change), patch.dict(self.response, change):
                self.refused(reason)
        self.args.pr_branch = "invalid..ref"
        self.refused("git")
        self.args.pr_branch = "fix/feature"
        h.git(
            self.repo,
            "remote",
            "set-url",
            "origin",
            "https://github.com/foreign/repo.git",
        )
        self.refused("repository does not match")
        h.git(self.repo, "worktree", "lock", self.path)
        self.refused("unlocked linked")

    def test_change_between_remote_reads_does_not_commit(self):
        with patch.object(h, "pr_ref", side_effect=[self.head, self.base]):
            self.refused("head changed")
        with patch.object(
            h, "migration_local_head", side_effect=[self.head, self.base]
        ):
            self.refused("head changed")

    def test_remote_failure_and_inconsistent_git_metadata_preserve_binding(self):
        self.migrate()
        for response in ([], None, {"url": self.pr, "headRefName": None}):
            with (
                self.subTest(response=response),
                patch.object(self, "response", response),
            ):
                self.refused("PR identity")
        previous = h.linked(self.repo, self.path)
        with patch.object(h, "linked", return_value={**previous, "HEAD": self.base}):
            self.refused("worktree HEAD changed")
        with patch.object(h, "run", side_effect=h.Protected("command unavailable")):
            self.refused("command unavailable")

    def test_cli_roundtrip_and_rewritten_pr_history(self):
        from contextlib import redirect_stdout
        from io import StringIO

        ledger = self.root / "ownership.json"
        ledger.write_text(json.dumps(self.state))
        before = ledger.read_bytes()
        argv = [
            cli.__file__,
            "--state",
            str(ledger),
            "--host",
            "codex",
            "--session",
            "test",
            "migrate-pr-branch",
            "--pr",
            self.pr,
            "--pr-branch",
            "fix/feature",
            "--path",
            self.path,
        ]
        for dry in (True, False):
            out = StringIO()
            with (
                patch.object(sys, "argv", argv + (["--dry-run"] if dry else [])),
                redirect_stdout(out),
            ):
                cli.main()
            result = json.loads(out.getvalue())
            self.assertIs(result["dry_run"], dry)
            self.assertEqual(result["pr_head"]["sha"], self.head)
            self.assertEqual(ledger.read_bytes() == before, dry)
        self.state = json.loads(ledger.read_bytes())
        # The owner's new evidence can refer to an older commit. The verified
        # migration commit must still be reachable, including on a branch retry.
        h.git(self.path, "checkout", "-B", "feature", self.base)
        h.preserve(self.state, self.owner, self.path, str(self.evidence))
        self.response["headRefOid"] = self.base
        self.assertIn("protected", self.clean(dry_run=True))
        record = self.state[self.path]
        h.git(self.repo, "worktree", "remove", self.path)
        record["pending_branch"] = True
        self.assertIn("protected", self.clean(dry_run=True))
        self.assertEqual(h.git(self.repo, "rev-parse", record["branch"]), self.base)

    def test_new_pushed_head_can_migrate_without_refreshing_old_evidence(self):
        h.git(self.path, "commit", "--allow-empty", "-m", "second")
        new = h.git(self.path, "rev-parse", "HEAD")
        self.refused("same SHA")
        self.response["headRefOid"] = new
        self.assertEqual(self.migrate()["pr_head"]["sha"], new)
        self.assertEqual(self.state[self.path]["evidence_head"], self.head)
        self.assertIn("HEAD changed", self.clean(dry_run=True)["protected"])

    def test_relink_invalidates_binding_only_if_url_changes(self):
        self.migrate()
        self.args.command = "link-pr"
        self.migrate()
        self.assertEqual(self.state[self.path]["pr_head"]["url"], self.pr)
        with (
            patch.dict(self.state[self.path], {"pr": self.pr + "1"}),
            self.assertRaisesRegex(h.Protected, "different PR"),
        ):
            h.live_head(self.state[self.path])
        self.args.pr += "1"
        self.migrate()
        self.assertNotIn("pr_head", self.state[self.path])

    def test_invalid_persisted_binding_blocks_registry(self):
        self.migrate()
        validate(self.state)
        record = self.state[self.path]
        for invalid in [
            None,
            {},
            {"url": self.pr, "ref": "refs/heads/", "sha": self.head},
            {"url": self.pr + "0", "ref": "refs/heads/f", "sha": self.head},
            {"url": self.pr, "ref": 0, "sha": self.head},
            {"url": self.pr, "ref": "refs/heads/f", "sha": "bad"},
        ]:
            with (
                self.subTest(invalid=invalid),
                patch.dict(record, {"pr_head": invalid}),
                self.assertRaisesRegex(h.Protected, "invalid verified"),
            ):
                validate(self.state)
        with (
            patch.dict(record, {"kind": "temp"}),
            self.assertRaisesRegex(h.Protected, "invalid verified"),
        ):
            validate(self.state)
        for n in (40, 64):
            self.assertTrue(valid_oid("0" * n))
            self.assertFalse(valid_oid("z" * n))
        self.assertFalse(valid_oid(10**39))


def test_antigravity_requires_its_own_identity_and_has_no_native_hook(tmp_path):
    state = tmp_path / "state.json"
    entry = [
        sys.executable,
        "-B",
        cli.__file__,
        "--state",
        str(state),
        "--host",
        "antigravity",
    ]
    env = {
        **os.environ,
        "CODEX_THREAD_ID": "codex-session",
        "DISK_HYGIENE_CLEANUP": "on",
    }

    def invoke(*argv):
        result = subprocess.run(
            [*entry, *argv],
            capture_output=True,
            text=True,
            env=env,
            input="{}",
            check=False,
        )
        return result.returncode, json.loads(result.stdout)

    code, result = invoke("status")
    assert code == 1 and "explicit session" in result["protected"]
    for event in ("SessionStart", "PostToolUse", "Stop", "SessionEnd"):
        code, result = invoke("--session", "review", "hook", event)
        assert code == 1 and "explicit CLI" in result["protected"]
    assert not state.exists()
    code, result = invoke(
        "--session", "review", "create-temp", "--parent", str(tmp_path)
    )
    assert code == 0
    path = result["created"]
    code, result = invoke("--session", "review", "status")
    assert code == 0 and result[path]["owner"] == "antigravity:review"
    code, result = invoke("--session", "other", "status")
    assert (code, result) == (0, {})
    ended = {
        path: {"owner": "codex:test", "repo": str(tmp_path)},
        "_ended": {"codex:test": True},
    }
    assert (
        acting_owner(ended, "antigravity:review", path, str(tmp_path))
        == "antigravity:review"
    )


def test_cli_requires_an_explicit_host_even_with_a_session(monkeypatch):
    monkeypatch.setattr(sys, "argv", [cli.__file__, "--session", "review", "status"])
    with pytest.raises(SystemExit) as error:
        cli.parse_args()
    assert error.value.code == 2


def test_native_owner_and_unregistered_path_do_not_require_a_git_cwd(monkeypatch):
    def unexpected_scope(cwd):
        pytest.fail(f"unnecessary Git scope lookup at {cwd}")

    monkeypatch.setattr(cleanup_hooks, "scope", unexpected_scope)
    owner = "codex:review"
    state = {"/owned": {"owner": owner}, "_ended": {owner: True}}
    assert acting_owner(state, owner, "/owned", "/outside-repo") == owner
    assert acting_owner(state, owner, "/unknown", "/outside-repo") == owner
