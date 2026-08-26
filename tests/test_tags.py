"""Lightweight jj tag creation and push, verified against jj and Git."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import GitError, RevsetError

from tests.diff.jj_cli import JjCli


def _git(git_dir: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(git_dir), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_bare(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    return path


def test_create_tag_is_lightweight_and_publishes_one_operation(
    linear_repo: Path, jj: JjCli
) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    target = jj.commit_id(linear_repo, "@-")
    operations_before = len(ws.operations())

    op = ws.create_tag("v1.0", "@-")

    assert op is not None
    assert len(ws.operations()) == operations_before + 1
    assert "v1.0" in jj(linear_repo, "tag", "list")
    assert _git(linear_repo, "cat-file", "-t", "refs/tags/v1.0") == "commit"
    assert _git(linear_repo, "rev-parse", "refs/tags/v1.0") == target


def test_create_tag_duplicate_requires_force(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    commit_b = jj.commit_id(linear_repo, "@--")
    ws.create_tag("dup", "@-")

    with pytest.raises(GitError):
        ws.create_tag("dup", "@--")

    ws.create_tag("dup", "@--", force=True)
    assert _git(linear_repo, "cat-file", "-t", "refs/tags/dup") == "commit"
    assert _git(linear_repo, "rev-parse", "refs/tags/dup") == commit_b
    assert jj.commit_id(linear_repo, "@--") == commit_b


def test_create_tag_rejects_multi_revision_target(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(RevsetError):
        ws.create_tag("bad", "@-|@--")


def test_push_tag_pushes_lightweight_tag(
    linear_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(linear_repo)
    target = jj.commit_id(linear_repo, "@-")
    ws.add_remote("origin", str(origin))
    ws.create_tag("v2", "@-")

    op = ws.push_tag("v2", "origin")

    assert op is not None
    assert "push" in op.description.lower()
    assert _git(origin, "cat-file", "-t", "refs/tags/v2") == "commit"
    assert _git(origin, "rev-parse", "refs/tags/v2") == target

