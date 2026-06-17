"""`RepoView` — the read surface over an immutable repo at one operation (concept §4).

Every read lives here and follows one shape: call the native `PyRepoView`, which returns plain
data, then validate it into a Pydantic model. A `RepoView` is obtained from a `Workspace`
(`ws.head()` for the current state, `ws.at_operation(op)` for history); it is side-effect-free
and never snapshots the working copy (M1).
"""

from __future__ import annotations

from ._pyjutsu import PyRepoView
from .models import Bookmark, Commit, Conflict, Diff, DiffStat, Operation

__all__ = ["RepoView"]


class RepoView:
    """An immutable view of the repo at a single operation, scoped to one workspace."""

    __slots__ = ("_handle",)

    def __init__(self, handle: PyRepoView) -> None:
        # Internal: obtain via `Workspace.head()` / `Workspace.at_operation(...)`.
        self._handle = handle

    def working_copy(self) -> Commit:
        """Read ``@`` — the originating workspace's working-copy commit. Read-only (no snapshot)."""
        return Commit.model_validate(self._handle.working_copy())

    def resolve(self, revset: str) -> Commit:
        """Resolve a revset naming **exactly one** revision → its :class:`Commit`.

        Raises :class:`~pyjutsu.errors.RevsetError` if the revset matches zero or many
        revisions (mirrors jj's "must resolve to a single revision").
        """
        return Commit.model_validate(self._handle.resolve(revset))

    def log(self, revset: str, limit: int | None = None) -> list[Commit]:
        """Evaluate a revset → its :class:`Commit` list in revset order, capped at ``limit``."""
        return [Commit.model_validate(row) for row in self._handle.log(revset, limit)]

    def operations(self, limit: int | None = None) -> list[Operation]:
        """The op log from this view's operation: it and its ancestors, newest first."""
        return [Operation.model_validate(row) for row in self._handle.operations(limit)]

    @property
    def operation_id(self) -> str:
        """The id of the operation this view observes (its head operation)."""
        return self._handle.operation_id()

    def bookmarks(self) -> list[Bookmark]:
        """All bookmarks at this operation: local rows (``remote=None``) then remote-tracking refs."""
        return [Bookmark.model_validate(row) for row in self._handle.bookmarks()]

    def conflicts(self, revset: str) -> list[Conflict]:
        """The conflicts in the single commit named by ``revset`` — one row per conflicted path.

        Raises :class:`~pyjutsu.errors.RevsetError` unless ``revset`` names exactly one revision.
        """
        return [Conflict.model_validate(row) for row in self._handle.conflicts(revset)]

    def diff_stat(self, revset: str) -> DiffStat:
        """The diff stat of the single commit named by ``revset`` against its parent(s).

        Raises :class:`~pyjutsu.errors.RevsetError` unless ``revset`` names exactly one revision.
        """
        return DiffStat.model_validate(self._handle.diff_stat(revset))

    def diff(self, revset: str) -> Diff:
        """The name-status diff of the single commit named by ``revset`` against its parent(s).

        Returns each changed path and how it changed (added/modified/removed/type_changed).
        Raises :class:`~pyjutsu.errors.RevsetError` unless ``revset`` names exactly one revision.
        """
        return Diff.model_validate(self._handle.diff(revset))
