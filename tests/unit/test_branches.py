# tests/integration/test_branches.py
"""Integration tests for branch operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient


def test_branch_create(tmp_path: Path) -> None:
    """Can create a new branch pointing at current change."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    branch = client.branch_create("feature-x")
    assert branch.name == "feature-x"
    assert branch.target_change_id
    assert branch.target_commit_id


def test_branch_list_includes_created_branches(tmp_path: Path) -> None:
    """Listing branches returns created branches by name."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    client.branch_create("main")
    client.branch_create("dev")

    branches = client.branch_list()
    names = {b.name for b in branches}

    assert "main" in names
    assert "dev" in names


def test_branch_delete_removes_branch(tmp_path: Path) -> None:
    """Deleting a branch removes it from branch_list."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    client.branch_create("temp")

    # Ensure it exists
    names_before = {b.name for b in client.branch_list()}
    assert "temp" in names_before

    client.branch_delete("temp")

    names_after = {b.name for b in client.branch_list()}
    assert "temp" not in names_after


def test_branch_set_moves_branch(tmp_path: Path) -> None:
    """branch_set moves an existing branch to a new *change* (not just a new commit)."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    # Create initial branch at current change
    branch = client.branch_create("move-me")
    original_change = branch.target_change_id

    # Create a *new change* (jj new), then move the branch there
    test_file = repo_path / "test.txt"
    test_file.write_text("content")
    client.describe("Some change")
    client.new()  # this creates a distinct change with a new change_id
    new_change = client.status().working_copy_change_id

    client.branch_set("move-me", revision="@")  # move to current change

    updated_branches = {b.name: b for b in client.branch_list()}
    assert "move-me" in updated_branches
    assert updated_branches["move-me"].target_change_id == new_change
    assert updated_branches["move-me"].target_change_id != original_change

