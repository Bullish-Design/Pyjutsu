# tests/unit/test_exceptions.py
"""Test exception hierarchy."""

from __future__ import annotations

import pytest

from pyjutsu import (
    ConflictError,
    InvalidRevisionError,
    JjCommandError,
    JjNotFoundError,
    PyjutsuError,
    RepositoryNotFoundError,
)


def test_exception_hierarchy() -> None:
    """All custom exceptions inherit from PyjutsuError."""
    assert issubclass(JjNotFoundError, PyjutsuError)
    assert issubclass(JjCommandError, PyjutsuError)
    assert issubclass(RepositoryNotFoundError, PyjutsuError)
    assert issubclass(ConflictError, PyjutsuError)
    assert issubclass(InvalidRevisionError, PyjutsuError)


def test_jj_not_found_error() -> None:
    """JjNotFoundError has default message."""
    err = JjNotFoundError()
    assert "not found" in str(err).lower()


def test_jj_command_error() -> None:
    """JjCommandError stores command details."""
    err = JjCommandError("jj status", 1, stdout="", stderr="error message")
    assert err.command == "jj status"
    assert err.returncode == 1
    assert err.stderr == "error message"
    assert "jj status" in str(err)
    assert "exit code 1" in str(err).lower()


def test_repository_not_found_error() -> None:
    """RepositoryNotFoundError stores path."""
    err = RepositoryNotFoundError("/tmp/notrepo")
    assert err.path == "/tmp/notrepo"
    assert "/tmp/notrepo" in str(err)


def test_invalid_revision_error() -> None:
    """InvalidRevisionError stores revision."""
    err = InvalidRevisionError("bad@rev")
    assert err.revision == "bad@rev"
    assert "bad@rev" in str(err)
