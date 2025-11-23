# src/pyjutsu/__init__.py
"""Pyjutsu - Pythonic wrapper for Jujutsu VCS."""

from __future__ import annotations

from pyjutsu.client import JjClient
from pyjutsu.enums import BranchTrackingStatus, ChangeState, FileStatus
from pyjutsu.exceptions import (
    ConflictError,
    InvalidRevisionError,
    JjCommandError,
    JjNotFoundError,
    PyjutsuError,
    RepositoryNotFoundError,
)
from pyjutsu.models import Branch, Change, DiffSummary, FileChange, LogEntry, WorkspaceStatus

__version__ = "0.1.0"

__all__ = [
    "Branch",
    "BranchTrackingStatus",
    "Change",
    "ChangeState",
    "ConflictError",
    "DiffSummary",
    "FileChange",
    "FileStatus",
    "InvalidRevisionError",
    "JjClient",
    "JjCommandError",
    "JjNotFoundError",
    "LogEntry",
    "PyjutsuError",
    "RepositoryNotFoundError",
    "WorkspaceStatus",
]
