"""`RepoView` — the read surface over an immutable repo at one operation (concept §4).

Every read lives here and follows one shape: call the native `PyRepoView`, which returns plain
data, then validate it into a Pydantic model. A `RepoView` is obtained from a `Workspace`
(`ws.head()` for the current state, `ws.at_operation(op)` for history); it is side-effect-free
and never snapshots the working copy (M1).
"""

from __future__ import annotations

from collections.abc import Iterator

from ._pyjutsu import PyRepoView
from .models import Bookmark, Commit, Conflict, Diff, DiffStat, Operation
from .revset import Revset, _revset_str

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

    def resolve(self, revset: str | Revset) -> Commit:
        """Resolve a revset naming **exactly one** revision → its :class:`Commit`.

        Accepts a revset string or a :class:`~pyjutsu.Revset` builder. Raises
        :class:`~pyjutsu.errors.RevsetError` if the revset matches zero or many revisions
        (mirrors jj's "must resolve to a single revision").
        """
        return Commit.model_validate(self._handle.resolve(_revset_str(revset)))

    def log(self, revset: str | Revset, limit: int | None = None) -> list[Commit]:
        """Evaluate a revset → its :class:`Commit` list in revset order, capped at ``limit``.

        Accepts a revset string or a :class:`~pyjutsu.Revset` builder.
        """
        return [
            Commit.model_validate(row) for row in self._handle.log(_revset_str(revset), limit)
        ]

    def iter_log(self, revset: str | Revset, limit: int | None = None) -> Iterator[Commit]:
        """Lazily yield the revset's commits as validated models (for huge histories).

        Same commits, same order as :meth:`log`, but builds one :class:`Commit` model at a time per
        step instead of a whole list — so a caller can process-and-discard rather than hold them all.
        Accepts a revset string or a :class:`~pyjutsu.Revset` builder; ``limit`` caps the count.
        """
        for row in self._handle.log_stream(_revset_str(revset), limit):
            yield Commit.model_validate(row)

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

    def conflicts(self, revset: str | Revset) -> list[Conflict]:
        """The conflicts in the single commit named by ``revset`` — one row per conflicted path.

        Accepts a revset string or a :class:`~pyjutsu.Revset` builder. Raises
        :class:`~pyjutsu.errors.RevsetError` unless ``revset`` names exactly one revision.
        """
        return [
            Conflict.model_validate(row) for row in self._handle.conflicts(_revset_str(revset))
        ]

    def diff_stat(self, revset: str | Revset, to: str | Revset | None = None) -> DiffStat:
        """The diff stat (per-file + total line counts) of a commit or a range.

        With one argument, ``revset`` names a single commit and the stat is against its parent(s)
        (``jj diff --stat -r <rev>``). With ``to`` also given, the stat is the tree-to-tree diff
        **from** ``revset`` **to** ``to`` (``jj diff --stat --from <a> --to <b>``) — each side must
        name exactly one revision. Accepts revset strings or :class:`~pyjutsu.Revset` builders.
        Raises :class:`~pyjutsu.errors.RevsetError` unless each side names exactly one revision.
        """
        if to is None:
            return DiffStat.model_validate(self._handle.diff_stat(_revset_str(revset)))
        return DiffStat.model_validate(
            self._handle.diff_stat_between(_revset_str(revset), _revset_str(to))
        )

    def diff(self, revset: str | Revset, to: str | Revset | None = None) -> Diff:
        """The name-status diff (changed paths + content hunks) of a commit or a range.

        With one argument, ``revset`` names a single commit and the diff is against its parent(s)
        (``jj diff -r <rev>``). With ``to`` also given, the diff is the tree-to-tree diff **from**
        ``revset`` **to** ``to`` (``jj diff --from <a> --to <b>``) — each side must name exactly one
        revision. Returns each changed path and how it changed
        (added/modified/removed/type_changed/renamed/copied). Accepts revset strings or
        :class:`~pyjutsu.Revset` builders. Raises :class:`~pyjutsu.errors.RevsetError` unless each
        side names exactly one revision.
        """
        if to is None:
            return Diff.model_validate(self._handle.diff(_revset_str(revset)))
        return Diff.model_validate(
            self._handle.diff_between(_revset_str(revset), _revset_str(to))
        )

    def is_ancestor(self, ancestor: str | Revset, descendant: str | Revset) -> bool:
        """Whether ``ancestor`` is an ancestor of ``descendant`` in the commit DAG.

        A commit is its own ancestor (``is_ancestor(x, x)`` is ``True``), matching
        ``git merge-base --is-ancestor``. Each side must name exactly one revision; accepts revset
        strings or :class:`~pyjutsu.Revset` builders. Raises :class:`~pyjutsu.errors.RevsetError`
        unless each side names exactly one revision.
        """
        return self._handle.is_ancestor(_revset_str(ancestor), _revset_str(descendant))

    def patch_id(self, revset: str | Revset) -> str:
        """A stable content identity for the change ``revset`` introduces against its parent(s).

        Two commits that make the **same change** — e.g. before and after a rebase/squash that
        re-hashes the commit id — share a ``patch_id`` even though their commit ids differ. It is a
        hash of the diff's changed paths and added/removed line contents (line numbers excluded); it
        is *not* byte-compatible with ``git patch-id``. Accepts a revset string or a
        :class:`~pyjutsu.Revset` builder. Raises :class:`~pyjutsu.errors.RevsetError` unless
        ``revset`` names exactly one revision.
        """
        return self._handle.patch_id(_revset_str(revset))
