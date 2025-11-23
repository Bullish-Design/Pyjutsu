# tests/integration/test_status.py
"""Integration tests for status command."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient, JjNotFoundError


def test_status_on_new_repo(tmp_path: Path) -> None:
    """Status works on newly initialized repository."""
    repo_path = tmp_path / "test-repo"
    try:
        client = JjClient.init(repo_path)
    except JjNotFoundError:
        pytest.skip("jj not installed in test environment")

    status = client.status()
    assert status.working_copy_change_id
    assert len(status.working_copy_change_id) >= 12
    assert not status.has_conflicts
    assert status.modified_files == []


def test_status_detects_new_file(tmp_path: Path) -> None:
    """Status detects newly created files."""
    repo_path = tmp_path / "test-repo"
    try:
        client = JjClient.init(repo_path)
    except JjNotFoundError:
        pytest.skip("jj not installed in test environment")

    # Create a new file
    test_file = repo_path / "test.txt"
    test_file.write_text("Hello, world!")

    status = client.status()
    assert len(status.modified_files) >= 1
    # Find our test file
    test_changes = [f for f in status.modified_files if f.path.name == "test.txt"]
    assert len(test_changes) == 1
