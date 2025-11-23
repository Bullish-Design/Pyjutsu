# tests/integration/test_log.py
"""Integration tests for log command."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient


def test_log_returns_entries(tmp_path: Path) -> None:
    """Log returns commit entries with descriptions."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    # Create some commits
    test_file = repo_path / "test.txt"
    test_file.write_text("v1")
    client.describe("First commit")
    client.new()

    test_file.write_text("v2")
    client.describe("Second commit")

    entries = client.log(limit=5)
    print(f"Log entries: [{type(entries)}] {entries}")
    count = 0
    for entry in entries:
        count += 1
        print(f"Entry {count}: {entry.change.description}")
    assert len(entries) >= 2
    descriptions = [entry.change.description for entry in entries]
    assert any("First commit" in d for d in descriptions)
    assert any("Second commit" in d for d in descriptions)


def test_log_respects_limit(tmp_path: Path) -> None:
    """log(limit=...) returns at most that many entries."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    # Create several commits
    test_file = repo_path / "test.txt"
    for i in range(5):
        test_file.write_text(f"version {i}")
        client.describe(f"Commit {i}")
        client.new()

    entries = client.log(limit=3)
    assert len(entries) <= 3
