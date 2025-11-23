# src/pyjutsu/enums.py
"""Enumerations for Pyjutsu."""

from __future__ import annotations

from enum import Enum


class FileStatus(str, Enum):
    """File status codes from jj."""

    ADDED = "A"
    MODIFIED = "M"
    DELETED = "D"
    RENAMED = "R"
    COPIED = "C"
    UNKNOWN = "?"

    @classmethod
    def from_code(cls, code: str) -> "FileStatus":
        """Parse status code from jj output."""
        code = code.strip().upper()
        for status in cls:
            if status.value == code:
                return status
        return cls.UNKNOWN


class ChangeState(str, Enum):
    """State of a change/commit."""

    WORKING_COPY = "working_copy"
    IMMUTABLE = "immutable"
    MUTABLE = "mutable"
    ABANDONED = "abandoned"


class BranchTrackingStatus(str, Enum):
    """Branch tracking status."""

    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    UP_TO_DATE = "up_to_date"
    UNTRACKED = "untracked"
