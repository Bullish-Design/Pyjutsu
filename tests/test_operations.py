"""Slice 4: `operations` + `head_operation`, differential vs the pinned jj op log."""

from __future__ import annotations

from pathlib import Path

import pyjutsu
from pyjutsu import Operation

from tests.diff.jj_cli import JjCli

ROOT_OP_ID = "0" * 128  # jj's root operation


def test_operations_ids_and_order_match_cli(linear_repo: Path, jj: JjCli) -> None:
    ops = pyjutsu.Workspace.load(linear_repo).operations()
    assert [op.id for op in ops] == jj.op_log_ids(linear_repo)


def test_operations_limit(linear_repo: Path, jj: JjCli) -> None:
    ops = pyjutsu.Workspace.load(linear_repo).operations(limit=2)
    assert [op.id for op in ops] == jj.op_log_ids(linear_repo, limit=2)


def test_operations_are_models_with_metadata(linear_repo: Path) -> None:
    ops = pyjutsu.Workspace.load(linear_repo).operations()
    assert all(isinstance(op, Operation) for op in ops)
    newest = ops[0]
    # The newest op in the fixture is the trailing `jj new` (an empty working-copy commit).
    assert newest.description != ""
    assert newest.end_time >= newest.start_time
    assert newest.parent_ids == [ops[1].id]


def test_op_log_ends_at_root_operation(linear_repo: Path) -> None:
    ops = pyjutsu.Workspace.load(linear_repo).operations()
    assert ops[-1].id == ROOT_OP_ID
    assert ops[-1].parent_ids == []


def test_head_operation_matches_cli_and_newest(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    head = ws.head_operation()
    assert head == jj.op_head_id(linear_repo)
    assert head == ws.operations()[0].id
    assert head == ws.head().operation_id
