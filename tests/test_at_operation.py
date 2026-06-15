"""Slice 4: `at_operation` — a historical RepoView, differential vs `jj --at-op`."""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import PyjutsuError, RepoView

from tests.diff.jj_cli import JjCli


def test_at_head_operation_matches_head_view(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    view = ws.at_operation(ws.head_operation())
    assert isinstance(view, RepoView)
    assert view.operation_id == ws.head_operation()
    assert view.working_copy() == ws.working_copy()


def test_at_operation_accepts_op_expression(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    # `@-` (previous operation) should match the second entry of the op log.
    assert ws.at_operation("@-").operation_id == ws.operations()[1].id


def test_historical_working_copy_matches_cli(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    prev_op = ws.operations()[1].id  # the operation before the latest
    historical_wc = ws.at_operation(prev_op).working_copy()
    assert historical_wc.change_id == jj.change_id_at_op(linear_repo, prev_op, "@")
    # And it differs from the current `@` (the repo moved on).
    assert historical_wc.change_id != ws.working_copy().change_id


def test_at_operation_is_read_only(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    before = ws.head_operation()
    ws.at_operation(ws.operations()[1].id).log("::@")
    assert ws.head_operation() == before  # reading history wrote nothing


def test_invalid_operation_raises(linear_repo: Path) -> None:
    with pytest.raises(PyjutsuError):
        pyjutsu.Workspace.load(linear_repo).at_operation("deadbeefnotanop")
