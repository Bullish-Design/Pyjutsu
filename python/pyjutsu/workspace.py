"""The `Workspace` facade — Pyjutsu's main entry point.

A `Workspace` wraps one opaque native handle (one working-copy path); the repo behind it is
shared (concept §11). M0 exposes loading + reading `@`; reads/transactions/op-log/git follow
in M1–M3.
"""

from __future__ import annotations

import os
from pathlib import Path

from ._pyjutsu import PyWorkspace
from .models import Bookmark, Commit, Conflict, DiffStat, Operation
from .repo_view import RepoView
from .transaction import Transaction

__all__ = ["Workspace"]


class Workspace:
    """A loaded jj workspace bound to a single working-copy path."""

    __slots__ = ("_handle",)

    def __init__(self, handle: PyWorkspace) -> None:
        # Internal: construct via `Workspace.load(...)`, not directly.
        self._handle = handle

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Workspace:
        """Load the workspace whose working copy is rooted at ``path``."""
        return cls(PyWorkspace.load(os.fspath(path)))

    @property
    def name(self) -> str:
        """This workspace's name/id (e.g. ``"default"``)."""
        return self._handle.name()

    @property
    def root(self) -> Path:
        """The filesystem root of this workspace's working copy (canonicalized)."""
        return Path(self._handle.workspace_root())

    def transaction(self, description: str, *, auto_snapshot: bool = True) -> Transaction:
        """Open a write transaction committing as ``description`` (concept §4, M2).

        Use it as a context manager: the ``with`` block begins the transaction, publishes it on
        clean exit, and rolls it back on any exception (atomicity). At most one transaction may
        be open on a workspace at a time. A mutation transaction publishes exactly one jj
        operation::

            with ws.transaction("describe @") as tx:
                ...  # mutation methods arrive in later slices

        ``auto_snapshot`` (default ``True``) snapshots a dirty ``@`` as a separate preceding
        operation on open (matching the CLI); set it ``False`` to have the mutation see ``@`` as-is.
        """
        return Transaction(self._handle, description, auto_snapshot=auto_snapshot)

    def snapshot(self) -> Operation | None:
        """Snapshot a dirty ``@`` as a separate ``snapshot working copy`` operation → that
        :class:`Operation`, or ``None`` if ``@`` was already clean (no operation published).

        This is what the ``jj`` CLI does automatically before each command; :meth:`transaction`
        does it for you on open when ``auto_snapshot`` is set. Raises
        :class:`~pyjutsu.errors.StaleWorkingCopyError` if ``@`` is stale.
        """
        row = self._handle.snapshot()
        return Operation.model_validate(row) if row is not None else None

    def head(self) -> RepoView:
        """A :class:`RepoView` of the repo at its **head** operation, scoped to this workspace.

        All reads live on the view; the conveniences below delegate to a fresh head view.
        """
        return RepoView(self._handle.head_view())

    def working_copy(self) -> Commit:
        """Read ``@`` — this workspace's working-copy commit. Read-only (no snapshot)."""
        return self.head().working_copy()

    def resolve(self, revset: str) -> Commit:
        """Resolve a single-revision revset → its :class:`Commit` (delegates to a head view)."""
        return self.head().resolve(revset)

    def log(self, revset: str, limit: int | None = None) -> list[Commit]:
        """Evaluate a revset → its :class:`Commit` list (delegates to a head view)."""
        return self.head().log(revset, limit)

    def operations(self, limit: int | None = None) -> list[Operation]:
        """The op log (head operation + ancestors, newest first), capped at ``limit``."""
        return self.head().operations(limit)

    def bookmarks(self) -> list[Bookmark]:
        """All bookmarks (local + remote-tracking) at the head operation."""
        return self.head().bookmarks()

    def conflicts(self, revset: str) -> list[Conflict]:
        """The conflicts in the single commit named by ``revset`` (delegates to a head view)."""
        return self.head().conflicts(revset)

    def diff_stat(self, revset: str) -> DiffStat:
        """The diff stat of the single commit named by ``revset`` (delegates to a head view)."""
        return self.head().diff_stat(revset)

    def head_operation(self) -> str:
        """The id of the current head operation."""
        return self._handle.head_operation()

    def at_operation(self, op: str) -> RepoView:
        """A historical :class:`RepoView` at the operation named by ``op`` (id/prefix/expr).

        Reads observe that past repo state; the on-disk working copy is untouched.
        """
        return RepoView(self._handle.at_operation(op))

    def __repr__(self) -> str:
        return f"Workspace(name={self.name!r}, root={str(self.root)!r})"
