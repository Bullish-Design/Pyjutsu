"""Project 11: ``tx.split`` / ``tx.select_tree`` — the hunk-level (sub-file) commit-split binding.

``split`` divides one commit's diff by a *partial* selection (per-file, per-hunk) into two commits —
the missing primitive ``restore``'s whole-file ``FilesMatcher`` can't express. The selection
vocabulary (S3-A) references the very hunks :meth:`RepoView.diff` emits: ``{path: [hunk indices] |
None}`` where ``None`` means the whole file.

Test style mirrors ``test_rewrite``'s differential pattern where a CLI equivalent exists (a
**whole-file** split's remainder equals the path-scoped ``jj restore`` carve → commit-id parity);
hunk-level splits, which jj only does interactively, get structural asserts instead (first = selected
only, second = remainder only, and the two reassemble the original change).
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


def _file(diff: pyjutsu.Diff, path: str) -> pyjutsu.FileChange | None:
    """The :class:`FileChange` for ``path`` in ``diff``, or ``None`` if the path didn't change."""
    return next((f for f in diff.files if f.path == path), None)


def _lines(fc: pyjutsu.FileChange) -> tuple[list[str], list[str]]:
    """A file change's ``(added, removed)`` line contents, newline-stripped, in hunk order."""
    added = [ln.content.rstrip("\n") for h in fc.hunks for ln in h.lines if ln.kind == "added"]
    removed = [ln.content.rstrip("\n") for h in fc.hunks for ln in h.lines if ln.kind == "removed"]
    return added, removed


def _two_hunk_repo(jj: JjCli, repo: Path) -> Path:
    """A repo whose ``@-`` edits one file in two **disjoint** spans (line 2 and line 9); ``@`` empty.

    ``diff(@-)`` for ``app.py`` therefore has exactly two hunks: index 0 = the ``l2 → TOP`` change,
    index 1 = the ``l9 → BOTTOM`` change — the doc's "two disjoint hunks, top vs bottom" shape.
    """
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "app.py").write_text("l1\nl2\nl3\nl4\nl5\nl6\nl7\nl8\nl9\nl10\n")
    jj(repo, "describe", "-m", "base")
    jj(repo, "new")
    (repo / "app.py").write_text("l1\nTOP\nl3\nl4\nl5\nl6\nl7\nl8\nBOTTOM\nl10\n")
    jj(repo, "describe", "-m", "edit")
    jj(repo, "new")
    return repo


# --- hunk-level split (structural) ---------------------------------------------------------------


def test_split_two_disjoint_hunks_siblings(tmp_path: Path, jj: JjCli) -> None:
    # Split off ONLY the top hunk (line 2). `first` (new sibling) must carry just the top change;
    # `second` (the original commit, rewritten in place) just the bottom — and together they cover
    # exactly the original change (disjoint hunks reassemble to the original tree).
    repo = _two_hunk_repo(jj, tmp_path / "twohunk")
    ws = pyjutsu.Workspace.load(repo)
    edit_change = jj.change_id(repo, "@-")
    base_commit = jj.commit_id(repo, "@--")
    orig_added, orig_removed = _lines(_file(ws.diff("@-"), "app.py"))
    assert (sorted(orig_added), sorted(orig_removed)) == (["BOTTOM", "TOP"], ["l2", "l9"])

    with ws.transaction("split top hunk off") as tx:
        first, second = tx.split("@-", {"app.py": [0]})

    # `second` keeps the original change id + parent; `first` is a fresh sibling on the same parent.
    assert second.change_id == edit_change
    assert first.change_id != edit_change
    assert first.parent_ids == [base_commit]
    assert second.parent_ids == [base_commit]

    ws2 = pyjutsu.Workspace.load(repo)
    first_added, first_removed = _lines(_file(ws2.diff(first.change_id), "app.py"))
    second_added, second_removed = _lines(_file(ws2.diff(second.change_id), "app.py"))
    # first = top hunk only, second = bottom hunk only.
    assert (first_added, first_removed) == (["TOP"], ["l2"])
    assert (second_added, second_removed) == (["BOTTOM"], ["l9"])
    # Reassembly: the two disjoint sides together are exactly the original change.
    assert sorted(first_added + second_added) == sorted(orig_added)
    assert sorted(first_removed + second_removed) == sorted(orig_removed)


