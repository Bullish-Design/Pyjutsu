"""Slice 2: `resolve` — single-revision revset → Commit, differential vs the pinned jj."""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import RevsetError

from tests.diff.jj_cli import JjCli


@pytest.mark.parametrize("revset", ["@", "@-", "root()"])
def test_resolve_ids_match_pinned_cli(linear_repo: Path, jj: JjCli, revset: str) -> None:
    commit = pyjutsu.Workspace.load(linear_repo).resolve(revset)
    assert commit.change_id == jj.change_id(linear_repo, revset)
    assert commit.commit_id == jj.commit_id(linear_repo, revset)


def test_resolve_by_change_id_prefix(linear_repo: Path, jj: JjCli) -> None:
    # A change-id prefix is a valid single-revision symbol; resolve must round-trip it.
    full = jj.change_id(linear_repo, "@-")
    commit = pyjutsu.Workspace.load(linear_repo).resolve(full[:8])
    assert commit.change_id == full


def test_resolve_zero_matches_raises(linear_repo: Path) -> None:
    with pytest.raises(RevsetError):
        pyjutsu.Workspace.load(linear_repo).resolve("none()")


def test_resolve_many_matches_raises(linear_repo: Path) -> None:
    with pytest.raises(RevsetError):
        pyjutsu.Workspace.load(linear_repo).resolve("all()")


def test_resolve_parse_error_raises(linear_repo: Path) -> None:
    with pytest.raises(RevsetError):
        pyjutsu.Workspace.load(linear_repo).resolve("this is not a revset (")
