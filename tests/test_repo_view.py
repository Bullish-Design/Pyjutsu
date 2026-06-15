"""Slice 1 plumbing: the `RepoView` split and the Rust-defined error hierarchy."""

from __future__ import annotations

from pathlib import Path

import pyjutsu
from pyjutsu import Commit, RepoView
from pyjutsu.errors import (
    BackendError,
    ConflictError,
    PyjutsuError,
    RevsetError,
    WorkspaceError,
)


def test_head_returns_a_repo_view(scratch_repo: Path) -> None:
    view = pyjutsu.Workspace.load(scratch_repo).head()
    assert isinstance(view, RepoView)
    assert isinstance(view.working_copy(), Commit)


def test_workspace_working_copy_delegates_to_head(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    # The convenience and the explicit view observe the same `@`.
    assert ws.working_copy() == ws.head().working_copy()


def test_error_hierarchy_is_native_and_nested() -> None:
    # All subclasses come from the native ext and descend from the PyjutsuError base.
    for exc in (RevsetError, ConflictError, BackendError, WorkspaceError):
        assert issubclass(exc, PyjutsuError)
        assert exc.__module__ == "_pyjutsu"  # defined in Rust, not re-declared in Python
    assert PyjutsuError.__module__ == "_pyjutsu"


def test_load_missing_path_raises_workspace_error(tmp_path: Path) -> None:
    try:
        pyjutsu.Workspace.load(tmp_path / "nonexistent")
    except WorkspaceError:
        pass
    else:
        raise AssertionError("loading a non-repo path should raise WorkspaceError")
