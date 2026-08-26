"""C2: file content and listing (lane `003/c2`).

Oracle is the pinned `jj` CLI: `file_content` must match `jj file show`
byte-for-byte (including binary content), `file_list` must match `jj file list`.
"""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import ConflictError, RevsetError

from tests.diff.jj_cli import JjCli


def _files_repo(tmp_path: Path, jj: JjCli) -> Path:
    """A repo whose `@-` holds a text file, a binary file, and a nested file."""
    repo = tmp_path / "files"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "text.txt").write_text("line one\nline two\n")
    (repo / "blob.bin").write_bytes(b"\x00\x01\xfe\xff\x00")
    (repo / "sub").mkdir()
    (repo / "sub" / "nested.txt").write_text("nested\n")
    jj(repo, "describe", "-m", "files")
    jj(repo, "new")
    return repo


def test_file_content_text_matches_jj(linear_repo: Path, jj: JjCli) -> None:
    view = pyjutsu.Workspace.load(linear_repo).head()
    assert view.file_content("b.txt", "@-") == b"contents of b\n"
    assert view.file_content("b.txt", "@-") == jj(
        linear_repo, "file", "show", "-r", "@-", "b.txt"
    ).encode()


def test_file_content_binary_round_trips(tmp_path: Path, jj: JjCli) -> None:
    repo = _files_repo(tmp_path, jj)
    view = pyjutsu.Workspace.load(repo).head()
    raw = view.file_content("blob.bin", "@-")
    assert raw == b"\x00\x01\xfe\xff\x00"
    # The CLI oracle in binary mode agrees byte-for-byte (the text-mode `jj` helper would mangle it).
    import subprocess

    cli = subprocess.run(
        ["jj", "file", "show", "-r", "@-", "blob.bin"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    assert raw == cli


def test_file_content_absent_path_raises(linear_repo: Path) -> None:
    view = pyjutsu.Workspace.load(linear_repo).head()
    with pytest.raises(pyjutsu.PyjutsuError):
        view.file_content("ghost.txt", "@-")


def test_file_content_conflicted_path_points_at_conflict_content(
    conflict_repo: Path,
) -> None:
    view = pyjutsu.Workspace.load(conflict_repo).head()
    with pytest.raises(ConflictError, match="conflict_content"):
        view.file_content("file.txt", "@")


def test_file_content_requires_single_revision(linear_repo: Path) -> None:
    view = pyjutsu.Workspace.load(linear_repo).head()
    with pytest.raises(RevsetError):
        view.file_content("b.txt", "all()")


def test_file_list_matches_jj(linear_repo: Path, jj: JjCli) -> None:
    view = pyjutsu.Workspace.load(linear_repo).head()
    assert view.file_list("@-") == [
        line for line in jj(linear_repo, "file", "list", "-r", "@-").splitlines() if line
    ]


def test_file_list_prefix_and_glob_filter(tmp_path: Path, jj: JjCli) -> None:
    repo = _files_repo(tmp_path, jj)
    view = pyjutsu.Workspace.load(repo).head()
    assert view.file_list("@-", ["sub"]) == ["sub/nested.txt"]
    # Like the CLI, `glob:*.txt` does not cross directory boundaries.
    assert view.file_list("@-", ["glob:*.txt"]) == ["text.txt"]
    assert view.file_list("@-", ["glob:sub/*.txt"]) == ["sub/nested.txt"]
    assert view.file_list("@-", ["text.txt", "sub"]) == ["sub/nested.txt", "text.txt"]
    # No match → empty, like `jj file list` (a bare name is a path prefix).
    assert view.file_list("@-", ["text"]) == []


def test_file_list_empty_repo(scratch_repo: Path) -> None:
    view = pyjutsu.Workspace.load(scratch_repo).head()
    assert view.file_list("@") == []


def test_file_list_malformed_fileset_raises(linear_repo: Path) -> None:
    view = pyjutsu.Workspace.load(linear_repo).head()
    with pytest.raises(pyjutsu.PyjutsuError):
        view.file_list("@-", ["exact:not-a-kind"])
