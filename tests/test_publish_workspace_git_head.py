"""Per-workspace Git HEAD: ``publish_if`` and ``recover`` keep each workspace's Git HEAD correct.

jj 0.45 and later stores one Git HEAD per workspace (the op-store view field ``git_heads``). A
colocated child workspace is a Git worktree made by ``jj workspace add --colocate``. It has its own
``HEAD``. Each test publishes or recovers in one workspace. Each then runs a plain jj CLI command in
that workspace. The command must not print "Reset the working copy parent to the new Git HEAD" and
must not move ``@``. Another workspace must keep its Git HEAD.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pyjutsu
import pytest

from tests.diff.jj_cli import JjCli
from tests.publish_support import (
    HAS_TEST_HOOKS,
    Barrier,
    PublishRepo,
    build_publish_repo,
    cli_command,
    git_index_paths,
    git_out,
    kill_hard,
    observe_commit,
    op_ids,
    parse_output,
    run_publish,
    start_publish,
    tracked_files,
)

RESET_MESSAGE = "Reset the working copy parent to the new Git HEAD"


def _child_of(repo: PublishRepo, jj: JjCli, tmp_path: Path) -> PublishRepo:
    """Add a colocated child workspace on the base commit, as ``jj workspace add --colocate``."""
    child = tmp_path / "child"
    jj(repo.root, "workspace", "add", "--colocate", "--name", "child", "-r", repo.base, str(child))
    assert (child / ".git").is_file(), "a colocated child workspace is a Git worktree"
    return PublishRepo(
        root=child,
        expected=observe_commit(jj, child, "@"),
        onto=repo.onto,
        base=repo.base,
        colocated=True,
    )


def _head(root: Path) -> str:
    return git_out(root, "rev-parse", "HEAD").strip()


def _assert_plain_jj_command_is_quiet(jj: JjCli, root: Path, *, at: str, parent: str) -> None:
    """A plain jj 0.46 command imports no Git HEAD: no message, same ``@``, same ``@-``."""
    ops_before = op_ids(jj, root)
    status = subprocess.run(["jj", "status"], cwd=root, capture_output=True, text=True, env=jj._env())
    assert status.returncode == 0, status.stderr
    output = status.stdout + status.stderr
    assert RESET_MESSAGE not in output, output
    assert "stale" not in output.lower(), output
    assert observe_commit(jj, root, "@") == at
    assert observe_commit(jj, root, "@-") == parent
    assert op_ids(jj, root) == ops_before, "the command published an operation"


def _assert_published_in(jj: JjCli, repo: PublishRepo, *, new_wc: str) -> None:
    assert _head(repo.root) == repo.onto
    assert git_index_paths(repo.root) == tracked_files(jj, repo.root, repo.onto)
    assert git_out(repo.root, "status", "--porcelain").strip() == ""
    assert (repo.root / "p.txt").read_text() == "prepared\n"
    _assert_plain_jj_command_is_quiet(jj, repo.root, at=new_wc, parent=repo.onto)


def test_publish_in_a_colocated_child_workspace_updates_that_worktrees_git_head(
    tmp_path: Path, jj: JjCli
) -> None:
    main = build_publish_repo(tmp_path, jj)
    child = _child_of(main, jj, tmp_path)
    assert _head(child.root) == main.base
    main_at = observe_commit(jj, main.root, "@")

    result = pyjutsu.Workspace.load(child.root).publish_if(child.expected, child.onto, "publish in child")

    assert result.git_sync == "synced"
    assert result.git_sync_operation is not None
    new_wc = observe_commit(jj, child.root, "@")
    assert new_wc == result.wc_commit
    _assert_published_in(jj, child, new_wc=new_wc)
    # The default workspace keeps its own Git HEAD, its `@`, and its files.
    assert _head(main.root) == main.base
    assert (main.root / "a.txt").read_text() == "base\n"
    assert not (main.root / "p.txt").exists()
    _assert_plain_jj_command_is_quiet(jj, main.root, at=main_at, parent=main.base)


def test_publish_cli_in_a_colocated_child_workspace_updates_that_worktrees_git_head(
    tmp_path: Path, jj: JjCli
) -> None:
    main = build_publish_repo(tmp_path, jj)
    child = _child_of(main, jj, tmp_path)

    proc = run_publish(child)

    assert proc.returncode == 0, proc.stderr
    facts = parse_output(proc.stdout)
    assert facts["result"] == "published"
    assert facts["git_sync"] == "synced"
    _assert_published_in(jj, child, new_wc=observe_commit(jj, child.root, "@"))


def test_publish_in_the_default_workspace_leaves_a_colocated_child_alone(tmp_path: Path, jj: JjCli) -> None:
    main = build_publish_repo(tmp_path, jj)
    child = _child_of(main, jj, tmp_path)
    child_at = observe_commit(jj, child.root, "@")

    result = pyjutsu.Workspace.load(main.root).publish_if(main.expected, main.onto, "publish in default")

    assert result.git_sync == "synced"
    new_wc = observe_commit(jj, main.root, "@")
    _assert_published_in(jj, main, new_wc=new_wc)
    assert _head(child.root) == main.base
    _assert_plain_jj_command_is_quiet(jj, child.root, at=child_at, parent=main.base)


def test_publish_in_the_default_workspace_keeps_jj_quiet(tmp_path: Path, jj: JjCli) -> None:
    main = build_publish_repo(tmp_path, jj)

    result = pyjutsu.Workspace.load(main.root).publish_if(main.expected, main.onto, "publish in default")

    assert result.git_sync == "synced"
    _assert_published_in(jj, main, new_wc=observe_commit(jj, main.root, "@"))


@pytest.mark.skipif(not HAS_TEST_HOOKS, reason="needs a build with the test-hooks feature")
@pytest.mark.parametrize("stage", ["AFTER_PUBLISH", "AFTER_CHECKOUT", "AFTER_GIT_SYNC"])
def test_recover_in_a_colocated_child_workspace_repairs_that_worktrees_git_head(
    tmp_path: Path, jj: JjCli, stage: str
) -> None:
    main = build_publish_repo(tmp_path, jj)
    child = _child_of(main, jj, tmp_path)
    barrier = Barrier(tmp_path, stage)
    proc = start_publish(child, barrier)
    barrier.wait_reached()
    kill_hard(proc)

    recovered = subprocess.run(cli_command("recover", "--repo", str(child.root)), capture_output=True, text=True)

    assert recovered.returncode == 0, recovered.stderr
    assert parse_output(recovered.stdout)["result"] == "recovered"
    new_wc = observe_commit(jj, child.root, "@")
    assert new_wc != child.expected
    _assert_published_in(jj, child, new_wc=new_wc)
    assert _head(main.root) == main.base


def test_recover_in_a_colocated_child_workspace_after_a_failed_git_sync(tmp_path: Path, jj: JjCli) -> None:
    """Git cannot lock the worktree's HEAD, so the publication ends incomplete. ``recover`` heals it."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    main = build_publish_repo(tmp_path, jj)
    child = _child_of(main, jj, tmp_path)
    worktree_git_dir = Path(git_out(child.root, "rev-parse", "--absolute-git-dir").strip())
    assert worktree_git_dir != main.root / ".git"

    worktree_git_dir.chmod(0o555)
    try:
        proc = run_publish(child)
    finally:
        worktree_git_dir.chmod(0o755)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    facts = parse_output(proc.stdout)
    assert facts["result"] == "incomplete" and facts["stage"] == "git-sync"
    assert _head(child.root) == child.base  # Git lags; the jj working copy is current

    recovered = subprocess.run(cli_command("recover", "--repo", str(child.root)), capture_output=True, text=True)

    assert recovered.returncode == 0, recovered.stderr
    _assert_published_in(jj, child, new_wc=observe_commit(jj, child.root, "@"))
    assert _head(main.root) == main.base


def test_sync_colocated_in_a_child_workspace_resets_only_that_worktree(tmp_path: Path, jj: JjCli) -> None:
    main = build_publish_repo(tmp_path, jj)
    child = _child_of(main, jj, tmp_path)
    ws = pyjutsu.Workspace.load(child.root)
    # A binding transaction moves `@` and leaves the worktree's HEAD behind.
    with ws.transaction("advance") as tx:
        tx.new([child.onto])
    at = observe_commit(jj, child.root, "@")
    assert _head(child.root) == child.base

    op = ws.sync_colocated()

    assert op is not None
    assert _head(child.root) == child.onto
    assert _head(main.root) == main.base
    _assert_plain_jj_command_is_quiet(jj, child.root, at=at, parent=child.onto)
