"""D6: raw git object access (lane `004/d6`).

Oracle is `git cat-file -t` and `git cat-file -p`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest

from tests.diff.jj_cli import JjCli


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _git_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout


def _object_repo(tmp_path: Path, jj: JjCli) -> Path:
    repo = tmp_path / "objects"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "a.txt").write_text("alpha\n")
    jj(repo, "describe", "-m", "commit A")
    jj(repo, "bookmark", "create", "main", "-r", "@")
    jj(repo, "new")
    jj(repo, "git", "export")
    # An annotated tag object, so the listing covers all four git object kinds.
    _git(repo, "tag", "-a", "v1", "-m", "release one", "main")
    return repo


def test_object_type_matches_cat_file(tmp_path: Path, jj: JjCli) -> None:
    repo = _object_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)

    for rev in ("main", "main^{tree}", "main:a.txt", "refs/tags/v1"):
        oid = _git(repo, "rev-parse", rev).strip()
        assert ws.git.object_type(oid) == _git(repo, "cat-file", "-t", oid).strip()


def test_object_type_covers_all_four_kinds(tmp_path: Path, jj: JjCli) -> None:
    repo = _object_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    kinds = {
        ws.git.object_type(_git(repo, "rev-parse", rev).strip())
        for rev in ("main", "main^{tree}", "main:a.txt", "refs/tags/v1")
    }
    assert kinds == {"commit", "tree", "blob", "tag"}


def test_object_type_is_none_for_an_absent_object(tmp_path: Path, jj: JjCli) -> None:
    repo = _object_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    absent = "0" * len(_git(repo, "rev-parse", "main").strip())
    assert ws.git.object_type(absent) is None
    assert ws.git.exists(absent) is False


def test_exists_matches_cat_file(tmp_path: Path, jj: JjCli) -> None:
    repo = _object_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    oid = _git(repo, "rev-parse", "main").strip()
    assert ws.git.exists(oid) is True


def test_read_blob_matches_cat_file(tmp_path: Path, jj: JjCli) -> None:
    repo = _object_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    oid = _git(repo, "rev-parse", "main:a.txt").strip()
    assert ws.git.read_blob(oid) == _git_bytes(repo, "cat-file", "-p", oid)
    assert ws.git.read_blob(oid) == b"alpha\n"


def test_read_blob_round_trips_binary_content(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "binary"
    repo.mkdir()
    jj.init_colocated(repo)
    payload = bytes(range(256))
    (repo / "bin.dat").write_bytes(payload)
    jj(repo, "describe", "-m", "binary")
    jj(repo, "bookmark", "create", "main", "-r", "@")
    jj(repo, "new")
    jj(repo, "git", "export")

    ws = pyjutsu.Workspace.load(repo)
    oid = _git(repo, "rev-parse", "main:bin.dat").strip()
    assert ws.git.read_blob(oid) == payload


def test_read_blob_refuses_a_non_blob(tmp_path: Path, jj: JjCli) -> None:
    """Deliberately narrow: reading a commit's serialized form must be explicit, not accidental."""
    repo = _object_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    oid = _git(repo, "rev-parse", "main").strip()
    with pytest.raises(pyjutsu.PyjutsuError, match="is a commit, not a blob"):
        ws.git.read_blob(oid)


def test_read_blob_of_an_absent_object_raises(tmp_path: Path, jj: JjCli) -> None:
    repo = _object_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    absent = "0" * len(_git(repo, "rev-parse", "main").strip())
    with pytest.raises(pyjutsu.PyjutsuError, match="no such git object"):
        ws.git.read_blob(absent)


def test_a_malformed_oid_raises_rather_than_reporting_absent(tmp_path: Path, jj: JjCli) -> None:
    """A typo must not look like a missing object."""
    repo = _object_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    for bad in ("", "zz", "abc", "not-hex" * 6):
        with pytest.raises(pyjutsu.PyjutsuError, match="invalid object id"):
            ws.git.object_type(bad)
        with pytest.raises(pyjutsu.PyjutsuError, match="invalid object id"):
            ws.git.exists(bad)


def test_object_reads_publish_no_operation(tmp_path: Path, jj: JjCli) -> None:
    repo = _object_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    oid = _git(repo, "rev-parse", "main:a.txt").strip()
    before = ws.head_operation()
    ws.git.object_type(oid)
    ws.git.exists(oid)
    ws.git.read_blob(oid)
    assert ws.head_operation() == before
