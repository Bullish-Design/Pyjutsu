"""C5: duplicate (lane `003/c5`).

Oracle is the pinned `jj` CLI's `jj duplicate`.
"""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import ImmutableCommitError

from tests.diff.jj_cli import JjCli


def test_duplicate_single_commit(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    original = ws.resolve("@-")  # commit C
    with ws.transaction("duplicate C") as tx:
        dups = tx.duplicate("@-")
    assert len(dups) == 1
    dup = dups[0]
    # Same content and description, new change id and commit id.
    assert dup.description == original.description
    assert dup.change_id != original.change_id
    assert dup.commit_id != original.commit_id
    assert dup.tree_id == original.tree_id

    # The CLI agrees: the duplicated change resolves, and its commit id matches.
    assert jj.commit_id(linear_repo, dup.change_id) == dup.commit_id


def test_duplicate_onto(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    # linear_repo: A -> B -> C -> @(empty). Duplicate B onto C: the duplicate's parent is C.
    c_id = ws.resolve("@-").commit_id
    with ws.transaction("duplicate B onto C") as tx:
        dups = tx.duplicate("@--", onto="@-")
    assert len(dups) == 1
    dup = dups[0]
    assert dup.parent_ids == [c_id]

    # CLI oracle: `jj duplicate -r @-- --onto @-` produces a commit whose parent is C.
    # The duplicate is a descendant of C (`@-::`); the original B is not.
    assert dup.change_id in jj.change_ids(linear_repo, "@-::")
    assert jj.parent_commit_ids(linear_repo, dup.change_id) == [c_id]


def test_duplicate_chain_onto(linear_repo: Path, jj: JjCli) -> None:
    """Duplicating A and B together onto C keeps their internal structure (B on duplicated A)."""
    ws = pyjutsu.Workspace.load(linear_repo)
    c_id = ws.resolve("@-").commit_id
    with ws.transaction("duplicate A|B onto C") as tx:
        dups = tx.duplicate("@---|@--", onto="@-")
    assert len(dups) == 2
    # Children-first order: the B duplicate (child) first, then the A duplicate (root).
    dup_b, dup_a = dups
    assert dup_a.parent_ids == [c_id]  # the A duplicate sits on C
    assert dup_b.parent_ids == [dup_a.commit_id]  # the B duplicate sits on duplicated A


def test_duplicate_preserves_originals(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    original_id = ws.resolve("@-").commit_id
    with ws.transaction("duplicate") as tx:
        tx.duplicate("@-")
    # The original commit is untouched; only a new change appeared.
    assert ws.head().resolve(original_id).commit_id == original_id


def test_duplicate_empty_selection_raises(linear_repo: Path) -> None:
    from pyjutsu import RevsetError

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("dup") as tx:
        with pytest.raises(RevsetError):
            tx.duplicate("none()")


def test_duplicate_immutable_raises(scratch_repo: Path) -> None:
    """Duplicating the root commit is refused."""
    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("dup root") as tx:
        with pytest.raises(ImmutableCommitError):
            tx.duplicate("root()")
