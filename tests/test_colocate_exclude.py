"""Colocate writes ``/.jj/`` to ``.git/info/exclude`` (project 14 §P5).

After ``Workspace.init(colocate=True)`` jj's metadata dir must be invisible to git, and re-colocating
must not duplicate the exclude line.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu


def _exclude_lines(repo: Path) -> list[str]:
    return (repo / ".git" / "info" / "exclude").read_text().splitlines()


def test_colocate_excludes_jj_and_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "fresh"
    repo.mkdir()
    pyjutsu.Workspace.init(repo, colocate=True)

    lines = _exclude_lines(repo)
    assert "/.jj/" in lines
    # git must not see .jj/ as untracked.
    porcelain = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert ".jj/" not in porcelain

    assert lines.count("/.jj/") == 1


def test_colocate_adopt_does_not_duplicate_existing_exclude(tmp_path: Path) -> None:
    # Adopt an existing .git whose info/exclude already lists /.jj/ (the re-colocate case): the line
    # must not be duplicated, and pre-existing exclude entries must be preserved.
    repo = tmp_path / "adopt"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    info = repo / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "exclude").write_text("*.tmp\n/.jj/\n")

    pyjutsu.Workspace.init(repo, colocate=True)
    lines = _exclude_lines(repo)
    assert lines.count("/.jj/") == 1  # not duplicated
    assert "*.tmp" in lines  # existing entries preserved
