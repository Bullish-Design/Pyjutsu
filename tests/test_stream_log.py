"""Slice 2 — streaming log parity: ``iter_log`` yields exactly ``log``'s commits, in CLI order.

The stream evaluates the revset to ids eagerly, then builds one ``Commit`` model per ``__next__``;
correctness is that it produces the same models, in the same order, as the eager ``log`` (which the
suite already proves matches the CLI).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pyjutsu import Workspace
from pyjutsu import revset as R

from tests.diff.jj_cli import JjCli


@pytest.mark.parametrize("revset", ["::@", "all()", "root()..@"])
def test_iter_log_matches_log(linear_repo: Path, revset: str) -> None:
    """The headline guarantee: streamed commits == the eager log list, same models and order."""
    ws = Workspace.load(linear_repo)
    assert list(ws.iter_log(revset)) == ws.log(revset)


def test_iter_log_matches_cli_order(linear_repo: Path, jj: JjCli) -> None:
    """Streamed change ids match the pinned CLI's log order."""
    ws = Workspace.load(linear_repo)
    got = [c.change_id for c in ws.iter_log("::@")]
    assert got == jj.change_ids(linear_repo, "::@")


def test_iter_log_limit(linear_repo: Path) -> None:
    """``limit`` truncates the streamed ids, matching ``log(..., limit=n)``."""
    ws = Workspace.load(linear_repo)
    streamed = list(ws.iter_log("all()", limit=2))
    assert len(streamed) == 2
    assert streamed == ws.log("all()", limit=2)


def test_iter_log_is_lazy_iterator(linear_repo: Path) -> None:
    """It is a one-shot iterator: ``iter(it) is it`` and exhaustion raises ``StopIteration``."""
    ws = Workspace.load(linear_repo)
    it = ws.iter_log("all()")
    first = next(it)
    assert first.change_id  # a built Commit model
    assert iter(it) is it
    remaining = list(it)  # drain the rest
    assert remaining == ws.log("all()")[1:]
    with pytest.raises(StopIteration):
        next(it)


def test_iter_log_accepts_builder(linear_repo: Path) -> None:
    """The slice-1 builder feeds the stream just like a string (str | Revset coercion)."""
    ws = Workspace.load(linear_repo)
    assert list(ws.iter_log(R.all_())) == ws.log("all()")
