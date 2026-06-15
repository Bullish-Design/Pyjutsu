"""Slice 3: `log` + the enriched `Commit` shape, differential vs the pinned jj."""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest

from tests.diff.jj_cli import JjCli


def test_log_change_ids_and_order_match_cli(linear_repo: Path, jj: JjCli) -> None:
    commits = pyjutsu.Workspace.load(linear_repo).log("::@")
    assert [c.change_id for c in commits] == jj.change_ids(linear_repo, "::@")


def test_log_limit_keeps_newest_first(linear_repo: Path, jj: JjCli) -> None:
    commits = pyjutsu.Workspace.load(linear_repo).log("::@", limit=2)
    assert [c.change_id for c in commits] == jj.change_ids(linear_repo, "::@")[:2]


def test_log_empty_revset_is_empty_list(linear_repo: Path) -> None:
    assert pyjutsu.Workspace.load(linear_repo).log("none()") == []


def test_commit_signatures_match_cli(linear_repo: Path, jj: JjCli) -> None:
    commit = pyjutsu.Workspace.load(linear_repo).resolve("@-")  # a described, authored commit
    for which, sig in (("author", commit.author), ("committer", commit.committer)):
        expected = jj.signature(linear_repo, "@-", which)
        assert sig.name == expected["name"]
        assert sig.email == expected["email"]
        assert int(sig.timestamp.timestamp()) == expected["epoch"]
        offset = sig.timestamp.utcoffset()
        assert offset is not None
        assert offset.total_seconds() / 60 == expected["tz_minutes"]


def test_commit_parents_and_emptiness_match_cli(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    head = ws.resolve("@")
    assert head.parent_ids == jj.parent_commit_ids(linear_repo, "@")
    assert head.is_empty == jj.is_empty(linear_repo, "@")
    assert head.is_empty is True  # the trailing `@` in the linear fixture is empty


def test_root_commit_has_no_parents(linear_repo: Path) -> None:
    root = pyjutsu.Workspace.load(linear_repo).resolve("root()")
    assert root.parent_ids == []
    assert root.is_empty is True


@pytest.mark.parametrize("revset", ["@", "@-", "root()"])
def test_commit_has_no_bookmarks_in_plain_repo(linear_repo: Path, revset: str) -> None:
    commit = pyjutsu.Workspace.load(linear_repo).resolve(revset)
    assert commit.bookmarks == []
