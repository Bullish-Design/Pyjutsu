"""D5: git worktrees (lane `004/d5`).

Oracle is `git worktree list --porcelain`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pyjutsu

from tests.diff.jj_cli import JjCli


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _porcelain(repo: Path) -> list[dict[str, str | bool]]:
    """Parse `git worktree list --porcelain` into one dict per worktree, in git's order."""
    out = _git(repo, "worktree", "list", "--porcelain").stdout
    entries: list[dict[str, str | bool]] = []
    current: dict[str, str | bool] = {}
    for line in out.splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value if value else True
    if current:
        entries.append(current)
    return entries


def _repo_with_commit(tmp_path: Path, jj: JjCli, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "a.txt").write_text("alpha\n")
    jj(repo, "describe", "-m", "commit A")
    jj(repo, "bookmark", "create", "main", "-r", "@")
    jj(repo, "new")
    jj(repo, "git", "export")
    return repo


def test_only_the_main_worktree_by_default(tmp_path: Path, jj: JjCli) -> None:
    repo = _repo_with_commit(tmp_path, jj, "solo")
    ws = pyjutsu.Workspace.load(repo)

    trees = ws.git.worktrees()
    entries = _porcelain(repo)
    assert len(trees) == len(entries) == 1

    only = trees[0]
    assert only.main is True
    assert Path(only.path).resolve() == Path(str(entries[0]["worktree"])).resolve()
    assert only.locked is False
    assert only.prunable is False


def test_a_linked_worktree_matches_git(tmp_path: Path, jj: JjCli) -> None:
    repo = _repo_with_commit(tmp_path, jj, "linked")
    linked = tmp_path / "linked-wt"
    _git(repo, "worktree", "add", "-b", "side", str(linked), "main")

    ws = pyjutsu.Workspace.load(repo)
    trees = ws.git.worktrees()
    entries = _porcelain(repo)
    assert len(trees) == len(entries) == 2

    # git lists the main worktree first, and so does the binding.
    assert trees[0].main is True
    side = trees[1]
    assert side.main is False
    assert Path(side.path).resolve() == linked.resolve()
    assert side.branch == entries[1]["branch"]
    assert side.head_oid == entries[1]["HEAD"]
    assert side.locked is False
    assert side.prunable is False


def test_a_locked_worktree_reports_locked(tmp_path: Path, jj: JjCli) -> None:
    repo = _repo_with_commit(tmp_path, jj, "lockable")
    linked = tmp_path / "locked-wt"
    _git(repo, "worktree", "add", "-b", "held", str(linked), "main")
    _git(repo, "worktree", "lock", str(linked))

    ws = pyjutsu.Workspace.load(repo)
    side = next(t for t in ws.git.worktrees() if not t.main)
    assert side.locked is True
    assert _porcelain(repo)[1].get("locked") is True


def test_a_removed_checkout_reports_prunable(tmp_path: Path, jj: JjCli) -> None:
    """git calls a worktree prunable when its checkout directory is gone."""
    repo = _repo_with_commit(tmp_path, jj, "prunable")
    linked = tmp_path / "gone-wt"
    _git(repo, "worktree", "add", "-b", "gone", str(linked), "main")
    shutil.rmtree(linked)

    ws = pyjutsu.Workspace.load(repo)
    side = next(t for t in ws.git.worktrees() if not t.main)
    assert side.prunable is True
    assert "prunable" in _porcelain(repo)[1]


def test_a_detached_worktree_has_no_branch(tmp_path: Path, jj: JjCli) -> None:
    repo = _repo_with_commit(tmp_path, jj, "detached")
    linked = tmp_path / "detached-wt"
    _git(repo, "worktree", "add", "--detach", str(linked), "main")

    ws = pyjutsu.Workspace.load(repo)
    side = next(t for t in ws.git.worktrees() if not t.main)
    assert side.branch is None
    assert side.head_oid == _porcelain(repo)[1]["HEAD"]
    assert "detached" in _porcelain(repo)[1]


def test_listing_publishes_no_operation(tmp_path: Path, jj: JjCli) -> None:
    repo = _repo_with_commit(tmp_path, jj, "readonly")
    ws = pyjutsu.Workspace.load(repo)
    before = ws.head_operation()
    ws.git.worktrees()
    assert ws.head_operation() == before
