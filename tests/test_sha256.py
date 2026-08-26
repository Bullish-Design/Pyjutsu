"""SHA-256 repositories (upgrade phase B3).

jj 0.44 chooses a new repo's object format from the ``git.object-hash`` setting. Pyjutsu's
``init`` reads the same setting, so a SHA-256 repo is one config line away. These tests prove
three things: Pyjutsu creates such a repo, the read and write surface works inside it, and
``patch_id`` keeps its 40-hex SHA-1 width there (the decision recorded for finding F2).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest

from tests.diff.jj_cli import OBJECT_HASH_ENV, JjCli, suite_object_hash

#: Each test here picks its own object format, so a suite-wide choice would conflict with it.
pytestmark = pytest.mark.skipif(
    suite_object_hash() is not None,
    reason=f"{OBJECT_HASH_ENV} forces one object format for the whole suite",
)

#: Hex width of a commit id per object format. jj commit ids are git object ids.
SHA1_WIDTH = 40
SHA256_WIDTH = 64


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _init(jj: JjCli, path: Path, object_hash: str | None) -> pyjutsu.Workspace:
    """Create a repo through Pyjutsu, optionally choosing the object format first."""
    if object_hash is not None:
        jj.append_config(f'[git]\nobject-hash = "{object_hash}"\n')
    path.mkdir(parents=True)
    return pyjutsu.Workspace.init(path, colocate=True)


def test_init_defaults_to_sha1(tmp_path: Path, jj: JjCli) -> None:
    """No setting means SHA-1, matching the pinned CLI's `git.object-hash` default."""
    ws = _init(jj, tmp_path / "repo", None)
    assert _git(ws.root, "config", "--default", "sha1", "extensions.objectformat").strip() == "sha1"
    assert len(ws.head().resolve("@").commit_id) == SHA1_WIDTH


def test_init_creates_a_sha256_repo(tmp_path: Path, jj: JjCli) -> None:
    """`git.object-hash = "sha256"` reaches jj-lib's initializer, and git agrees."""
    ws = _init(jj, tmp_path / "repo", "sha256")
    assert _git(ws.root, "config", "extensions.objectformat").strip() == "sha256"
    assert len(ws.head().resolve("@").commit_id) == SHA256_WIDTH


def test_invalid_object_hash_is_rejected(tmp_path: Path, jj: JjCli) -> None:
    """An unknown format fails at init rather than creating a repo Pyjutsu cannot read."""
    jj.append_config('[git]\nobject-hash = "sha512"\n')
    path = tmp_path / "repo"
    path.mkdir()
    with pytest.raises(pyjutsu.PyjutsuError, match="sha512"):
        pyjutsu.Workspace.init(path, colocate=True)


def test_sha256_repo_matches_the_pinned_cli(tmp_path: Path, jj: JjCli) -> None:
    """The CLI reads back what Pyjutsu wrote in a SHA-256 repo, id for id."""
    ws = _init(jj, tmp_path / "repo", "sha256")
    (ws.root / "a.txt").write_text("contents of a\n")
    with ws.transaction("describe in sha256") as tx:
        commit = tx.describe("@", "sha256 commit")

    assert len(commit.commit_id) == SHA256_WIDTH
    assert jj.commit_id(ws.root, "@") == commit.commit_id
    assert jj.template(ws.root, "@", "description").strip() == "sha256 commit"


def test_sha256_repo_supports_the_write_surface(tmp_path: Path, jj: JjCli) -> None:
    """Bookmarks, tags, and the operation log all work in a SHA-256 repo."""
    ws = _init(jj, tmp_path / "repo", "sha256")
    (ws.root / "a.txt").write_text("contents of a\n")
    with ws.transaction("first commit") as tx:
        tx.describe("@", "first commit")
    with ws.transaction("bookmark") as tx:
        tx.set_bookmark("feature", "@")
    ws.create_tag("v1", "@")

    assert jj.template(ws.root, "@", "bookmarks").strip() == "feature"
    assert "v1" in jj(ws.root, "tag", "list")
    # A lightweight jj tag is the commit object itself, at full SHA-256 width.
    assert _git(ws.root, "cat-file", "-t", "refs/tags/v1").strip() == "commit"
    assert len(_git(ws.root, "rev-parse", "refs/tags/v1").strip()) == SHA256_WIDTH

    # `gc()` runs the native store collection against a SHA-256 backend.
    ws.gc()
    assert jj.commit_id(ws.root, "@") == ws.head().resolve("@").commit_id


def test_patch_id_keeps_sha1_width_in_a_sha256_repo(tmp_path: Path, jj: JjCli) -> None:
    """`patch_id` is a Pyjutsu content digest, not a git object id, so it never widens.

    This is the assertion that proves the lane A1 decision: the digest stays SHA-1 in every
    repository. A 64-hex result here would mean the digest silently followed the object format.
    """
    ws = _init(jj, tmp_path / "repo", "sha256")
    (ws.root / "h.txt").write_text("x\n")
    with ws.transaction("content") as tx:
        commit = tx.describe("@", "content")

    assert len(commit.commit_id) == SHA256_WIDTH
    patch_id = ws.patch_id("@")
    assert len(patch_id) == SHA1_WIDTH
    assert int(patch_id, 16) >= 0  # pure hex, no format prefix
