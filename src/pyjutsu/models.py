# src/pyjutsu/models.py
"""Pydantic models for Pyjutsu."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from pyjutsu.enums import BranchTrackingStatus, ChangeState, FileStatus


class FileChange(BaseModel):
    """Represents a file change in the working copy or a commit."""

    path: Path
    status: FileStatus
    old_path: Path | None = None

    def __str__(self) -> str:
        """String representation."""
        if self.old_path:
            return f"{self.status.value} {self.old_path} -> {self.path}"
        return f"{self.status.value} {self.path}"


class Change(BaseModel):
    """Represents a jj change (commit)."""

    change_id: str = Field(description="Jujutsu's unique change ID")
    commit_id: str = Field(description="Git-compatible commit hash")
    description: str = Field(description="Commit message")
    author: str = Field(description="Author name <email>")
    timestamp: datetime
    parent_ids: list[str] = Field(default_factory=list)
    state: ChangeState = ChangeState.MUTABLE

    def __str__(self) -> str:
        """String representation."""
        short_id = self.change_id[:12]
        first_line = self.description.split("\n")[0][:50]
        return f"{short_id} {first_line}"


class Branch(BaseModel):
    """Represents a jj branch."""

    name: str
    target_change_id: str = Field(description="Change ID this branch points to")
    target_commit_id: str = Field(description="Commit hash this branch points to")
    tracking_status: BranchTrackingStatus = BranchTrackingStatus.UNTRACKED
    remote_name: str | None = None

    def __str__(self) -> str:
        """String representation."""
        return f"{self.name} -> {self.target_change_id[:12]}"


class WorkspaceStatus(BaseModel):
    """Represents the current workspace status."""

    working_copy_change_id: str
    current_branch: str | None = None
    has_conflicts: bool = False
    modified_files: list[FileChange] = Field(default_factory=list)
    is_colocated: bool = True

    def __str__(self) -> str:
        """String representation."""
        branch_str = f" on {self.current_branch}" if self.current_branch else ""
        files_str = (
            f" ({len(self.modified_files)} files modified)" if self.modified_files else ""
        )
        return f"{self.working_copy_change_id[:12]}{branch_str}{files_str}"


class LogEntry(BaseModel):
    """Single entry from jj log."""

    change: Change
    branches: list[str] = Field(
        default_factory=list,
        description="Branches pointing to this change",
    )
    is_working_copy: bool = False

    def __str__(self) -> str:
        """String representation."""
        branches_str = f" ({', '.join(self.branches)})" if self.branches else ""
        wc_str = " @" if self.is_working_copy else ""
        return f"{self.change}{branches_str}{wc_str}"


class DiffSummary(BaseModel):
    """Summary of differences between revisions."""

    from_revision: str
    to_revision: str
    files_changed: list[FileChange]
    insertions: int = 0
    deletions: int = 0

    def __str__(self) -> str:
        """String representation."""
        return f"{len(self.files_changed)} files changed, +{self.insertions} -{self.deletions}"
