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


def test_failed_commit_releases_transaction_slot(scratch_repo: Path) -> None:
    """H1 regression: if the op-store write fails when committing, the single-transaction slot
    must still be released so the workspace isn't permanently wedged (otherwise every later
    `transaction()` would raise "already open").

    We force the failure by making the op-store's `operations`/`views` directories read-only, so
    `Transaction::commit`'s attempt to write the new operation/view file errors. The mutation
    itself (`describe`) is in-memory and succeeds; only the publish fails.
    """
    ws = pyjutsu.Workspace.load(scratch_repo)

    op_store = scratch_repo / ".jj" / "repo" / "op_store"
    locked = [op_store / "operations", op_store / "views"]
    original = {d: d.stat().st_mode for d in locked}
    try:
        for d in locked:
            d.chmod(0o555)  # r-x: creating a new file inside fails (EACCES)
        with pytest.raises(pyjutsu.PyjutsuError):
            # auto_snapshot=False so the failure is isolated to this transaction's commit.
            with ws.transaction("doomed", auto_snapshot=False) as tx:
                tx.describe("@", "never lands")
    finally:
        for d, mode in original.items():
            d.chmod(mode)

    # The slot must be free: a fresh transaction opens (not "already open") and commits cleanly.
    with ws.transaction("recovers", auto_snapshot=False) as tx:
        tx.describe("@", "ok")
    assert tx.operation_id is not None
    assert ws.head_operation() == tx.operation_id
