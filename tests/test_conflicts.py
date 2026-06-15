"""Slice 6: `conflicts`, differential vs `jj resolve --list`."""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import Conflict, RevsetError

from tests.diff.jj_cli import JjCli


def test_no_conflicts_in_clean_repo(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    assert ws.conflicts("@") == []
    assert ws.resolve("@").has_conflict is False


def test_conflicts_match_cli(conflict_repo: Path, jj: JjCli) -> None:
    conflicts = pyjutsu.Workspace.load(conflict_repo).conflicts("@")
    assert {c.path: c.num_sides for c in conflicts} == jj.conflicted_paths(conflict_repo)


def test_conflict_shape(conflict_repo: Path) -> None:
    conflicts = pyjutsu.Workspace.load(conflict_repo).conflicts("@")
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert isinstance(conflict, Conflict)
    assert conflict.path == "file.txt"
    # A regular merge of two diverging edits → a 2-sided, 1-base conflict.
    assert conflict.num_sides == 2
    assert conflict.num_bases == 1


def test_conflicted_commit_flags_has_conflict(conflict_repo: Path) -> None:
    assert pyjutsu.Workspace.load(conflict_repo).resolve("@").has_conflict is True


def test_conflicts_requires_single_revision(conflict_repo: Path) -> None:
    with pytest.raises(RevsetError):
        pyjutsu.Workspace.load(conflict_repo).conflicts("all()")
