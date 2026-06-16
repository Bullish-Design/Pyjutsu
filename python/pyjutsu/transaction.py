"""`Transaction` — a thin token over a workspace's open native transaction (concept §4, M2).

A `Transaction` does not own any jj-lib state itself; the native `Transaction` lives inside the
`PyWorkspace` handle (behind its `Mutex`). This token just drives that one open transaction and
enforces its lifecycle: a single ``with`` block that commits on clean exit and rolls back on an
exception (atomicity). Mutation methods (``describe``/``new``/…) arrive in later slices and will
re-enter the same handle; each returns the affected revision read back from the open transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import Commit

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
        # Reserved for slice 5 (auto-snapshot of a dirty `@` on open); accepted now for a stable
        # signature, not yet wired into the native `begin_transaction`.
        self._auto_snapshot = auto_snapshot
        self._state = "pending"
        # The native, unsendable transaction handle, live only between `__enter__` and `__exit__`.
        self._native: PyTransaction | None = None
        self._operation_id: str | None = None

    def __enter__(self) -> Transaction:
        if self._state != "pending":
            raise RuntimeError(f"transaction already {self._state}; create a new one")
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
