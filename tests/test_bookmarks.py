"""Slice 5: `bookmarks`, differential vs `jj bookmark list --all-remotes`."""

from __future__ import annotations

from pathlib import Path

import pyjutsu

from tests.diff.jj_cli import JjCli


def _as_rows(bookmarks: list[pyjutsu.Bookmark]) -> set[tuple[str, str, str, bool]]:
    # Flatten to the same (name, remote, single-target, tracked) shape the CLI helper yields.
    # Every fixture bookmark is non-conflicted, so target_ids has exactly one entry.
    return {
        (b.name, b.remote or "", b.target_ids[0], b.tracked)
        for b in bookmarks
    }


def test_no_bookmarks_in_plain_repo(linear_repo: Path) -> None:
    assert pyjutsu.Workspace.load(linear_repo).bookmarks() == []


def test_bookmarks_match_cli(bookmarked_repo: Path, jj: JjCli) -> None:
    bookmarks = pyjutsu.Workspace.load(bookmarked_repo).bookmarks()
    assert _as_rows(bookmarks) == jj.bookmarks(bookmarked_repo)


def test_local_and_remote_rows_present(bookmarked_repo: Path) -> None:
    bookmarks = pyjutsu.Workspace.load(bookmarked_repo).bookmarks()
    locals_ = [b for b in bookmarks if b.remote is None]
    remotes = {b.remote for b in bookmarks if b.remote is not None}

    assert [b.name for b in locals_] == ["feature"]
    assert locals_[0].tracked is False  # tracking is a remote-ref property
    assert "origin" in remotes and "git" in remotes


def test_remote_tracking_state(bookmarked_repo: Path) -> None:
    bookmarks = pyjutsu.Workspace.load(bookmarked_repo).bookmarks()
    origin = next(b for b in bookmarks if b.remote == "origin")
    assert origin.name == "feature"
    assert origin.tracked is True
    assert origin.conflicted is False
    assert len(origin.target_ids) == 1
