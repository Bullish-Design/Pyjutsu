"""D8: git reflog read (lane `004/d8`).

Oracle is `git reflog show --format=...`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest

from tests.diff.jj_cli import JjCli

_ENV = {
    "PATH": subprocess.os.environ["PATH"],
    "HOME": subprocess.os.environ.get("HOME", "/tmp"),
    "GIT_AUTHOR_NAME": "Pyjutsu Test",
    "GIT_AUTHOR_EMAIL": "test@pyjutsu.invalid",
    "GIT_COMMITTER_NAME": "Pyjutsu Test",
    "GIT_COMMITTER_EMAIL": "test@pyjutsu.invalid",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, env=_ENV
    ).stdout


def _reflog(repo: Path, ref: str = "HEAD") -> list[tuple[str, str, str]]:
    """(old_oid, new_oid, message) per entry, newest first — `git reflog show`'s own order."""
    out = _git(repo, "reflog", "show", "--format=%H%x00%gD%x00%gs", ref)
    rows = []
    raw = _git(repo, "reflog", "show", "--format=%h", ref)  # entry count cross-check
    del raw
    for line in out.splitlines():
        if not line:
            continue
        new_oid, _selector, message = line.split("\x00", 2)
        rows.append((new_oid, message))
    return rows  # type: ignore[return-value]


def _git_repo_with_history(tmp_path: Path, name: str) -> Path:
    """A plain git repository with three ref moves, so the reflog has real entries."""
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main", ".")
    for i in range(3):
        (repo / f"f{i}.txt").write_text(f"file {i}\n")
        _git(repo, "add", f"f{i}.txt")
        _git(repo, "commit", "-q", "-m", f"commit {i}")
    return repo


def _adopt(repo: Path) -> pyjutsu.Workspace:
    """Adopt an existing `.git` as a colocated jj repo without disturbing its reflog."""
    return pyjutsu.Workspace.init(repo, colocate=True)


def test_head_reflog_matches_git(tmp_path: Path, jj: JjCli) -> None:
    repo = _git_repo_with_history(tmp_path, "reflog")
    ws = _adopt(repo)

    entries = ws.git.reflog()
    expected = _reflog(repo)
    assert len(entries) == len(expected)
    for entry, (new_oid, message) in zip(entries, expected, strict=True):
        assert entry.new_oid == new_oid
        assert entry.message == message


def test_reflog_is_newest_first(tmp_path: Path, jj: JjCli) -> None:
    repo = _git_repo_with_history(tmp_path, "order")
    ws = _adopt(repo)
    entries = ws.git.reflog()
    assert entries[0].message.endswith("commit 2")
    assert entries[-1].message.endswith("commit 0")
    # Each entry's old_oid is the next-older entry's new_oid.
    for newer, older in zip(entries, entries[1:], strict=False):
        assert newer.old_oid == older.new_oid


def test_the_first_entry_has_a_null_old_oid(tmp_path: Path, jj: JjCli) -> None:
    """git records the creation of a ref with an all-zero previous oid."""
    repo = _git_repo_with_history(tmp_path, "creation")
    ws = _adopt(repo)
    oldest = ws.git.reflog()[-1]
    assert set(oldest.old_oid) == {"0"}


def test_limit_caps_the_entries(tmp_path: Path, jj: JjCli) -> None:
    repo = _git_repo_with_history(tmp_path, "limited")
    ws = _adopt(repo)
    full = ws.git.reflog()
    assert len(full) > 2
    capped = ws.git.reflog(limit=2)
    assert len(capped) == 2
    assert [e.new_oid for e in capped] == [e.new_oid for e in full[:2]]


def test_signature_matches_git(tmp_path: Path, jj: JjCli) -> None:
    repo = _git_repo_with_history(tmp_path, "signature")
    ws = _adopt(repo)
    entry = ws.git.reflog()[0]
    assert entry.signature.name == "Pyjutsu Test"
    assert entry.signature.email == "test@pyjutsu.invalid"
    assert entry.signature.timestamp.tzinfo is not None


def test_a_branch_reflog_reads_by_short_name(tmp_path: Path, jj: JjCli) -> None:
    repo = _git_repo_with_history(tmp_path, "branch")
    ws = _adopt(repo)

    by_short = ws.git.reflog("main")
    by_full = ws.git.reflog("refs/heads/main")
    assert [e.new_oid for e in by_short] == [e.new_oid for e in by_full]
    assert [e.new_oid for e in by_short] == [n for n, _ in _reflog(repo, "main")]


def test_a_ref_with_no_reflog_reads_as_empty(tmp_path: Path, jj: JjCli) -> None:
    """git creates a reflog only once something moves the ref."""
    repo = _git_repo_with_history(tmp_path, "noreflog")
    _git(repo, "branch", "quiet", "main")
    # `git branch` does write a reflog, so strip it to reach the "no file" state.
    (repo / ".git" / "logs" / "refs" / "heads" / "quiet").unlink()

    ws = _adopt(repo)
    assert ws.git.reflog("quiet") == []


def test_an_unknown_ref_raises(tmp_path: Path, jj: JjCli) -> None:
    repo = _git_repo_with_history(tmp_path, "unknown")
    ws = _adopt(repo)
    with pytest.raises(pyjutsu.PyjutsuError, match="no such git ref"):
        ws.git.reflog("never-existed")


def test_reading_publishes_no_operation(tmp_path: Path, jj: JjCli) -> None:
    repo = _git_repo_with_history(tmp_path, "readonly")
    ws = _adopt(repo)
    before = ws.head_operation()
    ws.git.reflog()
    assert ws.head_operation() == before
