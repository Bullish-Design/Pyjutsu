# tests/unit/test_models.py
"""Test Pydantic models."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from pyjutsu import Branch, Change, FileChange, FileStatus, WorkspaceStatus


def test_file_change_creation() -> None:
    """FileChange model validates correctly."""
    fc = FileChange(path=Path("src/main.py"), status=FileStatus.MODIFIED)
    assert fc.path == Path("src/main.py")
    assert fc.status == FileStatus.MODIFIED
    assert fc.old_path is None


def test_file_change_rename() -> None:
    """FileChange handles renames."""
    fc = FileChange(
        path=Path("new.py"),
        status=FileStatus.RENAMED,
        old_path=Path("old.py"),
    )
    assert fc.old_path == Path("old.py")
    assert "old.py -> new.py" in str(fc)


def test_change_creation() -> None:
    """Change model validates correctly."""
    change = Change(
        change_id="abc123def456",
        commit_id="0" * 40,
        description="Initial commit",
        author="Test User <test@example.com>",
        timestamp=datetime.now(),
        parent_ids=[],
    )
    assert len(change.change_id) == 12
    assert "Initial commit" in change.description


def test_branch_creation() -> None:
    """Branch model validates correctly."""
    branch = Branch(
        name="main",
        target_change_id="abc123",
        target_commit_id="0" * 40,
    )
    assert branch.name == "main"
    assert branch.remote_name is None


def test_workspace_status_creation() -> None:
    """WorkspaceStatus model validates correctly."""
    status = WorkspaceStatus(
        working_copy_change_id="abc123",
        current_branch="main",
        modified_files=[
            FileChange(path=Path("test.py"), status=FileStatus.MODIFIED),
        ],
    )
    assert status.current_branch == "main"
    assert len(status.modified_files) == 1
    assert not status.has_conflicts
