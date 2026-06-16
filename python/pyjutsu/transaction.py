"""`Transaction` — a thin token over a workspace's open native transaction (concept §4, M2).

A `Transaction` does not own any jj-lib state itself; the native `Transaction` lives inside the
`PyWorkspace` handle (behind its `Mutex`). This token just drives that one open transaction and
enforces its lifecycle: a single ``with`` block that commits on clean exit and rolls back on an
exception (atomicity). Mutation methods (``describe``/``new``/…) arrive in later slices and will
re-enter the same handle; each returns the affected revision read back from the open transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import Bookmark, Commit

if TYPE_CHECKING:
    from types import TracebackType

    from ._pyjutsu import PyTransaction, PyWorkspace

__all__ = ["Transaction"]


def _complete_newline(message: str) -> str:
    """Normalize a commit description the way jj does: a non-empty one ends with a newline.

    jj-lib stores descriptions verbatim; the trailing-newline convention lives in jj's CLI. The
    binding reproduces it so a commit authored here is byte-identical to the same `jj` command's
    (matching commit ids) — and so reads of either look the same.
    """
    if message and not message.endswith("\n"):
        return message + "\n"
    return message


class Transaction:
    """An open jj transaction, scoped to one ``with`` block.

    Obtain one from :meth:`Workspace.transaction`; enter it to begin, and let the block exit
    to publish (or raise inside it to roll back). One mutation transaction publishes exactly one
    jj operation. Not reentrant and not safe to share across threads.
    """

    __slots__ = ("_handle", "_description", "_auto_snapshot", "_state", "_native", "_operation_id")

    # Lifecycle states. "pending": constructed, not yet entered. "open": inside the `with`.
    # "committed"/"rolled_back": terminal — the token cannot be reused.

    def __init__(
        self,
        handle: PyWorkspace,
        description: str,
        *,
        auto_snapshot: bool = True,
    ) -> None:
        # Internal: construct via `Workspace.transaction(...)`.
        self._handle = handle
        self._description = description
        # When set, `__enter__` snapshots a dirty `@` as a separate preceding operation before
        # beginning this transaction (matching the CLI). Disabled ⇒ the mutation sees `@` as-is.
        self._auto_snapshot = auto_snapshot
        self._state = "pending"
        # The native, unsendable transaction handle, live only between `__enter__` and `__exit__`.
        self._native: PyTransaction | None = None
        self._operation_id: str | None = None

    def __enter__(self) -> Transaction:
        if self._state != "pending":
            raise RuntimeError(f"transaction already {self._state}; create a new one")
        # Auto-snapshot a dirty `@` first, as a *separate preceding* operation (concept §0.1),
        # matching the CLI. A clean `@` snapshots to nothing (no op). Disabled ⇒ `@` seen as-is.
        if self._auto_snapshot:
            self._handle.snapshot()
        self._native = self._handle.begin_transaction()
        self._state = "open"
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if self._state != "open" or self._native is None:
            return False
        native, self._native = self._native, None
        if exc_type is not None:
            # An exception in the body aborts the whole transaction: nothing is published.
            native.rollback()
            self._state = "rolled_back"
            return False
        self._operation_id = native.commit(self._description)
        self._state = "committed"
        return False

    def _require_open(self) -> PyTransaction:
        """The live native handle, or a clear error if used outside the ``with`` block."""
        if self._state != "open" or self._native is None:
            raise RuntimeError("transaction is not open; use it inside its `with` block")
        return self._native

    def describe(self, commit: str, message: str) -> Commit:
        """Set ``commit``'s description to ``message`` → the rewritten :class:`Commit`.

        ``commit`` is any single-revision revset (a change id, commit id, or expression like
        ``@``). The change id is preserved; the returned commit carries the new commit id and
        description. Raises :class:`~pyjutsu.errors.RevsetError` unless it names exactly one
        revision. Must be called inside the transaction's ``with`` block.
        """
        return Commit.model_validate(
            self._require_open().describe(commit, _complete_newline(message))
        )

    def new(self, parents: list[str] | str | None = None) -> Commit:
        """Create a new commit on top of ``parents`` and point ``@`` at it → the new :class:`Commit`.

        ``parents`` may be a single revset, a list of revsets, or ``None`` (the default), in which
        case the new commit is a child of the current ``@`` (the common ``jj new``). With multiple
        parents the new commit is a merge: its tree is the merge of the parents' trees. Each revset
        must name exactly one revision (else :class:`~pyjutsu.errors.RevsetError`). The on-disk
        working copy is updated to the new ``@`` when the transaction commits. Must be called
        inside the transaction's ``with`` block.
        """
        if parents is None:
            revsets: list[str] | None = None
        elif isinstance(parents, str):
            revsets = [parents]
        else:
            revsets = list(parents)
        return Commit.model_validate(self._require_open().new(revsets))

    def edit(self, commit: str) -> Commit:
        """Point ``@`` at the existing ``commit`` (single-revision revset) → that :class:`Commit`.

        Unlike :meth:`new`, no new commit is written: ``@`` is moved onto an existing commit, whose
        content is returned unchanged. The on-disk working copy is updated to the edited commit's
        tree when the transaction commits. ``commit`` must name exactly one revision (else
        :class:`~pyjutsu.errors.RevsetError`); editing the root raises
        :class:`~pyjutsu.errors.ImmutableCommitError`. Must be called inside the ``with`` block.
        """
        return Commit.model_validate(self._require_open().edit(commit))

    def abandon(self, commit: str) -> None:
        """Abandon ``commit`` (single-revision revset); its children rebase onto its parent(s).

        Returns nothing — the commit is gone. Abandoning ``@`` advances ``@`` to a fresh empty
        commit on top of the old parents. ``commit`` must name exactly one revision (else
        :class:`~pyjutsu.errors.RevsetError`); abandoning the root raises
        :class:`~pyjutsu.errors.ImmutableCommitError`. Must be called inside the ``with`` block.
        """
        self._require_open().abandon(commit)

    def rebase(
        self, commit: str, *, onto: str | list[str], mode: str = "source"
    ) -> Commit:
        """Rebase ``commit`` onto ``onto`` → the rebased :class:`Commit`. ``mode`` selects which
        commits move, matching jj's flags:

        - ``"source"`` (default, ``jj rebase -s``): ``commit`` **and all its descendants**.
        - ``"revision"`` (``jj rebase -r``): **only** ``commit``; its children reattach to
          ``commit``'s old parents.
        - ``"branch"`` (``jj rebase -b``): the whole branch — the roots of ``onto..commit`` (the
          commits reachable from ``commit`` but not from any destination) plus their descendants.

        ``commit`` and each entry of ``onto`` are single-revision revsets. The change id is
        preserved; the commit id changes, and the on-disk working copy follows when the transaction
        commits if ``@`` moved. ``commit`` must name exactly one revision (else
        :class:`~pyjutsu.errors.RevsetError`); rebasing the root raises
        :class:`~pyjutsu.errors.ImmutableCommitError`; an unknown ``mode`` raises
        :class:`~pyjutsu.errors.PyjutsuError`. Must be called inside the ``with`` block.

        Interactive revision selection remains out of scope.
        """
        targets = [onto] if isinstance(onto, str) else list(onto)
        return Commit.model_validate(self._require_open().rebase(commit, targets, mode))

    def squash(self, source: str, into: str, *, message: str | None = None) -> Commit:
        """Move ``source``'s changes into ``into`` → the squashed :class:`Commit` (``jj squash``).

        ``source`` is abandoned when fully squashed; its descendants rebase onto its parent(s).
        With ``message`` the squashed commit takes it; without, ``into``'s description is kept
        (matching ``jj squash --use-destination-message``). ``source`` and ``into`` are
        single-revision revsets and must differ (else :class:`~pyjutsu.errors.PyjutsuError`);
        squashing the root raises :class:`~pyjutsu.errors.ImmutableCommitError`. Must be called
        inside the ``with`` block.

        The whole source commit is squashed: partial/interactive selection and jj's
        description-combining default are out of scope.
        """
        msg = _complete_newline(message) if message is not None else None
        return Commit.model_validate(self._require_open().squash(source, into, msg))

    def restore(self, commit: str, *, from_: str, paths: list[str] | None = None) -> Commit:
        """Replace ``commit``'s content (or just ``paths``) with ``from_``'s → the rewritten
        :class:`Commit` (matches ``jj restore --from <from_> --into <commit> [paths…]``).

        ``commit`` and ``from_`` are single-revision revsets; ``paths`` (repo-relative) scope the
        restore, else the whole tree is restored. The change id is preserved; the commit id
        changes. ``commit`` must name exactly one revision (else
        :class:`~pyjutsu.errors.RevsetError`); restoring the root raises
        :class:`~pyjutsu.errors.ImmutableCommitError`. Must be called inside the ``with`` block.
        """
        return Commit.model_validate(self._require_open().restore(commit, from_, paths))

    def create_bookmark(self, name: str, commit: str) -> Bookmark:
        """Create a new local bookmark ``name`` at ``commit`` → the new :class:`Bookmark`.

        ``commit`` is any single-revision revset. Raises
        :class:`~pyjutsu.errors.PyjutsuError` if a local bookmark ``name`` already exists (matches
        ``jj bookmark create``, which refuses to clobber), or
        :class:`~pyjutsu.errors.RevsetError` unless ``commit`` names exactly one revision. Must be
        called inside the transaction's ``with`` block.
        """
        return Bookmark.model_validate(self._require_open().create_bookmark(name, commit))

    def set_bookmark(self, name: str, commit: str) -> Bookmark:
        """Point local bookmark ``name`` at ``commit``, creating it if absent → the :class:`Bookmark`.

        Create-or-move (matches ``jj bookmark set``): unlike :meth:`create_bookmark`, it does not
        error if ``name`` already exists. ``commit`` must name exactly one revision (else
        :class:`~pyjutsu.errors.RevsetError`). Must be called inside the ``with`` block.
        """
        return Bookmark.model_validate(self._require_open().set_bookmark(name, commit))

    def delete_bookmark(self, name: str) -> None:
        """Delete local bookmark ``name`` (matches ``jj bookmark delete``).

        Returns nothing. Raises :class:`~pyjutsu.errors.PyjutsuError` if no such local bookmark
        exists, so a typo doesn't silently no-op. Must be called inside the ``with`` block.
        """
        self._require_open().delete_bookmark(name)

    def track_bookmark(self, name: str, remote: str) -> Bookmark:
        """Start tracking remote bookmark ``name@remote`` → its :class:`Bookmark` row.

        Matches ``jj bookmark track name@remote``: merges the remote bookmark into the local one and
        marks it tracked. Raises :class:`~pyjutsu.errors.PyjutsuError` if no such remote bookmark
        exists. Must be called inside the ``with`` block.
        """
        return Bookmark.model_validate(self._require_open().track_bookmark(name, remote))

    def untrack_bookmark(self, name: str, remote: str) -> Bookmark:
        """Stop tracking remote bookmark ``name@remote`` → its :class:`Bookmark` row.

        Matches ``jj bookmark untrack name@remote``: marks the remote bookmark untracked. Raises
        :class:`~pyjutsu.errors.PyjutsuError` if no such remote bookmark exists. Must be called
        inside the ``with`` block.
        """
        return Bookmark.model_validate(self._require_open().untrack_bookmark(name, remote))

    @property
    def description(self) -> str:
        """The operation description this transaction commits with."""
        return self._description

    @property
    def operation_id(self) -> str | None:
        """The id of the operation published on commit, or ``None`` until/unless committed."""
        return self._operation_id

    def __repr__(self) -> str:
        return f"Transaction(description={self._description!r}, state={self._state!r})"
