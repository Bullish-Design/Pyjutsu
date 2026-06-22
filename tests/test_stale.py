"""Slice 6: stale working copy (`Workspace.is_stale` / `update_stale`) — differential vs the CLI.

Within one colocated workspace the binding keeps `@` and the on-disk working copy in lockstep
(every `@`-rewrite checks out), so staleness must be induced by an *external* actor. The pinned
CLI's `--ignore-working-copy edit <A>` advances the repo `@` without snapshotting or checking out,
leaving the on-disk tree behind — the clean trigger (verified against the pinned 0.42.0 CLI). The
binding then reports `is_stale()`, refuses to mutate/snapshot, and `update_stale()` reconciles by
checking out the recorded `@`, matching `jj workspace update-stale`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import StaleWorkingCopyError

from tests.diff.jj_cli import JjCli


def _copy_repo(src: Path, dst: Path) -> Path:
    """A byte-identical sibling repo (same change ids, commit ids, and op log)."""
    shutil.copytree(src, dst)
    return dst


def _oldest_change(jj: JjCli, repo: Path) -> str:
    """The oldest non-root change in `linear_repo` (commit A, tree = a.txt only)."""
    return jj.change_ids(repo, "::@ ~ root()")[-1]  # newest-first ⇒ last == oldest


def test_is_stale_true_after_external_edit(linear_repo: Path, jj: JjCli) -> None:
    a_change = _oldest_change(jj, linear_repo)
    # Advance the repo `@` to A without touching disk → on-disk tree (a,b,c) is now behind.
    jj(linear_repo, "--ignore-working-copy", "edit", a_change)

    # Sanity: the later files are still on disk before any reconcile.
    for f in ("a.txt", "b.txt", "c.txt"):
        assert (linear_repo / f).exists()

    ws = pyjutsu.Workspace.load(linear_repo)
    assert ws.is_stale() is True


def test_is_stale_false_when_fresh(linear_repo: Path, scratch_repo: Path) -> None:
    # Untouched fixture: `@` and disk are in lockstep.
    assert pyjutsu.Workspace.load(linear_repo).is_stale() is False

    # A freshly-snapshotted clean scratch repo is likewise fresh.
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.snapshot()
    assert ws.is_stale() is False


def test_update_stale_reconciles_matches_cli(
    linear_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    other = _copy_repo(linear_repo, tmp_path / "copy")
    a_change = _oldest_change(jj, linear_repo)
    for repo in (linear_repo, other):
        jj(repo, "--ignore-working-copy", "edit", a_change)

    ws = pyjutsu.Workspace.load(linear_repo)
    c = ws.update_stale()
    jj(other, "workspace", "update-stale")

    # The reconciled `@` is A and matches the CLI's checked-out `@`.
    assert c is not None
    assert c.commit_id == jj.commit_id(linear_repo, "@")
    assert jj.commit_id(linear_repo, "@") == jj.commit_id(other, "@")

    # On both repos the on-disk tree is now A's: a.txt stays, b.txt/c.txt are gone.
    for repo in (linear_repo, other):
        assert (repo / "a.txt").exists()
        assert not (repo / "b.txt").exists()
        assert not (repo / "c.txt").exists()

    # Staleness is cleared.
    assert ws.is_stale() is False


def test_update_stale_noop_when_fresh(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    ops_before = len(jj.op_log_ids(linear_repo))

    assert ws.update_stale() is None  # matches the CLI's "the working copy is not stale" no-op
    assert len(jj.op_log_ids(linear_repo)) == ops_before
    assert ws.is_stale() is False


def test_mutation_on_stale_raises(linear_repo: Path, jj: JjCli) -> None:
    a_change = _oldest_change(jj, linear_repo)
    jj(linear_repo, "--ignore-working-copy", "edit", a_change)

    # Count ops via the binding (loads at head without snapshotting): the CLI's `op log` would
    # itself refuse on the stale `@`.
    ws = pyjutsu.Workspace.load(linear_repo)
    ops_before = len(ws.operations())

    # The auto-snapshot on `__enter__` raises before any mutation is recorded.
    with pytest.raises(StaleWorkingCopyError):
        with ws.transaction("describe") as tx:
            tx.describe("@", "m")

    # Nothing was published.
    assert len(ws.operations()) == ops_before


def test_snapshot_on_stale_raises(linear_repo: Path, jj: JjCli) -> None:
    a_change = _oldest_change(jj, linear_repo)
    jj(linear_repo, "--ignore-working-copy", "edit", a_change)

    ws = pyjutsu.Workspace.load(linear_repo)
    ops_before = len(ws.operations())

    with pytest.raises(StaleWorkingCopyError):
        ws.snapshot()

    assert len(ws.operations()) == ops_before


def test_update_stale_then_mutate(linear_repo: Path, jj: JjCli) -> None:
    a_change = _oldest_change(jj, linear_repo)
    jj(linear_repo, "--ignore-working-copy", "edit", a_change)

    ws = pyjutsu.Workspace.load(linear_repo)
    assert ws.update_stale() is not None

    # Reconcile cleared the block: a mutation now succeeds.
    with ws.transaction("describe") as tx:
        described = tx.describe("@", "reconciled")
    assert described.description.rstrip("\n") == "reconciled"
