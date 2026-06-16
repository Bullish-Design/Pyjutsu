"""Slice 4: bookmark writes — differential vs `jj bookmark create/set/delete/track/untrack`.

Bookmark writes rewrite no commit and never move `@`, so their targets are *existing, unrewritten*
commits whose ids are deterministic across two byte-identical repos (made by copying a directory).
That lets us compare the full bookmark row set — ``{(name, remote, target_commit_id, tracked)}`` —
between a repo mutated through Pyjutsu and a sibling mutated through the pinned CLI. These tests also
lock in the slice's defining property: a bookmark write never checks out (`@` and the on-disk files
stay put).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import PyjutsuError

from tests.diff.jj_cli import JjCli


def _copy_repo(src: Path, dst: Path) -> Path:
    """A byte-identical sibling repo (same change ids, commit ids, and op log)."""
    shutil.copytree(src, dst)
    return dst


def _rows(jj: JjCli, repo: Path) -> set[tuple[str, str, str, bool]]:
    """Bookmark rows excluding the colocated-git mirror (``@git``).

    In a colocated repo the CLI re-exports every bookmark write to the backing git repo, so a
    create/delete also adds/removes a tracked ``name@git`` row. The thin binding performs no git
    interop this slice (it is a separate, later concern), so we compare the jj-native rows and drop
    the ``@git`` mirror the CLI maintains on the side.
    """
    return {row for row in jj.bookmarks(repo) if row[1] != "git"}


# --- create --------------------------------------------------------------------------------------


def test_create_bookmark_matches_cli(linear_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(linear_repo, tmp_path / "copy")
    a_change = jj.change_ids(linear_repo, "::@ ~ root()")[-1]  # oldest non-root == A
    a_commit = jj.commit_id(linear_repo, a_change)
    ops_before = len(jj.op_log_ids(linear_repo))

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("create feat") as tx:
        created = tx.create_bookmark("feat", a_change)
    jj(other, "bookmark", "create", "feat", "-r", a_change)

    # Returned model is the new local bookmark pointing at A.
    assert created.name == "feat"
    assert created.remote is None
    assert created.target_ids == [a_commit]
    assert created.tracked is False

    # Full row set parity with the CLI; one op each on the clean `@`.
    assert _rows(jj, linear_repo) == _rows(jj, other)
    assert len(jj.op_log_ids(linear_repo)) == ops_before + 1
    assert len(jj.op_log_ids(other)) == ops_before + 1


def test_create_existing_raises(bookmarked_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    with pytest.raises(PyjutsuError):
        with ws.transaction("dup") as tx:
            tx.create_bookmark("feature", "@")


# --- set -----------------------------------------------------------------------------------------


def test_set_bookmark_creates_and_moves(bookmarked_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(bookmarked_repo, tmp_path / "copy")

    ws = pyjutsu.Workspace.load(bookmarked_repo)
    # Create a new bookmark via `set`, then move the existing `feature` forward onto `@`.
    with ws.transaction("set new") as tx:
        new_b = tx.set_bookmark("newb", "@")
    with ws.transaction("move feature") as tx:
        moved = tx.set_bookmark("feature", "@")
    jj(other, "bookmark", "set", "newb", "-r", "@")
    jj(other, "bookmark", "set", "feature", "-r", "@")

    at_commit = jj.commit_id(bookmarked_repo, "@")
    assert new_b.name == "newb" and new_b.remote is None and new_b.target_ids == [at_commit]
    assert moved.name == "feature" and moved.target_ids == [at_commit]

    assert _rows(jj, bookmarked_repo) == _rows(jj, other)


# --- delete --------------------------------------------------------------------------------------


def test_delete_bookmark_matches_cli(bookmarked_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(bookmarked_repo, tmp_path / "copy")
    ops_before = len(jj.op_log_ids(bookmarked_repo))

    ws = pyjutsu.Workspace.load(bookmarked_repo)
    with ws.transaction("delete feature") as tx:
        result = tx.delete_bookmark("feature")
    jj(other, "bookmark", "delete", "feature")

    assert result is None  # effect-only mutation returns nothing
    # The local `feature` row is gone; the remaining rows match the CLI exactly.
    assert ("feature", "", jj.commit_id(other, "feature@origin"), False) not in jj.bookmarks(
        bookmarked_repo
    )
    assert _rows(jj, bookmarked_repo) == _rows(jj, other)
    assert len(jj.op_log_ids(bookmarked_repo)) == ops_before + 1
    assert len(jj.op_log_ids(other)) == ops_before + 1


def test_delete_missing_raises(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(PyjutsuError):
        with ws.transaction("delete ghost") as tx:
            tx.delete_bookmark("nonexistent")


# --- track / untrack -----------------------------------------------------------------------------


def test_untrack_matches_cli(bookmarked_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(bookmarked_repo, tmp_path / "copy")
    ops_before = len(jj.op_log_ids(bookmarked_repo))

    ws = pyjutsu.Workspace.load(bookmarked_repo)
    with ws.transaction("untrack feature@origin") as tx:
        row = tx.untrack_bookmark("feature", "origin")
    jj(other, "bookmark", "untrack", "feature@origin")

    assert row.name == "feature" and row.remote == "origin" and row.tracked is False
    assert _rows(jj, bookmarked_repo) == _rows(jj, other)
    assert len(jj.op_log_ids(bookmarked_repo)) == ops_before + 1
    assert len(jj.op_log_ids(other)) == ops_before + 1


def test_track_matches_cli(bookmarked_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Setup: untrack on the source, then copy, so both siblings start from the untracked state.
    jj(bookmarked_repo, "bookmark", "untrack", "feature@origin")
    other = _copy_repo(bookmarked_repo, tmp_path / "copy")

    ws = pyjutsu.Workspace.load(bookmarked_repo)
    with ws.transaction("track feature@origin") as tx:
        row = tx.track_bookmark("feature", "origin")
    jj(other, "bookmark", "track", "feature@origin")

    assert row.name == "feature" and row.remote == "origin" and row.tracked is True
    assert _rows(jj, bookmarked_repo) == _rows(jj, other)


def test_track_missing_raises(bookmarked_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    with pytest.raises(PyjutsuError):
        with ws.transaction("track ghost") as tx:
            tx.track_bookmark("nonexistent", "origin")


# --- invariants ----------------------------------------------------------------------------------


def test_rollback_publishes_nothing(linear_repo: Path, jj: JjCli) -> None:
    a_change = jj.change_ids(linear_repo, "::@ ~ root()")[-1]
    ops_before = jj.op_log_ids(linear_repo)
    rows_before = _rows(jj, linear_repo)

    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(RuntimeError):
        with ws.transaction("create then boom") as tx:
            tx.create_bookmark("feat", a_change)
            raise RuntimeError("boom")

    # No operation published and no bookmark created.
    assert jj.op_log_ids(linear_repo) == ops_before
    assert _rows(jj, linear_repo) == rows_before


def test_bookmark_write_does_not_checkout(linear_repo: Path, jj: JjCli) -> None:
    # A bookmark write never moves `@` or touches the on-disk working copy (no checkout this slice).
    a_change = jj.change_ids(linear_repo, "::@ ~ root()")[-1]
    files_before = sorted(p.name for p in linear_repo.iterdir())

    ws = pyjutsu.Workspace.load(linear_repo)
    wc_before = ws.working_copy().commit_id
    with ws.transaction("create feat") as tx:
        tx.create_bookmark("feat", a_change)

    assert ws.working_copy().commit_id == wc_before
    assert sorted(p.name for p in linear_repo.iterdir()) == files_before


def test_bookmark_outside_with_block_raises(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    tx = ws.transaction("never entered")
    with pytest.raises(RuntimeError):
        tx.create_bookmark("feat", "@")
