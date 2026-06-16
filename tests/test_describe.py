"""Slice 1: `tx.describe` — differential vs `jj describe`.

To compare commit ids, the mutation is applied to two byte-identical repos: one via Pyjutsu, one
via the pinned CLI. Identical starting state comes from copying the repo directory (change ids are
random, so they can't be reproduced by replay); the pinned `debug.commit-timestamp` then makes the
rewritten commit id deterministic. Assertions: change id stable, commit id + description match the
CLI, and exactly one new operation on each side (clean `@` ⇒ no snapshot).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import RevsetError

from tests.diff.jj_cli import JjCli


def _copy_repo(src: Path, dst: Path) -> Path:
    """A byte-identical sibling repo (same change ids, commit ids, and op log)."""
    shutil.copytree(src, dst)
    return dst


def test_describe_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    # Sanity: the copy starts identical to the original.
    assert jj.commit_id(scratch_repo, "@") == jj.commit_id(other, "@")
    base_change = jj.change_id(scratch_repo, "@")
    ops_before = len(jj.op_log_ids(scratch_repo))

    # Pyjutsu describes `@` on the original; the CLI describes `@` on the copy.
    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("describe @") as tx:
        described = tx.describe("@", "described via pyjutsu")
    jj(other, "describe", "-m", "described via pyjutsu")

    # Change id is preserved; commit id + description match the CLI's result exactly. (The stored
    # description carries jj's trailing newline; the matching commit id confirms byte-equality.)
    assert described.change_id == base_change
    assert described.commit_id == jj.commit_id(other, "@")
    assert described.description.rstrip("\n") == "described via pyjutsu"

    # Reading back through Pyjutsu: `@` is now the described commit.
    assert ws.working_copy().commit_id == described.commit_id
    assert ws.resolve("@").change_id == base_change

    # Exactly one new operation on each side (clean `@`, so no preceding snapshot op).
    assert len(ws.operations()) == ops_before + 1
    assert len(jj.op_log_ids(other)) == ops_before + 1


def test_describe_by_change_id_targets_the_right_commit(linear_repo: Path, jj: JjCli) -> None:
    # Describe a non-`@` commit named by its change id; the rest of the graph is untouched.
    target_change = jj.change_ids(linear_repo, "::@")[2]  # an older commit in the stack
    graph_before = jj.change_ids(linear_repo, "::@")

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("describe older commit") as tx:
        described = tx.describe(target_change, "retitled")

    assert described.change_id == target_change
    assert described.description.rstrip("\n") == "retitled"
    # The change graph (change ids + ordering) is unchanged — describe rewrites in place.
    assert ws.head().log("::@") and [c.change_id for c in ws.head().log("::@")] == graph_before


def test_describe_unknown_revision_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(RevsetError):
        with ws.transaction("bad") as tx:
            tx.describe("nonexistent-change-id", "x")


def test_describe_outside_with_block_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    tx = ws.transaction("never entered")
    with pytest.raises(RuntimeError):
        tx.describe("@", "x")
