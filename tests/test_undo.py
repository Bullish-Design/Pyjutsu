"""Slice 7: `Workspace.undo` / `Workspace.restore_operation` — operation-log writes.

These are differential against the pinned `jj` CLI, but on **resulting repo state**, not op ids:
op ids embed wall-clock time + hostname, so the binding's new op never equals the CLI's. Each
scenario applies the same op-log write to two byte-identical copies (binding vs CLI) and asserts the
repos end up describing the same `@`/graph/bookmarks. Every scenario is **local-only** (no git
remote between the operations involved), so the plain `merge`/`set_view` primitives the binding uses
produce a byte-identical view to the CLI's portion-aware `jj undo`/`jj op restore` (partial-restore
portions are a documented refinement, out of scope — see the slice 7 guide §1/§6).

The >1-parent ("cannot undo a merge operation") guard is covered by code inspection: producing a
merge *operation* (as opposed to a merge commit) requires concurrent divergent op heads, which the
harness can't easily stage; the 0-parent guard below exercises the same refusal path.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import Operation, PyjutsuError

from tests.conftest import WC_DESCRIPTION
from tests.diff.jj_cli import JjCli


def _copy_repo(src: Path, dst: Path) -> Path:
    """A byte-identical sibling repo (same change ids, commit ids, and op log)."""
    shutil.copytree(src, dst)
    return dst


def test_undo_describe_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    original_at = jj.commit_id(scratch_repo, "@")  # deterministic; identical in both copies

    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("d") as tx:
        tx.describe("@", "v2")
    op = ws.undo()

    jj(other, "describe", "-m", "v2")
    jj(other, "undo")

    # The published op is the reverse of the describe.
    assert isinstance(op, Operation)
    assert op.description.startswith("undo operation ")

    # `@`'s description is back to the fixture's, and `@` is byte-identical to the original commit
    # on both sides (commit-id parity — the undo reproduced the pre-describe commit exactly).
    assert jj.template(scratch_repo, "@", "description") == WC_DESCRIPTION
    assert jj.template(other, "@", "description") == WC_DESCRIPTION
    assert jj.commit_id(scratch_repo, "@") == jj.commit_id(other, "@") == original_at

    # describe + undo on each side ⇒ the two op logs grew by the same amount.
    assert len(jj.op_log_ids(scratch_repo)) == len(jj.op_log_ids(other))


def test_undo_new_restores_at_and_checks_out(
    linear_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    other = _copy_repo(linear_repo, tmp_path / "copy")
    pre_new_change = jj.change_id(linear_repo, "@")  # same in both copies

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("n") as tx:
        tx.new()  # a fresh empty `@` on top of the old `@`
    ws.undo()  # reverts the `new`; `@` moves back ⇒ on-disk checkout fires

    jj(other, "new")
    jj(other, "undo")

    # `@` is back to the pre-`new` change on both sides, and the working copy was checked out: the
    # full linear tree (a/b/c) is present on disk again.
    assert jj.change_id(linear_repo, "@") == pre_new_change
    assert jj.change_id(other, "@") == pre_new_change
    for repo in (linear_repo, other):
        for f in ("a.txt", "b.txt", "c.txt"):
            assert (repo / f).exists()
    assert len(jj.op_log_ids(linear_repo)) == len(jj.op_log_ids(other))


def test_undo_specific_operation(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    original_at = jj.commit_id(scratch_repo, "@")  # the pre-describe commit; deterministic in both

    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("d") as tx:
        tx.describe("@", "v2")
    describe_op = ws.head_operation()  # the older op we'll target specifically
    with ws.transaction("n") as tx:
        tx.new()
    op = ws.undo(describe_op)  # undo the *describe*, not the head `new`

    jj(other, "describe", "-m", "v2")
    describe_op_cli = jj.op_head_id(other)  # the CLI's own id for the same logical op
    jj(other, "new")
    jj(other, "undo", describe_op_cli)

    assert op.description.startswith("undo operation ")
    # `@` stays the `new` child (a fresh, random change id — uncomparable across the two repos), but
    # rebased onto the **reverted** commit: undoing the older describe put `@`'s parent back to the
    # original commit on both sides. Read the binding through the binding — running `jj` against a
    # colocated binding repo would re-import its (now stale) git HEAD and resurrect the old state.
    wc = ws.working_copy()
    assert wc.is_empty
    assert wc.description == ""
    assert wc.parent_ids == [original_at]
    assert jj.parent_commit_ids(other, "@") == [original_at]
    assert jj.is_empty(other, "@")


def test_undo_root_operation_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    # `jj op log`'s oldest entry is the all-zeros root operation — it has no parent, so there is no
    # reverse to apply (jj itself refuses: "Cannot revert root operation").
    root_op = ws.operations()[-1].id
    with pytest.raises(PyjutsuError):
        ws.undo(root_op)


def test_restore_operation_matches_cli(linear_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(linear_repo, tmp_path / "copy")
    # An earlier op id from the *shared* starting history (valid verbatim in both copies, since
    # `other` is a byte-identical copytree made before either side diverges).
    op0 = jj.op_log_ids(linear_repo)[3]

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("n") as tx:
        tx.new()
    with ws.transaction("d") as tx:
        tx.describe("@", "extra")
    op = ws.restore_operation(op0)

    jj(other, "new")
    jj(other, "describe", "-m", "extra")
    jj(other, "op", "restore", op0)

    assert isinstance(op, Operation)
    assert op.description.startswith("restore to operation ")
    # Both sides now hold op0's recorded view: the `extra` mutations are gone and `@`/the `::@` graph
    # are back to op0's commits, which (being from the shared pre-divergence history) have identical
    # ids in both repos. Read the binding through the binding — probing a colocated binding repo with
    # `jj` would re-import its stale git HEAD and resurrect the discarded state.
    binding_graph = sorted(c.change_id for c in ws.log("::@"))
    assert binding_graph == sorted(jj.change_ids(other, "::@"))
    assert ws.working_copy().commit_id == jj.commit_id(other, "@")


def test_restore_to_head_is_state_noop(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    before_at = jj.commit_id(linear_repo, "@")
    before_graph = jj.change_ids(linear_repo, "::@")

    op = ws.restore_operation(ws.head_operation())

    # A new op may be published, but the *state* (all portions) is identical to head.
    assert isinstance(op, Operation)
    assert op.description.startswith("restore to operation ")
    assert jj.commit_id(linear_repo, "@") == before_at
    assert jj.change_ids(linear_repo, "::@") == before_graph


def test_invalid_operation_raises(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(PyjutsuError):
        ws.undo("deadbeefnotanop")
    with pytest.raises(PyjutsuError):
        ws.restore_operation("deadbeefnotanop")
