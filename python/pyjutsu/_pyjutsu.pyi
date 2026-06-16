"""Type stub for the `_pyjutsu` native extension (the thin PyO3 layer).

The extension returns plain data; the pure-Python package validates/wraps it. Keep this stub
in sync with `src/`.
"""

from __future__ import annotations

import os

class PyjutsuError(Exception):
    """Base class for errors raised by the native layer."""

class RevsetError(PyjutsuError):
    """A revset failed to parse, resolve, or evaluate."""

class ConflictError(PyjutsuError):
    """A conflict blocked an operation."""

class BackendError(PyjutsuError):
    """The underlying store/backend reported an error."""

class WorkspaceError(PyjutsuError):
    """A workspace could not be loaded or is unusable."""

class WorkingCopyError(PyjutsuError):
    """The working copy could not be locked, snapshotted, or checked out."""

class StaleWorkingCopyError(WorkingCopyError):
    """The working copy is stale (another operation moved ``@``)."""

class ImmutableCommitError(PyjutsuError):
    """An attempt was made to rewrite or abandon an immutable commit (e.g. the root)."""

def version() -> str:
    """Return the pinned jj-lib version this extension was built against (e.g. ``"0.38.0"``)."""

class PyRepoView:
    """Opaque handle to a ReadonlyRepo at one operation, scoped to a workspace."""

    def working_copy(self) -> dict[str, object]: ...
    def resolve(self, revset_str: str) -> dict[str, object]: ...
    def log(self, revset_str: str, limit: int | None = ...) -> list[dict[str, object]]: ...
    def operations(self, limit: int | None = ...) -> list[dict[str, object]]: ...
    def operation_id(self) -> str: ...
    def bookmarks(self) -> list[dict[str, object]]: ...
    def conflicts(self, revset_str: str) -> list[dict[str, object]]: ...
    def diff_stat(self, revset_str: str) -> dict[str, object]: ...

class PyWorkspace:
    """Opaque handle to one jj workspace (one working-copy path)."""

    @staticmethod
    def load(path: str | os.PathLike[str]) -> PyWorkspace: ...
    def name(self) -> str: ...
    def workspace_root(self) -> str: ...
    def head_view(self) -> PyRepoView: ...
    def head_operation(self) -> str: ...
    def at_operation(self, op_str: str) -> PyRepoView: ...
    def begin_transaction(self) -> PyTransaction: ...

class PyTransaction:
    """Opaque, single-thread-bound handle to one in-flight jj transaction."""

    def describe(self, revset_str: str, message: str) -> dict[str, object]: ...
    def new(self, parents: list[str] | None = ...) -> dict[str, object]: ...
    def edit(self, revset_str: str) -> dict[str, object]: ...
    def abandon(self, revset_str: str) -> None: ...
    def commit(self, description: str) -> str: ...
    def rollback(self) -> None: ...
