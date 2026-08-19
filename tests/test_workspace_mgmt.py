"""Workspace management, including parent-aware secondary workspace creation."""

from __future__ import annotations

import shutil
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import PyjutsuError, Revset, RevsetError, WorkspaceError

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

    # Registration and initial working-copy creation are separate operations, matching the CLI.
    assert len(jj.op_log_ids(scratch_repo)) == ops_before + 2
    assert jj.op_log_descriptions(scratch_repo, 2) == [
        "create initial working-copy commit in workspace second",
        "add workspace 'second'",
    ]


def test_add_workspace_default_uses_source_parents(
    linear_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    other = _copy_repo(linear_repo, tmp_path / "default-copy")
    expected_parents = jj.parent_commit_ids(linear_repo, "@")

    ws = pyjutsu.Workspace.load(linear_repo)
    info = ws.add_workspace(tmp_path / "default-second", name="second")
    jj(other, "workspace", "add", "--name", "second", str(tmp_path / "cli-default-second"))

    assert ws.head().resolve(info.wc_commit_id).parent_ids == expected_parents
    assert jj.parent_commit_ids(other, "second@") == expected_parents


def test_add_workspace_accepts_explicit_parent(
    linear_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    expected_parent = jj.commit_id(linear_repo, "@--")

    ws = pyjutsu.Workspace.load(linear_repo)
    info = ws.add_workspace(
        tmp_path / "explicit-second",
        name="second",
        revisions="@--",
    )

    assert ws.head().resolve(info.wc_commit_id).parent_ids == [expected_parent]
    assert (tmp_path / "explicit-second" / "a.txt").is_file()
    assert (tmp_path / "explicit-second" / "b.txt").is_file()
    assert not (tmp_path / "explicit-second" / "c.txt").exists()


def test_add_workspace_accepts_change_id_and_typed_revset(
    linear_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    expected_parent = jj.commit_id(linear_repo, "@--")
    change_id = jj.change_id(linear_repo, "@--")
    ws = pyjutsu.Workspace.load(linear_repo)

    by_change = ws.add_workspace(
        tmp_path / "by-change",
        name="by-change",
        revisions=change_id,
    )
    by_revset = ws.add_workspace(
        tmp_path / "by-revset",
        name="by-revset",
        revisions=Revset("@--"),
    )

    assert ws.head().resolve(by_change.wc_commit_id).parent_ids == [expected_parent]
    assert ws.head().resolve(by_revset.wc_commit_id).parent_ids == [expected_parent]


def test_add_workspace_multiple_parents_preserves_conflicts(
    conflict_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    other = _copy_repo(conflict_repo, tmp_path / "conflict-copy")
    expected_parents = sorted(
        [jj.commit_id(conflict_repo, "sideA"), jj.commit_id(conflict_repo, "sideB")]
    )
    ws = pyjutsu.Workspace.load(conflict_repo)

    info = ws.add_workspace(
        tmp_path / "merge-second",
        name="merge-second",
        revisions=["sideA", "sideB"],
    )
    jj.add_workspace(
        other,
        tmp_path / "cli-merge-second",
        name="merge-second",
        revisions=["sideA", "sideB"],
    )

    created = ws.head().resolve(info.wc_commit_id)
    assert sorted(created.parent_ids) == expected_parents
    assert sorted(jj.parent_commit_ids(other, "merge-second@")) == expected_parents
    assert created.has_conflict
    assert pyjutsu.Workspace.load(info.path).conflicts("@")
    assert jj.conflicted_paths(tmp_path / "cli-merge-second") == {"file.txt": 2}
    assert (tmp_path / "merge-second" / "file.txt").is_file()


@pytest.mark.parametrize(
    "revision",
    ["this is not a revset (", "none()", "all()"],
    ids=["invalid", "empty", "multiple"],
)
def test_add_workspace_rejects_non_single_revision_before_creation(
    linear_repo: Path,
    tmp_path: Path,
    revision: str,
) -> None:
    destination = tmp_path / f"bad-{revision[:3]}"
    ws = pyjutsu.Workspace.load(linear_repo)

    with pytest.raises(RevsetError):
        ws.add_workspace(destination, name=f"bad-{revision[:3]}", revisions=revision)

    assert not destination.exists()
    assert all(not row.name.startswith("bad-") for row in ws.workspaces())


def test_add_workspace_rejects_duplicate_name_and_nonempty_destination(
    scratch_repo: Path, tmp_path: Path
) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_workspace(tmp_path / "first", name="taken", revisions="root()")

    duplicate_destination = tmp_path / "duplicate"
    with pytest.raises(PyjutsuError, match="already exists"):
        ws.add_workspace(duplicate_destination, name="taken", revisions="root()")
    assert not duplicate_destination.exists()

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    marker = nonempty / "keep.txt"
    marker.write_text("keep\n")
    with pytest.raises(PyjutsuError, match="not an empty directory"):
        ws.add_workspace(nonempty, name="new-name", revisions="root()")
    assert marker.read_text() == "keep\n"
    assert "new-name" not in {row.name for row in ws.workspaces()}


def test_add_workspace_sparse_pattern_modes(
    linear_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    jj(linear_repo, "sparse", "set", "--clear", "--add", "a.txt")
    ws = pyjutsu.Workspace.load(linear_repo)

    copied = ws.add_workspace(tmp_path / "sparse-copy", name="sparse-copy")
    full = ws.add_workspace(
        tmp_path / "sparse-full",
        name="sparse-full",
        sparse_patterns="full",
    )
    empty = ws.add_workspace(
        tmp_path / "sparse-empty",
        name="sparse-empty",
        sparse_patterns="empty",
    )

    assert copied.path is not None and full.path is not None and empty.path is not None
    copied_path = Path(copied.path)
    full_path = Path(full.path)
    empty_path = Path(empty.path)
    assert (copied_path / "a.txt").is_file()
    assert not (copied_path / "b.txt").exists()
    assert all((full_path / name).is_file() for name in ("a.txt", "b.txt", "c.txt"))
    assert {path.name for path in empty_path.iterdir()} == {".jj"}


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
