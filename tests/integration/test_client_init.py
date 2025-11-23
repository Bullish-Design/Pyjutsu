# tests/integration/test_client_init.py
"""Integration tests for client initialization."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient, JjNotFoundError, RepositoryNotFoundError


def test_client_init_creates_repository(tmp_path: Path) -> None:
    """JjClient.init creates a new repository when jj is available."""
    repo_path = tmp_path / "test-repo"
    try:
        client = JjClient.init(repo_path)
    except JjNotFoundError:
        pytest.skip("jj not installed in test environment")
    assert client.repo_path == repo_path.resolve()
    assert repo_path.exists()


def test_client_invalid_repository_raises(tmp_path: Path) -> None:
    """Constructing JjClient on non-repo raises RepositoryNotFoundError."""
    path = tmp_path / "not-a-repo"
    path.mkdir()
    try:
        JjClient(path)
    except JjNotFoundError:
        pytest.skip("jj not installed in test environment")
    except RepositoryNotFoundError:
        # Expected when jj is installed
        return
    else:  # pragma: no cover - defensive
        pytest.fail("Expected RepositoryNotFoundError")


def test_client_repr_contains_path(tmp_path: Path) -> None:
    """JjClient repr shows repository path."""
    repo_path = tmp_path / "test-repo"
    try:
        client = JjClient.init(repo_path)
    except JjNotFoundError:
        pytest.skip("jj not installed in test environment")
    assert str(repo_path.resolve()) in repr(client)
