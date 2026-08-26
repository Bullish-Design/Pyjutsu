"""Annotated tag read/write under the `ws.git` namespace (lane D2).

Oracle is the `git` binary. `ws.git.create_tag` writes a real annotated tag
object; `ws.git.tag`/`ws.git.tags` read tags back, annotated or lightweight,
whether created here, by raw `git tag`, or fetched from a remote.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import GitError

from tests.diff.jj_cli import JjCli, init_bare_remote


def _git(d: Path, *a: str) -> str:
    return subprocess.run(
        ["git", "-C", str(d), *a], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_tag_reads_back_annotated_tag_created_by_git(linear_repo: Path, jj: JjCli) -> None:
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

    ws = pyjutsu.Workspace.load(linear_repo)
    tag = ws.git.tag("incoming")
    assert tag is not None
    assert tag.name == "incoming"
    assert tag.target == target  # fully peeled to the commit
    assert tag.annotated is True
    assert tag.message == "incoming body"
    assert tag.tagger is not None
    assert tag.tagger.name == "Tag Author"
    assert tag.tagger.email == "tag@example.com"
    assert tag.date is not None
    assert tag.date.tzinfo is not None

    rows = {t.name: t for t in ws.git.tags()}
    assert rows["incoming"] == tag


def test_tag_reads_back_lightweight_tag(linear_repo: Path, jj: JjCli) -> None:
    target = jj.commit_id(linear_repo, "@-")
    _git(linear_repo, "tag", "light", target)

    ws = pyjutsu.Workspace.load(linear_repo)
    tag = ws.git.tag("light")
    assert tag is not None
    assert tag.target == target
    assert tag.annotated is False
    assert tag.message is None
    assert tag.tagger is None
    assert tag.date is None


def test_tag_absent_returns_none(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    assert ws.git.tag("ghost") is None
    assert ws.git.tags() == []


def test_git_create_tag_writes_annotated_object(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    target = jj.commit_id(linear_repo, "@-")

    op = ws.git.create_tag("v1.0", "@-", "release one")

    assert op is not None
    assert _git(linear_repo, "cat-file", "-t", "refs/tags/v1.0") == "tag"
    assert _git(linear_repo, "rev-parse", "v1.0^{commit}") == target
    body = _git(linear_repo, "cat-file", "-p", "refs/tags/v1.0")
    assert "release one" in body
    assert "\ntagger " in body

    tag = ws.git.tag("v1.0")
    assert tag is not None
    assert tag.annotated is True
    assert tag.target == target
    assert tag.message == "release one"


def test_git_create_tag_duplicate_requires_force(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    ws.git.create_tag("dup", "@-", "first")
    with pytest.raises(GitError):
        ws.git.create_tag("dup", "@-", "second")
    ws.git.create_tag("dup", "@-", "second", force=True)
    assert _git(linear_repo, "cat-file", "-t", "refs/tags/dup") == "tag"


def test_tag_reads_back_fetched_from_remote(
    linear_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    origin = tmp_path / "origin.git"
    init_bare_remote(origin)
    target = jj.commit_id(linear_repo, "@-")
    _git(
        linear_repo,
        "-c",
        "user.name=Remote Author",
        "-c",
        "user.email=remote@example.com",
        "tag",
        "-a",
        "from-remote",
        target,
        "-m",
        "remote body",
    )
    _git(linear_repo, "push", str(origin), "refs/tags/from-remote")

    destination = tmp_path / "destination"
    destination.mkdir()
    jj.init_colocated(destination)
    (destination / "local.txt").write_text("local\n")
    jj(destination, "describe", "-m", "local")
    _git(destination, "fetch", str(origin), "refs/tags/from-remote:refs/tags/from-remote")

    ws = pyjutsu.Workspace.load(destination)
    tag = ws.git.tag("from-remote")
    assert tag is not None
    assert tag.annotated is True
    assert tag.message == "remote body"
    assert tag.tagger is not None
    assert tag.tagger.name == "Remote Author"
    assert tag.target == target


def test_workspace_create_tag_delegates_to_git_namespace(linear_repo: Path) -> None:
    """`Workspace.create_tag(message=...)` still works and lands in `ws.git`."""
    import warnings

    ws = pyjutsu.Workspace.load(linear_repo)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        op = ws.create_tag("delegated", "@-", "via alias")
    assert op is not None
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    tag = ws.git.tag("delegated")
    assert tag is not None
    assert tag.annotated is True
    assert tag.message == "via alias"
