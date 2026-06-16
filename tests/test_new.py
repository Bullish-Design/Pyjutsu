"""Slice 2: `tx.new` — differential vs `jj new`, plus the post-commit on-disk checkout.

`new`'s own commit id can't be compared across the two repos (a new commit gets a fresh, random
change id, which the git backend folds into the commit hash). So these tests assert the *structural*
result instead: the new commit's parents and emptiness, the op-log effect (one op on a clean `@`),
and — the point of this slice — that committing a transaction which moves `@` updates the on-disk
working copy to match (the checkout every later `@`-rewriting slice reuses).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import RevsetError

from tests.diff.jj_cli import JjCli


def _copy_repo(src: Path, dst: Path) -> Path:
    """A byte-identical sibling repo (same change ids, commit ids, and op log)."""
    shutil.copytree(src, dst)
    return dst


def test_new_on_top_of_at_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    base_at = jj.commit_id(scratch_repo, "@")  # deterministic; identical in both repos
    ops_before = len(jj.op_log_ids(scratch_repo))

    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("new") as tx:
        created = tx.new()
    jj(other, "new")

    # The new `@` is an empty commit whose only parent is the previous `@`.
    assert created.is_empty
    assert created.parent_ids == [base_at]
    assert jj.parent_commit_ids(other, "@") == [base_at]

    # Reading back through Pyjutsu: `@` is now the created commit.
    assert ws.working_copy().change_id == created.change_id
    assert ws.working_copy().commit_id == created.commit_id

    # Exactly one new operation on each side (clean `@`, so no preceding snapshot op).
    assert len(ws.operations()) == ops_before + 1
    assert len(jj.op_log_ids(other)) == ops_before + 1


def test_new_checks_out_parent_tree(linear_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # `new` on top of the oldest commit (`A`, holding only a.txt) must rewrite the working copy on
    # disk to A's tree: a.txt stays, the later b.txt/c.txt disappear. This is the slice's headline.
    other = _copy_repo(linear_repo, tmp_path / "copy")
    a_change = jj.change_ids(linear_repo, "::@ ~ root()")[-1]  # oldest non-root == A
    a_commit = jj.commit_id(linear_repo, a_change)
    ops_before = len(jj.op_log_ids(linear_repo))

    # Sanity: all three files are on disk before the checkout.
    for f in ("a.txt", "b.txt", "c.txt"):
        assert (linear_repo / f).exists()

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("new on A") as tx:
        created = tx.new(a_change)
    jj(other, "new", a_change)

    # New `@` is a child of A.
    assert created.parent_ids == [a_commit]
    assert jj.parent_commit_ids(other, "@") == [a_commit]

    # The on-disk working copy now matches A's tree — and matches what the CLI checked out.
    for repo in (linear_repo, other):
        assert (repo / "a.txt").exists()
        assert not (repo / "b.txt").exists()
        assert not (repo / "c.txt").exists()

    # One op on each side; no spurious snapshot from the (now in-lockstep) working copy.
    assert len(jj.op_log_ids(linear_repo)) == ops_before + 1
    assert len(jj.op_log_ids(other)) == ops_before + 1


def test_new_merge_matches_cli(conflict_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(conflict_repo, tmp_path / "copy")
    sides = sorted([jj.commit_id(conflict_repo, "sideA"), jj.commit_id(conflict_repo, "sideB")])
    ops_before = len(jj.op_log_ids(conflict_repo))

    ws = pyjutsu.Workspace.load(conflict_repo)
    with ws.transaction("new merge") as tx:
        created = tx.new(["sideA", "sideB"])
    jj(other, "new", "sideA", "sideB")

    # A multi-parent `new` is a merge: `@` has both sides as parents.
    assert sorted(created.parent_ids) == sides
    assert sorted(jj.parent_commit_ids(other, "@")) == sides
    assert len(jj.op_log_ids(conflict_repo)) == ops_before + 1
    assert len(jj.op_log_ids(other)) == ops_before + 1


def test_new_unknown_revision_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(RevsetError):
        with ws.transaction("bad") as tx:
            tx.new("nonexistent-change-id")


def test_new_outside_with_block_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    tx = ws.transaction("never entered")
    with pytest.raises(RuntimeError):
        tx.new()
