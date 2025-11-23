# tests/unit/test_enums.py
"""Test enumerations."""

from __future__ import annotations

import pytest

from pyjutsu import FileStatus


def test_file_status_values() -> None:
    """FileStatus has expected values."""
    assert FileStatus.ADDED.value == "A"
    assert FileStatus.MODIFIED.value == "M"
    assert FileStatus.DELETED.value == "D"
    assert FileStatus.RENAMED.value == "R"


def test_file_status_from_code() -> None:
    """FileStatus.from_code parses correctly."""
    assert FileStatus.from_code("A") == FileStatus.ADDED
    assert FileStatus.from_code("M") == FileStatus.MODIFIED
    assert FileStatus.from_code(" D ") == FileStatus.DELETED
    assert FileStatus.from_code("m") == FileStatus.MODIFIED


def test_file_status_from_code_unknown() -> None:
    """FileStatus.from_code returns UNKNOWN for invalid codes."""
    assert FileStatus.from_code("X") == FileStatus.UNKNOWN
    assert FileStatus.from_code("") == FileStatus.UNKNOWN
