# tests/integration/test_describe_new.py
"""Integration tests for describe and new commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient


def test_describe_sets_message(tmp_path: Path) -> None:
    """Describe sets commit message on working copy."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    # Create a file so there's something to describe
    test_file = repo_path / "test.txt"
    test_file.write_text("content")

    client.describe("Initial commit")

    # Verify message was set via jj log
    output = client._cmd.run("log", "-r", "@", "--no-graph", "-T", "description")
    assert "Initial commit" in output


def test_new_creates_new_working_copy_change(tmp_path: Path) -> None:
    """New creates a new working copy change with different change_id."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    old_status = client.status()
    old_change_id = old_status.working_copy_change_id

    new_change_id = client.new()

    assert new_change_id != old_change_id

    new_status = client.status()
    assert new_status.working_copy_change_id == new_change_id
