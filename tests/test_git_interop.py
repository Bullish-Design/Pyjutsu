"""Slice 10: git interop — `git_import` / `git_export` + remotes CRUD + `remotes()`.

These are `Workspace`-level verbs (not `Transaction` methods) that sync jj's view with the backing
git repo and manage git remotes. Differential against the pinned `jj` CLI on a `_copy_repo` sibling.

The headline asymmetry: `add_remote`/`remove_remote`/`rename_remote` are `MutableRepo` mutations and
each publish **one op**, but `set_remote_url` is a pure git-config write through `&Store` — it
publishes **no jj operation** (so op count is unchanged on the binding side).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import GitError

from tests.diff.jj_cli import JjCli


def _copy_repo(src: Path, dst: Path) -> Path:
    """A byte-identical sibling repo (same change ids, commit ids, and op log)."""
    shutil.copytree(src, dst)
    return dst


def _op_count(repo: Path, jj: JjCli) -> int:
    return len(jj.op_log_ids(repo))


# --- remotes() (read) -------------------------------------------------------------------------


def test_remotes_lists_origin(bookmarked_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    rows = {r.name: r for r in ws.remotes()}

    # The fixture configures exactly one remote, `origin`, and the binding agrees with the CLI on
    # both the name set and the fetch url.
    assert set(rows) == {"origin"}
    cli = jj.remotes(bookmarked_repo)
    assert set(rows) == set(cli)
    assert rows["origin"].url == cli["origin"]


def test_remotes_empty_when_none(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    assert ws.remotes() == []


# --- add / remove / rename (one op each) ------------------------------------------------------


def test_add_remote_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    url = "https://example.com/upstream.git"
    ws = pyjutsu.Workspace.load(scratch_repo)
    ops_before = _op_count(scratch_repo, jj)
    cli_ops_before = _op_count(other, jj)

    ws.add_remote("upstream", url)
    jj(other, "git", "remote", "add", "upstream", url)

    # `upstream` appears with the same url on both sides.
    rows = {r.name: r.url for r in ws.remotes()}
    assert rows.get("upstream") == url
    assert jj.remotes(other).get("upstream") == url

    # One new op each.
    assert _op_count(scratch_repo, jj) == ops_before + 1
    assert _op_count(other, jj) == cli_ops_before + 1


def test_add_duplicate_remote_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_remote("origin", "https://example.com/a.git")
    with pytest.raises(GitError):
        ws.add_remote("origin", "https://example.com/b.git")


def test_remove_remote_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    url = "https://example.com/r.git"
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_remote("origin", url)
    jj(other, "git", "remote", "add", "origin", url)

    ops_before = _op_count(scratch_repo, jj)
    cli_ops_before = _op_count(other, jj)

    ws.remove_remote("origin")
    jj(other, "git", "remote", "remove", "origin")

    # Gone on both sides; one new op each.
    assert "origin" not in {r.name for r in ws.remotes()}
    assert "origin" not in jj.remotes(other)
    assert _op_count(scratch_repo, jj) == ops_before + 1
    assert _op_count(other, jj) == cli_ops_before + 1


def test_remove_unknown_remote_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(GitError):
        ws.remove_remote("nope")


def test_rename_remote_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    url = "https://example.com/r.git"
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_remote("origin", url)
    jj(other, "git", "remote", "add", "origin", url)

    ops_before = _op_count(scratch_repo, jj)
    cli_ops_before = _op_count(other, jj)

    ws.rename_remote("origin", "upstream")
    jj(other, "git", "remote", "rename", "origin", "upstream")

    rows = {r.name: r.url for r in ws.remotes()}
    assert "origin" not in rows
    assert rows.get("upstream") == url
    cli = jj.remotes(other)
    assert "origin" not in cli
    assert cli.get("upstream") == url
    assert _op_count(scratch_repo, jj) == ops_before + 1
    assert _op_count(other, jj) == cli_ops_before + 1


def test_rename_unknown_remote_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(GitError):
        ws.rename_remote("nope", "upstream")


# --- set_remote_url (the asymmetry: NO op) ----------------------------------------------------


def test_set_remote_url_no_op(scratch_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_remote("origin", "https://example.com/old.git")
    ops_before = _op_count(scratch_repo, jj)

    new_url = "https://example.com/new.git"
    ws.set_remote_url("origin", new_url)

    # The url changed (binding + CLI agree)...
    assert {r.name: r.url for r in ws.remotes()}["origin"] == new_url
    assert jj.remotes(scratch_repo)["origin"] == new_url
    # ...but `set_remote_urls` is a pure git-config write through `&Store`: no jj operation.
    assert _op_count(scratch_repo, jj) == ops_before


def test_set_remote_url_unknown_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(GitError):
        ws.set_remote_url("nope", "https://example.com/x.git")


# --- git_export / git_import (headline) -------------------------------------------------------


def test_git_export_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")

    # Create a bookmark on each side (binding via a transaction; CLI via `jj bookmark create`).
    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("create bookmark") as tx:
        tx.create_bookmark("feature", "@")
    jj(other, "bookmark", "create", "feature", "-r", "@")

    op = ws.git_export()
    jj(other, "git", "export")

    # The export publishes an operation, and the git ref now exists in the colocated `.git` on both
    # sides (read straight from git, the common oracle, to dodge the colocated jj-read trap).
    assert op is not None
    assert "export" in op.description.lower()
    assert _git_has_ref(scratch_repo, "refs/heads/feature")
    assert _git_has_ref(other, "refs/heads/feature")


def test_git_export_noop_returns_none(bookmarked_repo: Path, jj: JjCli) -> None:
    # The fixture already pushed `feature`, so its git ref is already exported. A first export may
    # still have nothing to do; a second definitely does. Assert the no-op contract: None + no op.
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    ws.git_export()
    ops_before = _op_count(bookmarked_repo, jj)
    assert ws.git_export() is None
    assert _op_count(bookmarked_repo, jj) == ops_before


def test_git_import_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    head = jj.commit_id(scratch_repo, "@")  # a real git commit to point a new git branch at

    # Make an out-of-band git change on each side: a new branch directly in the colocated `.git`.
    _git(scratch_repo, "branch", "newbranch", head)
    _git(other, "branch", "newbranch", head)

    ws = pyjutsu.Workspace.load(scratch_repo)
    op = ws.git_import()
    jj(other, "git", "import")

    # The import publishes an operation, and jj picks up `newbranch` as a bookmark on both sides.
    assert op is not None
    assert "import" in op.description.lower()
    assert "newbranch" in {b.name for b in ws.bookmarks()}
    assert "newbranch" in {row[0] for row in jj.bookmarks(other)}


def test_git_import_noop_returns_none(scratch_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.git_import()  # absorb any first-import effect
    ops_before = _op_count(scratch_repo, jj)
    assert ws.git_import() is None
    assert _op_count(scratch_repo, jj) == ops_before


# --- helpers ----------------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _git_has_ref(repo: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", ref], cwd=repo, capture_output=True
    )
    return result.returncode == 0
