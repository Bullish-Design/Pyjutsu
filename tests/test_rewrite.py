"""Slice 8: `tx.rebase` / `tx.squash` / `tx.restore` — differential vs `jj rebase`/`squash`/`restore`.

These are commit-rewrite mutations whose result is a *commit* (deterministic id under the pinned
`debug.commit-timestamp`), so — like `test_describe`/`test_new` — the mutation is applied to two
byte-identical repos (one via Pyjutsu, one via the pinned CLI, identical state from copying the repo
directory) and we compare commit/change ids + graph + trees. Unlike the backward `undo`/`restore_op`
of slice 7, these move *forward* (new commit ids), so the colocated git-HEAD re-import doesn't bite:
reading the binding-mutated repo with `jj` is fine.

Out of scope (documented refinements, flagged not faked): `jj rebase -r`/`-b`, partial/interactive
squash + jj's description-combining default, and non-`Keep` empty behavior.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import ImmutableCommitError, PyjutsuError

from tests.diff.jj_cli import JjCli


def _copy_repo(src: Path, dst: Path) -> Path:
    """A byte-identical sibling repo (same change ids, commit ids, and op log)."""
    shutil.copytree(src, dst)
    return dst


# --- rebase --------------------------------------------------------------------------------------


def test_rebase_subtree_matches_cli(linear_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # `linear_repo` is A → B → C → @ (empty). Rebase B (and its descendants C, @) onto A. Since B is
    # already a child of A this is a no-op move structurally, so instead rebase C onto A: C and @
    # detach from B and reattach to A, carrying the descendant `@` — the `-s` semantics.
    other = _copy_repo(linear_repo, tmp_path / "copy")
    chain = jj.change_ids(linear_repo, "::@ ~ root()")  # newest-first: [@, C, B, A]
    a_change, c_change = chain[-1], chain[-3]
    a_commit = jj.commit_id(linear_repo, a_change)

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("rebase C onto A") as tx:
        rebased = tx.rebase(c_change, onto=a_change)
    jj(other, "rebase", "-s", c_change, "-d", a_change)

    # The rebased C keeps its change id; its parent is now A.
    assert rebased.change_id == c_change
    assert rebased.parent_ids == [a_commit]
    assert jj.parent_commit_ids(other, c_change) == [a_commit]
    # Commit-id parity on the rebased commit (deterministic), and the descendant `@` came along.
    assert jj.commit_id(linear_repo, c_change) == jj.commit_id(other, c_change)
    assert jj.parent_commit_ids(linear_repo, "@") == [jj.commit_id(linear_repo, c_change)]
    # The whole surviving change-id graph matches the CLI's.
    assert jj.change_ids(linear_repo, "::@") == jj.change_ids(other, "::@")


def test_rebase_carries_descendants(linear_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Rebasing B onto A's parent (root) must drag C and @ along — proving `Roots` carries the subtree.
    other = _copy_repo(linear_repo, tmp_path / "copy")
    chain = jj.change_ids(linear_repo, "::@ ~ root()")  # [@, C, B, A]
    b_change = chain[-2]

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("rebase B onto root") as tx:
        tx.rebase(b_change, onto="root()")
    jj(other, "rebase", "-s", b_change, "-d", "root()")

    # B now sits on root; C and @ still trail it. Full graph + commit-id parity with the CLI.
    assert jj.parent_commit_ids(linear_repo, b_change) == [jj.commit_id(linear_repo, "root()")]
    assert jj.change_ids(linear_repo, "::@") == jj.change_ids(other, "::@")
    assert jj.commit_id(linear_repo, b_change) == jj.commit_id(other, b_change)


def test_rebase_root_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(ImmutableCommitError):
        with ws.transaction("rebase root") as tx:
            tx.rebase("root()", onto="@")


def test_rebase_unknown_revision_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(pyjutsu.RevsetError):
        with ws.transaction("bad") as tx:
            tx.rebase("nonexistent-change-id", onto="@")


def test_rebase_outside_with_block_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    tx = ws.transaction("never entered")
    with pytest.raises(RuntimeError):
        tx.rebase("@", onto="@-")


# --- squash --------------------------------------------------------------------------------------


def test_squash_into_parent_matches_cli(diffstat_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # `diffstat_repo`: base(`@--`) → edit(`@-`, changes a.txt + adds b.txt) → @ (empty). Squash the
    # `edit` commit into its parent `base`: the source change id disappears and `base` absorbs both
    # file changes.
    other = _copy_repo(diffstat_repo, tmp_path / "copy")
    edit_change = jj.change_id(diffstat_repo, "@-")
    base_change = jj.change_id(diffstat_repo, "@--")

    ws = pyjutsu.Workspace.load(diffstat_repo)
    with ws.transaction("squash edit into base") as tx:
        squashed = tx.squash(edit_change, base_change, message="combined")
    jj(other, "squash", "--from", edit_change, "--into", base_change, "-m", "combined")

    # The squashed `base` keeps its change id and takes the new description; commit-id parity.
    assert squashed.change_id == base_change
    assert squashed.description.rstrip("\n") == "combined"
    assert jj.commit_id(diffstat_repo, base_change) == jj.commit_id(other, base_change)
    # The source change id is gone from the stack on both sides.
    assert edit_change not in jj.change_ids(diffstat_repo, "::@")
    assert edit_change not in jj.change_ids(other, "::@")
    # `base` now carries the source's file changes (a.txt changed +1 line, b.txt added +2).
    assert jj.diff_stat_totals(diffstat_repo, base_change) == jj.diff_stat_totals(other, base_change)
    assert jj.change_ids(diffstat_repo, "::@") == jj.change_ids(other, "::@")


def test_squash_no_message_keeps_destination_description(
    diffstat_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    # Without `message`, the squashed commit keeps `into`'s description — jj's
    # `--use-destination-message`.
    other = _copy_repo(diffstat_repo, tmp_path / "copy")
    edit_change = jj.change_id(diffstat_repo, "@-")
    base_change = jj.change_id(diffstat_repo, "@--")
    base_desc = jj.template(diffstat_repo, base_change, "description").rstrip("\n")

    ws = pyjutsu.Workspace.load(diffstat_repo)
    with ws.transaction("squash keep dest msg") as tx:
        squashed = tx.squash(edit_change, base_change)
    jj(other, "squash", "--from", edit_change, "--into", base_change, "--use-destination-message")

    assert squashed.description.rstrip("\n") == base_desc
    assert jj.commit_id(diffstat_repo, base_change) == jj.commit_id(other, base_change)


def test_squash_into_self_raises(diffstat_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(diffstat_repo)
    with pytest.raises(PyjutsuError):
        with ws.transaction("squash self") as tx:
            tx.squash("@-", "@-")


def test_squash_root_raises(diffstat_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(diffstat_repo)
    with pytest.raises(ImmutableCommitError):
        with ws.transaction("squash root") as tx:
            tx.squash("@-", "root()")


def test_squash_outside_with_block_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    tx = ws.transaction("never entered")
    with pytest.raises(RuntimeError):
        tx.squash("@", "@-")


# --- restore -------------------------------------------------------------------------------------


def test_restore_whole_commit_matches_cli(linear_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Restore `B`'s content from `A` (so B's tree becomes A's: only a.txt, B's b.txt drops). B is a
    # non-leaf so its tree is unambiguous; comparing a non-`@` commit avoids checkout interplay.
    other = _copy_repo(linear_repo, tmp_path / "copy")
    chain = jj.change_ids(linear_repo, "::@ ~ root()")  # [@, C, B, A]
    a_change, b_change = chain[-1], chain[-2]

    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("restore B from A") as tx:
        restored = tx.restore(b_change, from_=a_change)
    jj(other, "restore", "--from", a_change, "--into", b_change)

    # B keeps its change id; its tree now equals A's (commit-id parity proves byte-equal trees).
    assert restored.change_id == b_change
    assert jj.commit_id(linear_repo, b_change) == jj.commit_id(other, b_change)
    # B's tree == A's tree: B now makes no change vs A (its parent), i.e. it's empty.
    assert jj.is_empty(linear_repo, b_change)
    assert jj.is_empty(other, b_change)
    assert jj.change_ids(linear_repo, "::@") == jj.change_ids(other, "::@")


def test_restore_paths_matches_cli(diffstat_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Path-scoped restore: revert only `a.txt` in `@-` from its parent, leaving the added b.txt.
    other = _copy_repo(diffstat_repo, tmp_path / "copy")
    edit_change = jj.change_id(diffstat_repo, "@-")
    base_change = jj.change_id(diffstat_repo, "@--")

    ws = pyjutsu.Workspace.load(diffstat_repo)
    with ws.transaction("restore a.txt in edit") as tx:
        restored = tx.restore(edit_change, from_=base_change, paths=["a.txt"])
    jj(other, "restore", "--from", base_change, "--into", edit_change, "a.txt")

    # Only a.txt reverted: b.txt (+2) still added, a.txt's change gone. Commit-id parity confirms it.
    assert restored.change_id == edit_change
    assert jj.commit_id(diffstat_repo, edit_change) == jj.commit_id(other, edit_change)
    assert jj.diff_stat_totals(diffstat_repo, edit_change) == jj.diff_stat_totals(other, edit_change)


def test_restore_root_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(ImmutableCommitError):
        with ws.transaction("restore root") as tx:
            tx.restore("root()", from_="@")


def test_restore_outside_with_block_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    tx = ws.transaction("never entered")
    with pytest.raises(RuntimeError):
        tx.restore("@", from_="@-")