def test_split_hunk_leaves_other_file_in_remainder(diffstat_repo: Path, jj: JjCli) -> None:
    # `diffstat_repo` @-: a.txt has two hunks (l2→CHANGED @0, appended l4 @1); b.txt is a new file.
    # Select only a.txt's first hunk → `first` carries just that hunk (b.txt stays behind); `second`
    # carries a.txt's remaining hunk PLUS the whole unlisted b.txt.
    ws = pyjutsu.Workspace.load(diffstat_repo)
    with ws.transaction("split a.txt hunk 0") as tx:
        first, second = tx.split("@-", {"a.txt": [0]})

    ws2 = pyjutsu.Workspace.load(diffstat_repo)
    fdiff, sdiff = ws2.diff(first.change_id), ws2.diff(second.change_id)
    # first: only a.txt, only the l2→CHANGED hunk; b.txt not touched.
    assert {f.path for f in fdiff.files} == {"a.txt"}
    assert _lines(_file(fdiff, "a.txt")) == (["CHANGED"], ["l2"])
    # second: a.txt's appended-l4 hunk, and the whole added b.txt rode along (unlisted → remainder).
    assert {f.path for f in sdiff.files} == {"a.txt", "b.txt"}
    assert _lines(_file(sdiff, "a.txt")) == (["l4"], [])
    assert _lines(_file(sdiff, "b.txt")) == (["b1", "b2"], [])


# --- whole-file split (differential vs the restore carve) ----------------------------------------


