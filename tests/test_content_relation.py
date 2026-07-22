"""Content-relation reads (project 13 §P4): ``is_ancestor`` and ``patch_id``.

``is_ancestor`` is a thin DAG-reachability wrapper over jj-lib's index. ``patch_id`` is a stable
hash of the change a commit introduces against its parent(s): it is *commit-id-independent*, so two
commits that make the same change (before/after a rebase or a duplicate that re-hashes the commit)
share a ``patch_id`` while their commit ids differ.
"""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import RevsetError

from tests.diff.jj_cli import JjCli


# --- is_ancestor -------------------------------------------------------------------------------


def test_is_ancestor_linear_history(linear_repo: Path) -> None:
    """A→B→C→@ (so `@---`=A, `@--`=B, `@-`=C)."""
    ws = pyjutsu.Workspace.load(linear_repo)
    assert ws.is_ancestor("@---", "@-") is True  # A is an ancestor of C
    assert ws.is_ancestor("@-", "@---") is False  # C is not an ancestor of A
    assert ws.is_ancestor("root()", "@") is True  # root is an ancestor of everything


def test_is_ancestor_is_reflexive(linear_repo: Path) -> None:
    # Matches `git merge-base --is-ancestor X X` (a commit is its own ancestor).
    ws = pyjutsu.Workspace.load(linear_repo)
    assert ws.is_ancestor("@-", "@-") is True


def test_is_ancestor_rejects_multi_revision_endpoint(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(RevsetError):
        ws.is_ancestor("@-|@--", "@")
    with pytest.raises(RevsetError):
        ws.is_ancestor("@", "@-|@--")


# --- patch_id ----------------------------------------------------------------------------------


def _repo_with_two_equal_changes(repo: Path, jj: JjCli) -> None:
    """Build a repo with two sibling commits X and Y that each add the identical file to root, plus
    a third commit Z that adds the same path with *different* content — a clean, deterministic
    fixture for patch-id equivalence/difference."""
    jj.init_colocated(repo)
    # X: add f.txt on root.
    (repo / "f.txt").write_text("line one\nline two\n")
    jj(repo, "describe", "-m", "X")
    # Y: a fresh commit on root adding the byte-identical file → same diff as X, new commit id.
    jj(repo, "new", "root()")
    (repo / "f.txt").write_text("line one\nline two\n")
    jj(repo, "describe", "-m", "Y")
    # Z: same path, different content → a different diff.
    jj(repo, "new", "root()")
    (repo / "f.txt").write_text("totally different\n")
    jj(repo, "describe", "-m", "Z")


def test_patch_id_equal_for_same_change_distinct_commits(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "pid"
    repo.mkdir()
    _repo_with_two_equal_changes(repo, jj)
    ws = pyjutsu.Workspace.load(repo)

    x = ws.head().resolve('description(substring:"X")')
    y = ws.head().resolve('description(substring:"Y")')
    # Different commits...
    assert x.commit_id != y.commit_id
    # ...but the same change ⇒ the same patch_id.
    assert ws.patch_id('description(substring:"X")') == ws.patch_id('description(substring:"Y")')


def test_patch_id_differs_for_different_change(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "pid"
    repo.mkdir()
    _repo_with_two_equal_changes(repo, jj)
    ws = pyjutsu.Workspace.load(repo)
    assert ws.patch_id('description(substring:"X")') != ws.patch_id('description(substring:"Z")')


def test_patch_id_is_stable_across_duplicate(scratch_repo: Path, jj: JjCli) -> None:
    """`jj duplicate` copies a commit onto the same parent with a **new** commit id but an identical
    diff — the headline use case: the copy's patch_id equals the original's."""
    # scratch_repo's `@` is an (empty) described commit; give it real content to diff.
    (scratch_repo / "g.txt").write_text("alpha\nbeta\n")
    jj(scratch_repo, "describe", "-m", "dupmarker")
    orig = jj.change_ids(scratch_repo, "@")[0]
    jj(scratch_repo, "duplicate", orig)

    # Both the original and its duplicate carry the "dupmarker" description; they are distinct
    # commits but make the identical change.
    both = jj.change_ids(scratch_repo, 'description(substring:"dupmarker")')
    assert len(both) == 2
    a, b = both
    assert jj.commit_id(scratch_repo, a) != jj.commit_id(scratch_repo, b)

    ws = pyjutsu.Workspace.load(scratch_repo)
    assert ws.patch_id(a) == ws.patch_id(b)


def test_patch_id_is_hex_digest(scratch_repo: Path, jj: JjCli) -> None:
    (scratch_repo / "h.txt").write_text("x\n")
    jj(scratch_repo, "describe", "-m", "content")
    pid = pyjutsu.Workspace.load(scratch_repo).patch_id("@")
    assert len(pid) == 40 and all(c in "0123456789abcdef" for c in pid)  # sha1 hex
