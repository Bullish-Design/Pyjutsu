"""Annotated git tag write + push (project 13 §P5): ``Workspace.create_tag`` / ``push_tag``.

jj-lib is read-only on tags, so pyjutsu writes the annotated tag *object* straight to the colocated
``.git`` via gix, then imports it into the jj view. These tests verify with **raw git** that the
result is a genuine annotated tag object (``cat-file -t`` == ``tag``) — not a lightweight commit ref
— that it dereferences to the intended commit and carries the message, and that ``push_tag`` copies
that annotated object to a bare remote. Tags are pyjutsu's last raw-git surface, so there is no jj
CLI oracle for the *write* side; the oracle here is git's own object model.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import GitError, RevsetError

from tests.diff.jj_cli import JjCli


def _git(git_dir: Path, *args: str) -> str:
    """Run ``git -C <git_dir> <args>`` and return stripped stdout (raises on failure)."""
    return subprocess.run(
        ["git", "-C", str(git_dir), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_bare(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    return path


def _object_type(git_dir: Path, ref: str) -> str:
    return _git(git_dir, "cat-file", "-t", ref)


def test_create_writes_a_real_annotated_tag_object(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    target = jj.commit_id(linear_repo, "@-")  # C, a real described commit
    op = ws.create_tag("v1.0", "@-", "release one")

    assert op is not None  # importing the new tag published an operation
    # It is an *annotated tag object*, not a lightweight ref pointing straight at the commit.
    assert _object_type(linear_repo, "refs/tags/v1.0") == "tag"
    # It dereferences to the intended commit and carries the message + a tagger line.
    assert _git(linear_repo, "rev-parse", "v1.0^{commit}") == target
    body = _git(linear_repo, "cat-file", "-p", "refs/tags/v1.0")
    assert "release one" in body
    assert body.startswith("object ")
    assert "\ntagger " in body


def test_create_tag_duplicate_requires_force(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    # Capture B's commit id up front: creating a tag must not move `@` or rewrite commits, so the
    # revset `@--` must still name this same commit after the writes below (a regression guard).
    commit_b = jj.commit_id(linear_repo, "@--")
    ws.create_tag("dup", "@-", "first")  # points at C
    with pytest.raises(GitError):
        ws.create_tag("dup", "@--", "second")  # name clash ⇒ refused without force

    # force=True overwrites, retargeting to B (`@--`).
    ws.create_tag("dup", "@--", "second", force=True)
    assert _object_type(linear_repo, "refs/tags/dup") == "tag"
    assert _git(linear_repo, "rev-parse", "dup^{commit}") == commit_b
    # The tag write did not disturb the commit graph: `@--` still resolves to the same commit.
    assert jj.commit_id(linear_repo, "@--") == commit_b


def test_create_tag_rejects_multi_revision_target(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(RevsetError):
        ws.create_tag("bad", "@-|@--", "two targets")


def test_push_tag_lands_the_annotated_object_on_remote(
    linear_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(linear_repo)
    target = jj.commit_id(linear_repo, "@-")
    ws.add_remote("origin", str(origin))
    ws.create_tag("v2", "@-", "second release")

    op = ws.push_tag("v2", "origin")

    assert op is not None
    assert "push" in op.description.lower()
    # The remote receives the *annotated* tag object (copied, not downgraded to a lightweight ref)
    # and the target commit object it points at.
    assert _object_type(origin, "refs/tags/v2") == "tag"
    assert _git(origin, "rev-parse", "v2^{commit}") == target


def test_push_tag_is_idempotent(linear_repo: Path, tmp_path: Path) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(linear_repo)
    ws.add_remote("origin", str(origin))
    ws.create_tag("v3", "@-", "x")

    assert ws.push_tag("v3", "origin") is not None  # first push lands the tag
    assert ws.push_tag("v3", "origin") is None  # remote already up to date ⇒ no operation


def test_push_tag_without_local_tag_raises(linear_repo: Path, tmp_path: Path) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(linear_repo)
    ws.add_remote("origin", str(origin))
    with pytest.raises(GitError):
        ws.push_tag("ghost", "origin")
