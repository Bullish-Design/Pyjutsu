"""Native Jujutsu store garbage collection and re-adopt cleanup."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pyjutsu
import pytest


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _make_git_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "Initial commit")


def _keep_refs(repo: Path) -> set[str]:
    out = _git(repo, "for-each-ref", "--format=%(refname)", "refs/jj/keep/")
    return {line for line in out.splitlines() if line}


def _add_old_orphan_keep_ref(repo: Path) -> tuple[str, str]:
    head = _git(repo, "rev-parse", "HEAD").strip()
    tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
    orphan = _git(repo, "commit-tree", tree, "-p", head, "-m", "orphan").strip()
    ref = f"refs/jj/keep/{orphan}"
    _git(repo, "update-ref", ref, orphan)
    # The default cutoff preserves objects newer than two weeks. Make this loose ref unambiguously
    # old so a no-argument gc exercises the default while remaining independent of wall-clock drift.
    old_epoch = datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(repo / ".git" / ref, (old_epoch, old_epoch))
    return head, orphan


def test_gc_keeps_reachable_refs_removes_unreachable_and_publishes_no_operation(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _make_git_repo(repo)
    ws = pyjutsu.Workspace.init(repo, colocate=True)
    head, orphan = _add_old_orphan_keep_ref(repo)

    before_refs = _keep_refs(repo)
    assert any(ref.endswith(head) for ref in before_refs)
    assert f"refs/jj/keep/{orphan}" in before_refs
    before_op = ws.head_operation()

    ws.gc()

    after_refs = _keep_refs(repo)
    assert any(ref.endswith(head) for ref in after_refs)
    assert f"refs/jj/keep/{orphan}" not in after_refs
    assert ws.head_operation() == before_op


def test_readopt_leaves_stale_keep_refs_until_gc(tmp_path: Path) -> None:
    repo = tmp_path / "readopt"
    _make_git_repo(repo)
    pyjutsu.Workspace.init(repo, colocate=True)
    head, orphan = _add_old_orphan_keep_ref(repo)

    shutil.rmtree(repo / ".jj")
    ws = pyjutsu.Workspace.init(repo, colocate=True)

    before = _keep_refs(repo)
    assert f"refs/jj/keep/{orphan}" in before
    assert any(ref.endswith(head) for ref in before)
    ws.gc()
    after = _keep_refs(repo)
    assert f"refs/jj/keep/{orphan}" not in after
    assert any(ref.endswith(head) for ref in after)


def test_gc_requires_timezone_aware_cutoff(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    ws = pyjutsu.Workspace.init(repo, colocate=True)

    with pytest.raises(ValueError, match="timezone-aware"):
        ws.gc(datetime(2026, 1, 1))
