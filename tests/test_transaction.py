"""Slice 0: transaction scaffolding.

Differential invariants (concept §0.1, §6): an empty mutation transaction on a clean `@` publishes
exactly **one** operation carrying the given description; a transaction whose body raises publishes
**zero** (atomic rollback). Plus the single-open-transaction rule and token non-reuse.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import PyjutsuError, StaleWorkingCopyError

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


def test_commit_publishes_then_checkout_failure_raises_stale(linear_repo: Path, jj: JjCli) -> None:
    """M1 regression: a transaction whose op-store write **succeeds** but whose post-publish
    working-copy checkout **fails** must raise :class:`StaleWorkingCopyError` (not a generic error
    and not silent success) — the operation *is* in the log, and the caller reconciles the on-disk
    copy with :meth:`update_stale`.

    We force the split by making the workspace **root** read-only: `commit` writes the new
    operation/view deep under `.jj/repo/op_store` (still writable) and lands, but the ensuing
    checkout of `A` must delete `b.txt`/`c.txt` from the root — an unlink in a read-only directory
    fails (EACCES), tripping the checkout-failure path.
    """
    ws = pyjutsu.Workspace.load(linear_repo)
    a_change = jj.change_ids(linear_repo, "::@ ~ root()")[-1]  # oldest non-root == A
    a_commit = jj.commit_id(linear_repo, a_change)
    head_before = ws.head_operation()

    original = linear_repo.stat().st_mode
    try:
        linear_repo.chmod(0o555)  # r-x: unlinking b.txt/c.txt from the root fails
        with pytest.raises(StaleWorkingCopyError):
            # auto_snapshot=False so the failure is isolated to the post-commit checkout.
            with ws.transaction("edit A", auto_snapshot=False) as tx:
                tx.edit(a_change)
    finally:
        linear_repo.chmod(original)

    # The operation DID land despite the checkout failure: head advanced and `@` is now A.
    assert ws.head_operation() != head_before
    assert ws.working_copy().commit_id == a_commit

    # The on-disk copy is stale until reconciled; now that the root is writable again,
    # `update_stale` completes the deferred checkout (a.txt stays, b.txt/c.txt disappear).
    ws.update_stale()
    assert (linear_repo / "a.txt").exists()
    assert not (linear_repo / "b.txt").exists()
    assert not (linear_repo / "c.txt").exists()


def test_out_of_band_undo_is_not_excluded_by_open_transaction(linear_repo: Path) -> None:
    """L1 contract: workspace-level mutators are **not** mutually excluded with an open transaction
    (only a second `transaction()` is). A `ws.undo()` run on another thread — via
    :func:`asyncio.to_thread` — while a ``with ws.transaction(...)`` block is open must *not* raise
    the single-open-transaction guard; both publish operations. jj records them as divergent
    operations that later merge (its normal concurrency model, not corruption). This pins that
    documented behaviour so a future guard tightening is a deliberate, test-visible choice.
    """
    ws = pyjutsu.Workspace.load(linear_repo)
    head_before = ws.head_operation()

    async def drive() -> pyjutsu.Operation:
        with ws.transaction("open tx", auto_snapshot=False) as tx:
            # The transaction is open (slot claimed) but holds no workspace lock, so an out-of-band
            # undo on another thread proceeds instead of hitting "a transaction is already open".
            undone = await asyncio.to_thread(ws.undo)
            tx.describe("@", "from the open tx")
        return undone

    undone = asyncio.run(drive())

    # The undo published its own operation (it was not blocked by the open transaction)...
    assert undone.id != head_before
    # ...and the workspace is still consistent: a fresh head loads and reads succeed.
    assert ws.head_operation()
    assert ws.log("::@")
