"""Slice 0: transaction scaffolding.

Differential invariants (concept §0.1, §6): an empty mutation transaction on a clean `@` publishes
exactly **one** operation carrying the given description; a transaction whose body raises publishes
**zero** (atomic rollback). Plus the single-open-transaction rule and token non-reuse.
"""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import PyjutsuError

from tests.diff.jj_cli import JjCli


def test_empty_transaction_commits_one_op_with_description(scratch_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    ops_before = jj.op_log_ids(scratch_repo)
    graph_before = jj.change_ids(scratch_repo, "::@")

    with ws.transaction("my empty transaction") as tx:
        pass

    # Exactly one new operation, carrying the transaction's description.
    ops_after = jj.op_log_ids(scratch_repo)
    assert len(ops_after) == len(ops_before) + 1
    assert jj.op_head_description(scratch_repo) == "my empty transaction"
    # An empty transaction changes no commits.
    assert jj.change_ids(scratch_repo, "::@") == graph_before
    # Read-back: the token and the workspace agree with the CLI on the published head op.
    assert tx.operation_id == jj.op_head_id(scratch_repo)
    assert ws.head_operation() == tx.operation_id
    newest = ws.operations()[0]
    assert newest.id == tx.operation_id
    assert newest.description == "my empty transaction"
    assert newest.parent_ids == [ops_before[0]]


def test_transaction_rolls_back_on_exception(scratch_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    ops_before = jj.op_log_ids(scratch_repo)
    head_before = jj.op_head_id(scratch_repo)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with ws.transaction("should never land"):
            raise Boom

    # Nothing published: op log and head are byte-identical to before (atomicity).
    assert jj.op_log_ids(scratch_repo) == ops_before
    assert jj.op_head_id(scratch_repo) == head_before


def test_second_open_transaction_is_rejected(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(PyjutsuError):
        with ws.transaction("outer"):
            with ws.transaction("inner"):
                pass


def test_transaction_token_is_not_reusable(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    tx = ws.transaction("once")
    with tx:
        pass
    with pytest.raises(RuntimeError):
        with tx:
            pass
