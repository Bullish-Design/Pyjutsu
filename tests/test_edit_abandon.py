"""Slice 3: `tx.edit` / `tx.abandon` — differential vs `jj edit` / `jj abandon`.

`edit` moves `@` onto an existing commit without rewriting it, so its commit id *is* deterministic
across two byte-identical repos (made by copying a repo directory) and can be compared directly.
`abandon` drops a commit and rebases its children onto the parent(s); abandoning `@` advances `@` to
a fresh empty commit whose change id is random, so for the leaf case we compare the *surviving*
change-id graph instead. Both verbs reuse slice 2's post-commit checkout: when `@` moves, the
on-disk working copy is rewritten to the new `@`'s tree (the file-presence checks below prove it).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import ImmutableCommitError, RevsetError

from tests.diff.jj_cli import JjCli


def _copy_repo(src: Path, dst: Path) -> Path:
    """A byte-identical sibling repo (same change ids, commit ids, and op log)."""
    shutil.copytree(src, dst)
    return dst


# --- edit ----------------------------------------------------------------------------------------


def test_edit_moves_at_and_checks_out(linear_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Edit the oldest commit (`A`, holding only a.txt): `@` moves onto A (no new commit) and the
    # on-disk working copy is rewritten to A's tree — a.txt stays, the later b.txt/c.txt disappear.
    other = _copy_repo(linear_repo, tmp_path / "copy")
    a_change = jj.change_ids(linear_repo, "::@ ~ root()")[-1]  # oldest non-root == A
    a_commit = jj.commit_id(linear_repo, a_change)
    ops_before = len(jj.op_log_ids(linear_repo))

    # Sanity: all three files are on disk before the checkout.
    for f in ("a.txt", "b.txt", "c.txt"):
        assert (linear_repo / f).exists()

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("edit A") as tx:
        edited = tx.edit(a_change)
    jj(other, "edit", a_change)

    # `edit` doesn't rewrite: the returned commit *is* A (change id + commit id unchanged).
    assert edited.change_id == a_change
    assert edited.commit_id == a_commit

    # Reading back through Pyjutsu: `@` is now A.
    assert ws.working_copy().change_id == a_change
    assert ws.working_copy().commit_id == a_commit

    # The on-disk working copy now matches A's tree — and matches what the CLI checked out.
    for repo in (linear_repo, other):
        assert (repo / "a.txt").exists()
        assert not (repo / "b.txt").exists()
        assert not (repo / "c.txt").exists()

    # One op each side (clean `@`, so no preceding snapshot op).
    assert len(jj.op_log_ids(linear_repo)) == ops_before + 1
    assert len(jj.op_log_ids(other)) == ops_before + 1


def test_edit_root_raises(scratch_repo: Path) -> None:
    # `MutableRepo::edit` returns a typed `RewriteRootCommit` error → `ImmutableCommitError`.
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(ImmutableCommitError):
        with ws.transaction("edit root") as tx:
            tx.edit("root()")


def test_edit_unknown_revision_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(RevsetError):
        with ws.transaction("bad") as tx:
            tx.edit("nonexistent-change-id")


def test_edit_outside_with_block_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    tx = ws.transaction("never entered")
    with pytest.raises(RuntimeError):
        tx.edit("@")


def test_edit_rollback_publishes_nothing(linear_repo: Path, jj: JjCli) -> None:
    # An exception in the body aborts the transaction: no operation is published.
    a_change = jj.change_ids(linear_repo, "::@ ~ root()")[-1]
    ops_before = jj.op_log_ids(linear_repo)

    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(RuntimeError):
        with ws.transaction("edit then boom") as tx:
            tx.edit(a_change)
            raise RuntimeError("boom")

    assert jj.op_log_ids(linear_repo) == ops_before


# --- abandon -------------------------------------------------------------------------------------


def test_abandon_leaf_at(linear_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Abandon `@` (the empty leaf): it's dropped and `@` advances to a fresh empty commit on top of
    # the old parent (`C`). The new `@`'s change id is random, so compare the *surviving* graph.
    other = _copy_repo(linear_repo, tmp_path / "copy")
    ops_before = len(jj.op_log_ids(linear_repo))

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("abandon @") as tx:
        result = tx.abandon("@")
    jj(other, "abandon", "@")

    assert result is None  # effect-only mutation returns nothing
    # The surviving change-id graph (everything but the new `@`) matches the CLI's exactly.
    assert jj.change_ids(linear_repo, "::@ ~ @") == jj.change_ids(other, "::@ ~ @")
    # The new `@` is an empty child of C on both sides.
    assert ws.working_copy().is_empty
    assert jj.is_empty(other, "@")

    assert len(jj.op_log_ids(linear_repo)) == ops_before + 1
    assert len(jj.op_log_ids(other)) == ops_before + 1


def test_abandon_middle_rebases_children(linear_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Abandon the middle commit `B`: its child `C` is rebased onto B's parent `A`, keeping its
    # change id. Survivors' commit ids are deterministic (preserved change ids + pinned timestamp).
    other = _copy_repo(linear_repo, tmp_path / "copy")
    chain = jj.change_ids(linear_repo, "::@ ~ root()")  # newest-first: [@, C, B, A]
    a_change, b_change, c_change = chain[-1], chain[-2], chain[-3]
    a_commit = jj.commit_id(linear_repo, a_change)

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("abandon B") as tx:
        tx.abandon(b_change)
    jj(other, "abandon", b_change)

    # B is gone; the change-id graph minus B matches the CLI's.
    py_graph = jj.change_ids(linear_repo, "::@")
    assert py_graph == jj.change_ids(other, "::@")
    assert b_change not in py_graph

    # C kept its change id but was rebased: its parent is now A.
    assert jj.parent_commit_ids(linear_repo, c_change) == [a_commit]
    # Commit-id parity on the rebased survivor (deterministic).
    assert jj.commit_id(linear_repo, c_change) == jj.commit_id(other, c_change)


def test_abandon_root_raises(scratch_repo: Path) -> None:
    # `record_abandoned_commit` `assert_ne!`s on root → would panic; the explicit guard raises
    # `ImmutableCommitError` instead (so this proves *no panic* surfaces).
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(ImmutableCommitError):
        with ws.transaction("abandon root") as tx:
            tx.abandon("root()")


def test_abandon_unknown_revision_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(RevsetError):
        with ws.transaction("bad") as tx:
            tx.abandon("nonexistent-change-id")


def test_abandon_outside_with_block_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    tx = ws.transaction("never entered")
    with pytest.raises(RuntimeError):
        tx.abandon("@")


def test_abandon_rollback_publishes_nothing(linear_repo: Path, jj: JjCli) -> None:
    chain = jj.change_ids(linear_repo, "::@ ~ root()")
    b_change = chain[-2]
    ops_before = jj.op_log_ids(linear_repo)

    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(RuntimeError):
        with ws.transaction("abandon then boom") as tx:
            tx.abandon(b_change)
            raise RuntimeError("boom")

    assert jj.op_log_ids(linear_repo) == ops_before
