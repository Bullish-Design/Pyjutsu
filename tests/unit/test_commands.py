# tests/unit/test_commands.py
"""Test command wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu._commands import JjCommand, check_jj_installed
from pyjutsu.exceptions import JjNotFoundError


def test_check_jj_installed() -> None:
    """check_jj_installed succeeds or raises JjNotFoundError."""
    try:
        check_jj_installed()
    except JjNotFoundError:
        pytest.skip("jj not installed in test environment")


def test_jj_command_initializes_with_repo(tmp_path: Path) -> None:
    """JjCommand stores repo_path when jj is available."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    try:
        cmd = JjCommand(repo_path)
    except JjNotFoundError:
        pytest.skip("jj not installed in test environment")
    assert cmd.repo_path == repo_path


def test_run_lines_returns_lines(tmp_path: Path) -> None:
    """run_lines returns non-empty trimmed lines for --version."""
    repo_path = tmp_path
    try:
        cmd = JjCommand(repo_path)
    except JjNotFoundError:
        pytest.skip("jj not installed in test environment")
    lines = cmd.run_lines("--version")
    assert isinstance(lines, list)
    assert all(isinstance(line, str) for line in lines)
    # If jj exists, we expect at least one non-empty line
    if lines:
        assert all(line.strip() for line in lines)
