"""Slice 9: workspace management — `init` / `add_workspace` / `forget_workspace` / `workspaces`.

These are `Workspace`-level verbs (not `Transaction` methods). A fresh workspace `@` gets a random
change id (like `tx.new`), so — as in `test_new` — these assert *structure* (names present/absent in
the workspace set, `@` empty on `root()`, one op each), not commit-id parity. `add_workspace` puts
the new `@` on `root()`; the CLI's `jj workspace add` default bases it on the current `@`'s parents,
so the differential drives the CLI with `-r 'root()'` to match the primitive's placement.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import PyjutsuError, WorkspaceError

from tests.diff.jj_cli import JjCli


def _copy_repo(src: Path, dst: Path) -> Path:
    """A byte-identical sibling repo (same change ids, commit ids, and op log)."""
    shutil.copytree(src, dst)
    return dst


def test_init_creates_loadable_repo(tmp_path: Path, jj: JjCli) -> None:
    target = tmp_path / "r"
    target.mkdir()
    ws = pyjutsu.Workspace.init(target)

    # `.jj` exists, the default workspace is named "default", and `@` is the empty root child.
    assert (target / ".jj").is_dir()
    assert ws.name == "default"
    assert ws.working_copy().is_empty
    assert {w.name for w in ws.workspaces()} == {"default"}

    # The pinned CLI can read the repo the binding created.
    assert jj.op_log_ids(target)  # non-empty op log
    assert jj.workspaces(target) == {"default"}

    # Same shape as `jj git init` in a sibling dir: one workspace, an empty `@`.
    other = tmp_path / "cli"
    other.mkdir()
    jj(other, "git", "init")
    assert jj.workspaces(other) == {"default"}
    assert jj.is_empty(other, "@")


def test_init_colocated_creates_git_dir(tmp_path: Path, jj: JjCli) -> None:
    target = tmp_path / "colo"
    target.mkdir()
    ws = pyjutsu.Workspace.init(target, colocate=True)

    # Colocated ⇒ both `.jj` and a real `.git` are present (matches `jj git init --colocate`).
    assert (target / ".jj").is_dir()
    assert (target / ".git").exists()
    assert ws.name == "default"
    assert jj.workspaces(target) == {"default"}


def test_init_existing_repo_raises(scratch_repo: Path) -> None:
    # `init` into a dir that already holds a repo is a workspace-init failure.
    with pytest.raises(WorkspaceError):
        pyjutsu.Workspace.init(scratch_repo)


def test_add_workspace_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    ops_before = len(jj.op_log_ids(scratch_repo))
    root_commit = jj.commit_id(scratch_repo, "root()")

    ws = pyjutsu.Workspace.load(scratch_repo)
    info = ws.add_workspace(tmp_path / "second", name="second")
    jj(other, "workspace", "add", "--name", "second", "-r", "root()", str(tmp_path / "cli_second"))

    # The returned row: name "second", a real path, an empty `@` whose parent is the root commit.
    assert info.name == "second"
    assert info.path is not None
    second_at = ws.head().resolve(info.wc_commit_id)
    assert second_at.is_empty
    assert second_at.parent_ids == [root_commit]

    # "second" is tracked on both sides; the new `.jj` exists at the new path.
    assert "second" in {w.name for w in ws.workspaces()}
    assert "second" in jj.workspaces(other)
    assert (tmp_path / "second" / ".jj").exists()

    # The binding delegates to jj-lib's faithful primitive, which publishes exactly one
    # `add workspace '<name>'` op. (The CLI's `jj workspace add` is a richer wrapper that emits a
    # second `create initial working-copy commit in workspace …` op for its `-r` placement logic —
    # the out-of-scope refinement §1(b) — so op-count parity is not expected on the CLI side.)
    assert len(jj.op_log_ids(scratch_repo)) == ops_before + 1


def test_add_workspace_default_name_is_basename(scratch_repo: Path, tmp_path: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    info = ws.add_workspace(tmp_path / "wsx")
    assert info.name == "wsx"
    assert "wsx" in {w.name for w in ws.workspaces()}


def test_forget_workspace_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    default_at = jj.commit_id(scratch_repo, "@")

    # Add a second workspace on both sides first.
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_workspace(tmp_path / "second", name="second")
    jj(other, "workspace", "add", "--name", "second", "-r", "root()", str(tmp_path / "cli_second"))

    ops_before = len(jj.op_log_ids(scratch_repo))
    cli_ops_before = len(jj.op_log_ids(other))

    ws.forget_workspace("second")
    jj(other, "workspace", "forget", "second")

    # "second" is gone from the workspace set on both sides; the default `@` is unchanged.
    assert "second" not in {w.name for w in ws.workspaces()}
    assert "second" not in jj.workspaces(other)
    assert jj.commit_id(scratch_repo, "@") == default_at
    assert jj.commit_id(other, "@") == default_at

    # One new op each (the `forget workspace` op).
    assert len(jj.op_log_ids(scratch_repo)) == ops_before + 1
    assert len(jj.op_log_ids(other)) == cli_ops_before + 1


def test_forget_unknown_workspace_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(PyjutsuError):
        ws.forget_workspace("nope")


def test_workspaces_lists_all(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_workspace(tmp_path / "second", name="second")
    ws.add_workspace(tmp_path / "third", name="third")

    rows = {w.name: w for w in ws.workspaces()}
    assert set(rows) == {"default", "second", "third"}
    # The binding's view of names matches the CLI's.
    assert jj.workspaces(scratch_repo) == {"default", "second", "third"}

    # Each row carries a real path and a valid hex commit id resolvable in the repo.
    for name, row in rows.items():
        assert row.path is not None
        assert ws.head().resolve(row.wc_commit_id).commit_id == row.wc_commit_id
    # The default workspace's `@` matches what the CLI reports for `@`.
    assert rows["default"].wc_commit_id == jj.commit_id(scratch_repo, "@")
