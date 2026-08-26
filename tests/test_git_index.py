"""D9: git index read (lane `004/d9`), read-only.

Oracle is `git ls-files --stage`, whose lines are ``<mode> <oid> <stage>\\t<path>``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu

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


def _ls_files(repo: Path) -> list[tuple[int, str, int, str]]:
    """(mode, oid, stage, path) per index entry, in git's order."""
    rows = []
    for line in _git(repo, "ls-files", "--stage").splitlines():
        if not line:
            continue
        meta, path = line.split("\t", 1)
        mode, oid, stage = meta.split()
        rows.append((int(mode, 8), oid, int(stage), path))
    return rows


def _staged_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main", ".")
    (repo / "a.txt").write_text("alpha\n")
    (repo / "sub").mkdir()
    (repo / "sub" / "b.txt").write_text("beta\n")
    script = repo / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    (repo / "link").symlink_to("a.txt")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_index_entries_match_git_ls_files(tmp_path: Path, jj: JjCli) -> None:
    repo = _staged_repo(tmp_path, "index")
    ws = pyjutsu.Workspace.init(repo, colocate=True)

    entries = ws.git.index_entries()
    expected = _ls_files(repo)
    assert len(entries) == len(expected)
    assert [(e.mode, e.oid, e.stage, e.path) for e in entries] == expected


def test_modes_cover_file_executable_and_symlink(tmp_path: Path, jj: JjCli) -> None:
    repo = _staged_repo(tmp_path, "modes")
    ws = pyjutsu.Workspace.init(repo, colocate=True)
    by_path = {e.path: e for e in ws.git.index_entries()}

    assert by_path["a.txt"].mode == 0o100644
    assert by_path["run.sh"].mode == 0o100755
    assert by_path["link"].mode == 0o120000
    assert all(e.stage == 0 for e in by_path.values())


def test_entries_are_in_git_order(tmp_path: Path, jj: JjCli) -> None:
    repo = _staged_repo(tmp_path, "order")
    ws = pyjutsu.Workspace.init(repo, colocate=True)
    paths = [e.path for e in ws.git.index_entries()]
    assert paths == [row[3] for row in _ls_files(repo)]


def test_conflict_stages_are_reported(tmp_path: Path, jj: JjCli) -> None:
    """A git merge conflict puts stages 1/2/3 in the index; the read shows all three."""
    repo = tmp_path / "conflicted"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main", ".")
    (repo / "c.txt").write_text("base\n")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "c.txt").write_text("theirs\n")
    _git(repo, "commit", "-q", "-am", "theirs")
    _git(repo, "checkout", "-q", "main")
    (repo / "c.txt").write_text("ours\n")
    _git(repo, "commit", "-q", "-am", "ours")
    merge = subprocess.run(["git", "merge", "side"], cwd=repo, capture_output=True, env=_ENV)
    assert merge.returncode != 0  # the conflict is the point

    ws = pyjutsu.Workspace.init(repo, colocate=True)
    stages = sorted(e.stage for e in ws.git.index_entries() if e.path == "c.txt")
    assert stages == [1, 2, 3]
    assert stages == sorted(row[2] for row in _ls_files(repo) if row[3] == "c.txt")


def test_reading_publishes_no_operation(tmp_path: Path, jj: JjCli) -> None:
    repo = _staged_repo(tmp_path, "readonly")
    ws = pyjutsu.Workspace.init(repo, colocate=True)
    before = ws.head_operation()
    ws.git.index_entries()
    assert ws.head_operation() == before
