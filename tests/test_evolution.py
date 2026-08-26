"""C4: evolution and predecessors (lane `003/c4`).

Oracle is the pinned `jj` CLI's `jj evolog`. The fixture rewrites one change
several ways (describe, amend, rebase) so the evolution chain has real depth.
"""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import EvolutionEntry, PyjutsuError

from tests.diff.jj_cli import JjCli


def _evolog_commit_ids(jj: JjCli, repo: Path, revset: str) -> list[str]:
    """The commit ids `jj evolog -r <revset>` shows, newest first."""
    out = jj(repo, "evolog", "-r", revset, "--no-graph", "-T", 'commit.commit_id() ++ "\\n"')
    return [line for line in out.splitlines() if line]


def test_evolution_describe_then_amend(scratch_repo: Path, jj: JjCli) -> None:
    """A described-then-amended commit: the amended commit lists the described one as
    predecessor."""
    (scratch_repo / "f.txt").write_text("one\n")
    jj(scratch_repo, "describe", "-m", "first")
    change = jj.change_id(scratch_repo, "@")
    jj(scratch_repo, "describe", "-m", "second")  # amend-style rewrite of @

    ws = pyjutsu.Workspace.load(scratch_repo)
    entries = ws.head().evolution(change)

    # The CLI oracle for the same change (via its current @ commit) agrees commit-for-commit.
    # The chain includes the fixture's describe and snapshot steps, so only the structure is
    # asserted, not the length.
    assert [e.commit.commit_id for e in entries] == _evolog_commit_ids(jj, scratch_repo, "@")
    newest = entries[0]
    assert newest.commit.description.strip() == "second"
    # The amended commit evolved from the described one (the rewrite link).
    assert newest.commit.predecessor_ids == [entries[1].commit.commit_id]
    # The described-then-amended step is in the chain.
    assert any(e.commit.description.strip() == "first" for e in entries)
    # The oldest step has no predecessor; every step carries its creating operation.
    assert entries[-1].commit.predecessor_ids == []
    assert all(e.operation is not None for e in entries)


def test_evolution_after_rebase(linear_repo: Path, jj: JjCli) -> None:
    """Rebasing moves a commit to a new id; the rebased commit's predecessor is the old one."""
    ws = pyjutsu.Workspace.load(linear_repo)
    # linear_repo: A (@---) -> B (@--) -> C (@-) -> @(empty). Rebase B onto C.
    change = ws.resolve("@--").change_id
    old_id = ws.resolve("@--").commit_id

    with ws.transaction("rebase @-- onto @-") as tx:
        tx.rebase("@--", onto=["@-"], mode="revision")

    entries = ws.head().evolution(change)
    ids = [e.commit.commit_id for e in entries]
    assert old_id in ids
    newest = entries[0]
    assert newest.commit.commit_id != old_id
    assert old_id in newest.commit.predecessor_ids


def test_evolution_after_abandon(linear_repo: Path, jj: JjCli) -> None:
    """Abandoning a commit removes its change from the visible set: evolution is empty, and the
    CLI's `jj evolog -r <change>` errors "Revision doesn't exist" for the same reason."""
    ws = pyjutsu.Workspace.load(linear_repo)
    change = ws.resolve("@-").change_id

    with ws.transaction("abandon @-") as tx:
        tx.abandon("@-")

    assert ws.head().evolution(change) == []


def test_evolution_limit(scratch_repo: Path, jj: JjCli) -> None:
    (scratch_repo / "f.txt").write_text("one\n")
    jj(scratch_repo, "describe", "-m", "first")
    change = jj.change_id(scratch_repo, "@")
    jj(scratch_repo, "describe", "-m", "second")
    jj(scratch_repo, "describe", "-m", "third")

    entries = pyjutsu.Workspace.load(scratch_repo).head().evolution(change, limit=2)
    assert len(entries) == 2
    assert entries[0].commit.description.strip() == "third"


def test_evolution_unknown_change_id_is_empty(scratch_repo: Path) -> None:
    view = pyjutsu.Workspace.load(scratch_repo).head()
    assert view.evolution("k" * 32) == []  # full-length but unknown


def test_evolution_malformed_change_id_raises(scratch_repo: Path) -> None:
    view = pyjutsu.Workspace.load(scratch_repo).head()
    with pytest.raises(PyjutsuError):
        view.evolution("not-a-change-id")
    with pytest.raises(PyjutsuError):
        view.evolution("")


def test_evolution_entries_are_models(scratch_repo: Path, jj: JjCli) -> None:
    (scratch_repo / "f.txt").write_text("one\n")
    jj(scratch_repo, "describe", "-m", "only")
    entries = pyjutsu.Workspace.load(scratch_repo).head().evolution(jj.change_id(scratch_repo, "@"))
    # The CLI shows the full chain (the fixture's describe + snapshot steps included); the
    # binding must agree commit-for-commit.
    assert [e.commit.commit_id for e in entries] == _evolog_commit_ids(jj, scratch_repo, "@")
    entry = entries[0]
    assert isinstance(entry, EvolutionEntry)
    assert entry.commit.description.strip() == "only"
    assert entry.operation is not None
    assert entry.operation.description
