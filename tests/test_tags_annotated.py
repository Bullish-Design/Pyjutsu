"""Retained annotated Git tag path, verified against Git's object model."""

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


def _create_annotated(
    ws: pyjutsu.Workspace,
    name: str,
    target: str,
    message: str,
    *,
    force: bool = False,
) -> pyjutsu.Operation | None:
    with pytest.warns(DeprecationWarning, match=r"ws\.git\.create_tag"):
        return ws.create_tag(name, target, message, force=force)


def test_create_writes_a_real_annotated_tag_object(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    target = jj.commit_id(linear_repo, "@-")
    op = _create_annotated(ws, "v1.0", "@-", "release one")

    assert op is not None
    assert _git(linear_repo, "cat-file", "-t", "refs/tags/v1.0") == "tag"
    assert _git(linear_repo, "rev-parse", "v1.0^{commit}") == target
    body = _git(linear_repo, "cat-file", "-p", "refs/tags/v1.0")
    assert "release one" in body
    assert body.startswith("object ")
    assert "\ntagger " in body


def test_create_tag_duplicate_requires_force(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    commit_b = jj.commit_id(linear_repo, "@--")
    _create_annotated(ws, "dup", "@-", "first")
    with pytest.raises(GitError), pytest.warns(
        DeprecationWarning, match=r"ws\.git\.create_tag"
    ):
        ws.create_tag("dup", "@--", "second")

    _create_annotated(ws, "dup", "@--", "second", force=True)
    assert _git(linear_repo, "cat-file", "-t", "refs/tags/dup") == "tag"
    assert _git(linear_repo, "rev-parse", "dup^{commit}") == commit_b
    assert jj.commit_id(linear_repo, "@--") == commit_b


def test_create_tag_rejects_multi_revision_target(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(RevsetError), pytest.warns(
        DeprecationWarning, match=r"ws\.git\.create_tag"
    ):
        ws.create_tag("bad", "@-|@--", "two targets")


def test_push_tag_lands_the_annotated_object_on_remote(
    linear_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(linear_repo)
    target = jj.commit_id(linear_repo, "@-")
    ws.add_remote("origin", str(origin))
    _create_annotated(ws, "v2", "@-", "second release")

    op = ws.push_tag("v2", "origin")

    assert op is not None
    assert "push" in op.description.lower()
    assert _git(origin, "cat-file", "-t", "refs/tags/v2") == "tag"
    assert _git(origin, "rev-parse", "v2^{commit}") == target


def test_push_tag_is_idempotent(linear_repo: Path, tmp_path: Path) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(linear_repo)
    ws.add_remote("origin", str(origin))
    _create_annotated(ws, "v3", "@-", "x")

    assert ws.push_tag("v3", "origin") is not None
    assert ws.push_tag("v3", "origin") is None


def test_push_tag_without_local_tag_raises(linear_repo: Path, tmp_path: Path) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(linear_repo)
    ws.add_remote("origin", str(origin))
    with pytest.raises(GitError):
        ws.push_tag("ghost", "origin")


def test_fetched_annotated_tag_survives_local_export(
    linear_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    target = jj.commit_id(linear_repo, "@-")
    _git(
        linear_repo,
        "-c",
        "user.name=Tag Author",
        "-c",
        "user.email=tag@example.com",
        "tag",
        "-a",
        "incoming",
        target,
        "-m",
        "incoming body",
    )
    _git(
        linear_repo,
        "push",
        str(origin),
        f"{target}:refs/heads/main",
        "refs/tags/incoming",
    )

    destination = tmp_path / "destination"
    destination.mkdir()
    jj.init_colocated(destination)
    (destination / "local.txt").write_text("local\n")
    jj(destination, "describe", "-m", "local")
    _git(destination, "fetch", str(origin), "refs/tags/incoming:refs/tags/incoming")
    ws = pyjutsu.Workspace.load(destination)
    assert ws.git_import() is not None
    original_oid = _git(destination, "rev-parse", "refs/tags/incoming")

    ws.create_tag("local", "@")

    assert _git(destination, "cat-file", "-t", "refs/tags/incoming") == "tag"
    assert _git(destination, "rev-parse", "refs/tags/incoming") == original_oid
    assert "incoming body" in _git(destination, "cat-file", "-p", "refs/tags/incoming")
