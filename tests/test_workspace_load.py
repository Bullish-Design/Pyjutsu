"""M0's vertical slice: load a workspace and read `@`, differentially vs the pinned `jj`."""

from __future__ import annotations

from pathlib import Path

import pyjutsu
from pyjutsu import Commit

from tests.conftest import WC_DESCRIPTION
from tests.diff.jj_cli import JjCli


def test_load_reports_default_workspace(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    assert ws.name == "default"
    assert ws.root == scratch_repo.resolve()


def test_working_copy_is_a_commit(scratch_repo: Path) -> None:
    commit = pyjutsu.Workspace.load(scratch_repo).working_copy()
    assert isinstance(commit, Commit)
    assert commit.description.rstrip("\n") == WC_DESCRIPTION


def test_working_copy_ids_match_pinned_cli(scratch_repo: Path, jj: JjCli) -> None:
    # The differential assertion: the binding's `@` ids equal what the pinned `jj` reports.
    commit = pyjutsu.Workspace.load(scratch_repo).working_copy()
    assert commit.change_id == jj.template(scratch_repo, "@", "change_id")
    assert commit.commit_id == jj.template(scratch_repo, "@", "commit_id")
