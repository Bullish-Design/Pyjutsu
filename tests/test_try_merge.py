"""3-way merge / merge-tree primitive (project 14 §P1): ``RepoView.try_merge`` + ``Commit.tree_id``.

Mirrors gitman's `_merge_tree_relation` truth table: content-equal twins merge to a tree equal to
both tips (no conflict); genuine divergence merges to a tree differing from each tip; overlapping
edits to the same lines conflict.
"""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import RevsetError

from tests.diff.jj_cli import JjCli


def _two_lanes(repo: Path, jj: JjCli, a_content: str, b_content: str, path: str = "x.txt") -> None:
    """Two siblings off root: lane A writes `a_content` to `path`, lane B writes `b_content`."""
    jj.init_colocated(repo)
    (repo / path).write_text(a_content)
    jj(repo, "describe", "-m", "lane A")
    jj(repo, "bookmark", "create", "laneA", "-r", "@")
    jj(repo, "new", "root()", "-m", "lane B")  # sibling off root
    (repo / path).write_text(b_content)
    jj(repo, "bookmark", "create", "laneB", "-r", "@")


def test_content_equal_twins_no_conflict_tree_equals_both(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "twins"
    repo.mkdir()
    _two_lanes(repo, jj, "same\n", "same\n")  # identical content, divergent commit ids
    view = pyjutsu.Workspace.load(repo).head()
    res = view.try_merge("laneA", "laneB")
    assert res.has_conflict is False
    a_tree = view.resolve("laneA").tree_id
    b_tree = view.resolve("laneB").tree_id
    assert a_tree == b_tree  # twins ⇒ same tree
    assert res.tree_id == a_tree == b_tree  # merged tree equals both ⇒ in-sync


def test_genuine_divergence_tree_differs_from_each_tip(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "diverge"
    repo.mkdir()
    # Non-overlapping changes: A adds a.txt, B adds b.txt → clean merge, tree ≠ either tip.
    jj.init_colocated(repo)
    (repo / "a.txt").write_text("a\n")
    jj(repo, "describe", "-m", "A")
    jj(repo, "bookmark", "create", "laneA", "-r", "@")
    jj(repo, "new", "root()", "-m", "B")
    (repo / "b.txt").write_text("b\n")
    jj(repo, "bookmark", "create", "laneB", "-r", "@")
    view = pyjutsu.Workspace.load(repo).head()
    res = view.try_merge("laneA", "laneB")
    assert res.has_conflict is False  # non-overlapping ⇒ clean
    assert res.tree_id != view.resolve("laneA").tree_id  # merge added B's content
    assert res.tree_id != view.resolve("laneB").tree_id  # merge added A's content


def test_overlapping_edits_conflict(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "conflict"
    repo.mkdir()
    _two_lanes(repo, jj, "alpha\n", "beta\n")  # same path, incompatible content
    view = pyjutsu.Workspace.load(repo).head()
    res = view.try_merge("laneA", "laneB")
    assert res.has_conflict is True


def test_explicit_base_3way(tmp_path: Path, jj: JjCli) -> None:
    # A fixed base: base has x=1; A changes it to 2, B leaves it. 3-way against base ⇒ takes A's 2,
    # no conflict, and the merged tree matches lane A's tree (B contributed nothing new vs base).
    repo = tmp_path / "base3"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "x.txt").write_text("1\n")
    jj(repo, "describe", "-m", "base")
    jj(repo, "bookmark", "create", "base", "-r", "@")
    jj(repo, "new", "base", "-m", "A")
    (repo / "x.txt").write_text("2\n")
    jj(repo, "bookmark", "create", "laneA", "-r", "@")
    jj(repo, "new", "base", "-m", "B")  # B leaves x.txt as base's "1\n"
    jj(repo, "bookmark", "create", "laneB", "-r", "@")
    view = pyjutsu.Workspace.load(repo).head()
    res = view.try_merge("laneA", "laneB", base="base")
    assert res.has_conflict is False
    assert res.tree_id == view.resolve("laneA").tree_id


def test_rejects_multi_revision_endpoint(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "multi"
    repo.mkdir()
    _two_lanes(repo, jj, "a\n", "b\n")
    view = pyjutsu.Workspace.load(repo).head()
    with pytest.raises(RevsetError):
        view.try_merge("laneA|laneB", "root()")