def test_split_whole_file_matches_restore_carve(
    diffstat_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    # A whole-file split subsumes today's path-scoped `restore` carve: `split(@-, {"a.txt": None})`
    # leaves `second` = @- with a.txt reverted to its parent (b.txt still added) — byte-identical to
    # `jj restore --from base --into @- a.txt`, so the two commit ids match.
    other = _copy_repo(diffstat_repo, tmp_path / "copy")
    edit_change = jj.change_id(diffstat_repo, "@-")
    base_change = jj.change_id(diffstat_repo, "@--")
    base_commit = jj.commit_id(diffstat_repo, "@--")

    ws = pyjutsu.Workspace.load(diffstat_repo)
    with ws.transaction("split a.txt off") as tx:
        first, second = tx.split("@-", {"a.txt": None})
    jj(other, "restore", "--from", base_change, "--into", edit_change, "a.txt")

    # `second` keeps @-'s change id and equals the CLI restore carve → commit-id parity.
    assert second.change_id == edit_change
    assert jj.commit_id(diffstat_repo, edit_change) == jj.commit_id(other, edit_change)
    # `first` is a new sibling on base carrying only a.txt; b.txt stayed with `second`.
    assert first.parent_ids == [base_commit]
    ws2 = pyjutsu.Workspace.load(diffstat_repo)
    assert {f.path for f in ws2.diff(first.change_id).files} == {"a.txt"}
    assert {f.path for f in ws2.diff(second.change_id).files} == {"b.txt"}


# --- stacked mode --------------------------------------------------------------------------------


def test_split_stacked(diffstat_repo: Path, jj: JjCli) -> None:
    # `mode="stacked"` (jj's own `jj split` shape): `first` (selected) is a child of the original
    # parent; `second` (remainder) is reparented ONTO `first`, keeping its change id.
    ws = pyjutsu.Workspace.load(diffstat_repo)
    base_commit = jj.commit_id(diffstat_repo, "@--")
    edit_change = jj.change_id(diffstat_repo, "@-")
    with ws.transaction("stacked split") as tx:
        first, second = tx.split("@-", {"a.txt": None}, mode="stacked")

    assert first.parent_ids == [base_commit]
    assert second.parent_ids == [first.commit_id]
    assert second.change_id == edit_change
    # Stacked second keeps the full original tree, so its diff vs `first` is exactly the remainder.
    ws2 = pyjutsu.Workspace.load(diffstat_repo)
    assert {f.path for f in ws2.diff(first.change_id).files} == {"a.txt"}
    assert {f.path for f in ws2.diff(second.change_id).files} == {"b.txt"}


# --- select_tree primitive -----------------------------------------------------------------------


def test_select_tree_returns_distinct_ids(diffstat_repo: Path) -> None:
    # `select_tree` is the low-level "selection → tree id" primitive. An empty selection yields the
    # parent tree, a full one the commit tree, a partial one something in between — three distinct ids.
    ws = pyjutsu.Workspace.load(diffstat_repo)
    with ws.transaction("select trees") as tx:
        empty_id = tx.select_tree("@-", {})
        partial_id = tx.select_tree("@-", {"a.txt": [0]})
        full_id = tx.select_tree("@-", {"a.txt": None, "b.txt": None})
    assert all(isinstance(x, str) and x for x in (empty_id, partial_id, full_id))
    assert len({empty_id, partial_id, full_id}) == 3


# --- error surface -------------------------------------------------------------------------------


def test_split_empty_selection_raises(diffstat_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(diffstat_repo)
    with pytest.raises(PyjutsuError):
        with ws.transaction("empty selection") as tx:
            tx.split("@-", {})


def test_split_full_selection_raises(diffstat_repo: Path) -> None:
    # Selecting every changed path wholly = the commit's entire change → the second commit would be
    # empty; that's a no-op, not a split (leans on jj-lib's `is_full_selection`).
    ws = pyjutsu.Workspace.load(diffstat_repo)
    with pytest.raises(PyjutsuError):
        with ws.transaction("full selection") as tx:
            tx.split("@-", {"a.txt": None, "b.txt": None})


def test_split_unchanged_path_raises(diffstat_repo: Path) -> None:
    # A path that the commit didn't change may not appear in a selection.
    ws = pyjutsu.Workspace.load(diffstat_repo)
    with pytest.raises(PyjutsuError):
        with ws.transaction("unchanged path") as tx:
            tx.split("@-", {"nonexistent.txt": None})


def test_split_hunk_index_out_of_range_raises(diffstat_repo: Path) -> None:
    # a.txt has two hunks (indices 0, 1); index 5 names no hunk → typed error, not a silent no-op.
    ws = pyjutsu.Workspace.load(diffstat_repo)
    with pytest.raises(PyjutsuError):
        with ws.transaction("bad hunk index") as tx:
            tx.split("@-", {"a.txt": [5]})


def test_split_binary_hunk_selection_raises(tmp_path: Path, jj: JjCli) -> None:
    # Hunk-level selection is text-only: a binary file must be selected whole-file (None); a hunk
    # list on it is a typed error.
    repo = tmp_path / "binary"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "describe", "-m", "base")
    jj(repo, "new")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02\x00garbage\x00")
    jj(repo, "describe", "-m", "add binary")
    jj(repo, "new")

    ws = pyjutsu.Workspace.load(repo)
    with pytest.raises(PyjutsuError):
        with ws.transaction("split binary hunk") as tx:
            tx.split("@-", {"blob.bin": [0]})


def test_split_binary_whole_file_ok(tmp_path: Path, jj: JjCli) -> None:
    # ...but a whole-file (None) selection of a binary file is fine — the merged value is copied
    # verbatim, no hunk assembly. Here the binary is the ONLY change, so pair it with a text file so
    # the selection stays a proper subset.
    repo = tmp_path / "binary_ok"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "describe", "-m", "base")
    jj(repo, "new")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02\x00data\x00")
    (repo / "note.txt").write_text("hello\n")
    jj(repo, "describe", "-m", "add binary + text")
    jj(repo, "new")

    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("carve off the binary") as tx:
        first, second = tx.split("@-", {"blob.bin": None})
    ws2 = pyjutsu.Workspace.load(repo)
    assert {f.path for f in ws2.diff(first.change_id).files} == {"blob.bin"}
    assert {f.path for f in ws2.diff(second.change_id).files} == {"note.txt"}


def test_split_root_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(ImmutableCommitError):
        with ws.transaction("split root") as tx:
            tx.split("root()", {"whatever.txt": None})


def test_split_bad_mode_raises(diffstat_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(diffstat_repo)
    with pytest.raises(PyjutsuError):
        with ws.transaction("bad mode") as tx:
            tx.split("@-", {"a.txt": None}, mode="nonsense")


def test_split_outside_with_block_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    tx = ws.transaction("never entered")
    with pytest.raises(RuntimeError):
        tx.split("@", {"x.txt": None})
