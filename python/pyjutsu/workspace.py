"""The `Workspace` facade — Pyjutsu's main entry point.

A `Workspace` wraps one opaque native handle (one working-copy path); the repo behind it is
shared (concept §11). M0 exposes loading + reading `@`; reads/transactions/op-log/git follow
in M1–M3.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

from ._pyjutsu import PyWorkspace
from .errors import JjCliError, PyjutsuError
from .models import (
    Bookmark,
    Commit,
    Conflict,
    Diff,
    DiffStat,
    JjResult,
    Operation,
    Remote,
    WorkspaceInfo,
)
from .repo_view import RepoView
from .revset import Revset
from .transaction import Transaction

__all__ = ["Workspace"]


class Workspace:
    """A loaded jj workspace bound to a single working-copy path.

    **Async usage:** every method releases the GIL while it touches the backend, so in an asyncio
    app wrap a call in :func:`asyncio.to_thread` (e.g. ``await asyncio.to_thread(ws.git_fetch,
    "origin")``) to run it off the event loop. A native async facade is intentionally not provided.
    """

    __slots__ = ("_handle",)

    def __init__(self, handle: PyWorkspace) -> None:
        # Internal: construct via `Workspace.load(...)`, not directly.
        self._handle = handle

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Workspace:
        """Load the workspace whose working copy is rooted at ``path``."""
        return cls(PyWorkspace.load(os.fspath(path)))

    @classmethod
    def init(cls, path: str | os.PathLike[str], *, colocate: bool = False) -> Workspace:
        """Create a new jj repo + default workspace at ``path`` → a :class:`Workspace`.

        Matches ``jj git init`` (``colocate=False``, an internal git store under
        ``.jj/repo/store/git``) / ``jj git init --colocate`` (``colocate=True``, a ``.git`` sharing
        the working copy). The new ``@`` is an empty commit on the root commit. Raises
        :class:`~pyjutsu.errors.WorkspaceError` if ``path`` already holds a repo.
        """
        return cls(PyWorkspace.init(os.fspath(path), colocate))

    def add_workspace(
        self, path: str | os.PathLike[str], *, name: str | None = None
    ) -> WorkspaceInfo:
        """Add a secondary workspace at ``path`` → its :class:`WorkspaceInfo` (``jj workspace add``).

        The repo's store is shared; the new workspace gets its own ``@`` — here an empty commit on
        the **root** commit. (The CLI's default instead bases the new ``@`` on the current
        workspace's parents; that ``-r <revs>`` placement and ``--sparse-patterns`` inheritance are
        out-of-scope refinements.) ``name`` defaults to ``path``'s basename. One ``add workspace``
        operation is published.
        """
        return WorkspaceInfo.model_validate(self._handle.add_workspace(os.fspath(path), name))

    def forget_workspace(self, name: str) -> None:
        """Stop tracking workspace ``name``'s ``@`` in the repo (``jj workspace forget <name>``).

        The on-disk files at that workspace are left untouched; only the repo's record of its
        working-copy commit is dropped, publishing one operation. Raises
        :class:`~pyjutsu.errors.PyjutsuError` if no workspace ``name`` is tracked.
        """
        self._handle.forget_workspace(name)

    def workspaces(self) -> list[WorkspaceInfo]:
        """All workspaces tracked in the repo → their :class:`WorkspaceInfo` rows (``jj workspace
        list``): the ``default`` workspace plus any added with :meth:`add_workspace`.
        """
        return [WorkspaceInfo.model_validate(row) for row in self._handle.workspaces()]

    def git_import(self) -> Operation | None:
        """Reflect changes in the backing git repo into jj's view → the published :class:`Operation`,
        or ``None`` if nothing changed (no operation published).

        Matches ``jj git import``: imports git HEAD and refs (creating/updating bookmarks for new git
        branches, abandoning commits that became unreachable in git). If the import moves ``@``, the
        on-disk working copy is checked out to the new ``@``. Raises
        :class:`~pyjutsu.errors.GitError` on a git backend failure.
        """
        row = self._handle.git_import()
        return Operation.model_validate(row) if row is not None else None

    def git_export(self) -> Operation | None:
        """Export jj's bookmarks to the backing git repo's refs → the published :class:`Operation`,
        or ``None`` if nothing changed (no operation published).

        Matches ``jj git export``: writes each jj bookmark to its ``refs/heads/<name>`` git ref.
        Raises :class:`~pyjutsu.errors.GitError` listing any bookmark that failed to export.
        """
        row = self._handle.git_export()
        return Operation.model_validate(row) if row is not None else None

    def git_fetch(
        self, remote: str, *, bookmarks: list[str] | None = None
    ) -> Operation | None:
        """Fetch ``remote``'s bookmarks into jj's view → the published :class:`Operation`, or
        ``None`` if nothing changed (no operation published).

        Matches ``jj git fetch``: runs a ``git fetch`` and imports the fetched remote-tracking
        refs (creating/updating ``<bookmark>@<remote>`` rows). ``bookmarks=None`` (the default)
        fetches all bookmarks; pass a list to select bookmarks using jj's string-pattern
        vocabulary (``jj git fetch --branch``):

        - each entry is a **glob by default** — a literal name matches itself, ``"feature/*"``
          matches the prefix;
        - a ``kind:`` prefix forces a kind: ``"exact:main"``, ``"glob:feat/*"``,
          ``"substring:fix"``, ``"regex:^rel-"`` (and the ``-i`` case-insensitive variants);
        - a leading ``~`` negates an entry. Positive entries are unioned; each negated entry is
          then subtracted, so ``["glob:feature/*", "~feature/b"]`` fetches ``feature/*`` except
          ``feature/b``. A negatives-only list subtracts from all bookmarks.

        Tags are still not fetched (jj #7528) and ``--all-remotes`` is out of scope. Raises
        :class:`~pyjutsu.errors.GitError` on a malformed pattern or a git failure (unknown remote,
        rejected update, subprocess error).
        """
        row = self._handle.git_fetch(remote, bookmarks)
        return Operation.model_validate(row) if row is not None else None

    def git_push(
        self,
        remote: str,
        bookmark: str | list[str] | None = None,
        *,
        allow_new: bool = False,
        delete: bool = False,
        all: bool = False,
        tracked: bool = False,
    ) -> Operation | None:
        """Push local bookmarks to ``remote`` → the published :class:`Operation`, or ``None`` if
        nothing changed (no operation published).

        Matches ``jj git push``: runs a ``git push`` and updates the remote-tracking bookmark(s).
        Pass ``bookmark`` (one name or a list) to push named bookmarks (``--bookmark``); several
        push in one operation. ``allow_new=False`` (the default) refuses to create a bookmark that
        doesn't yet exist on the remote (the CLI's ``--allow-new`` gate); pass ``allow_new=True`` to
        create it. ``delete=True`` removes each named bookmark **on the remote** (it needs a
        remote-tracking ref but not a local bookmark).

        ``all=True`` (``--all``) pushes **every local bookmark** — creating new ones and
        fast-forwarding existing ones; ``tracked=True`` (``--tracked``) pushes only bookmarks already
        **tracking** this remote. These bulk modes ignore ``bookmark`` (which must be ``None``/empty)
        and are mutually exclusive. Neither deletes: a locally-absent bookmark is skipped, matching
        jj 0.38 (deletions need ``delete=True``).

        Raises :class:`~pyjutsu.errors.GitError` if no bookmark is given without a bulk mode (or one
        is given with a bulk mode), both ``all`` and ``tracked`` are set, ``delete`` is combined with
        a bulk mode, a (non-delete) named bookmark is missing/conflicted or new without
        ``allow_new``, a delete target has no remote ref, or the remote rejects the push. Force-push,
        ``--deleted``/``--change``/``-r <rev>`` selection remain out of scope.
        """
        if bookmark is None:
            names: list[str] = []
        elif isinstance(bookmark, str):
            names = [bookmark]
        else:
            names = list(bookmark)
        row = self._handle.git_push(remote, names, allow_new, delete, all, tracked)
        return Operation.model_validate(row) if row is not None else None

    @classmethod
    def git_clone(
        cls,
        url: str,
        path: str | os.PathLike[str],
        *,
        colocate: bool = False,
        remote: str = "origin",
    ) -> Workspace:
        """Clone the git repo at ``url`` into a new jj workspace at ``path`` → a :class:`Workspace`.

        Matches ``jj git clone``. jj-lib has no clone primitive, so this composes existing verbs:
        :meth:`init` a fresh repo, :meth:`add_remote` ``remote`` → ``url``, then :meth:`git_fetch`
        the remote's bookmarks. If the remote advertises a default branch, ``@`` is set to a new
        empty commit on top of that branch's tip (so the clone is immediately usable); if discovery
        is ambiguous (no default branch advertised), ``@`` is left as the empty root child.

        Raises :class:`~pyjutsu.errors.WorkspaceError` if ``path`` already holds a repo, or
        :class:`~pyjutsu.errors.GitError` on a remote/fetch failure.
        """
        # `jj git clone` creates the destination directory; `init` (like `jj git init`) needs it to
        # exist already, so create it here first.
        Path(path).mkdir(parents=True, exist_ok=True)
        ws = cls.init(path, colocate=colocate)
        ws.add_remote(remote, url)
        ws.git_fetch(remote)

        # Place `@` on the remote's default branch tip, mirroring `jj git clone`. The default
        # branch is fetched as the remote-tracking bookmark `<default>@<remote>`; if the remote
        # advertises no default, leave `@` on the empty root child (the documented ambiguous case).
        default = ws._handle.git_default_branch(remote)
        if default is not None:
            try:
                tip = ws.head().resolve(f"{default}@{remote}")
            except PyjutsuError:
                tip = None
            if tip is not None:
                with ws.transaction(f"check out {default}", auto_snapshot=False) as tx:
                    tx.new([tip.commit_id])
        return ws

    def remotes(self) -> list[Remote]:
        """The configured git remotes → their :class:`Remote` rows (``jj git remote list``).

        Each row carries the remote's name and **fetch** URL (``None`` if none is configured).
        Read-only.
        """
        return [Remote.model_validate(row) for row in self._handle.remotes()]

    def add_remote(self, name: str, url: str) -> None:
        """Add a git remote ``name`` → ``url`` (``jj git remote add``), publishing one operation.

        ``url`` is used as both the fetch and push URL (the CLI default). Raises
        :class:`~pyjutsu.errors.GitError` if a remote ``name`` already exists.
        """
        self._handle.add_remote(name, url)

    def remove_remote(self, name: str) -> None:
        """Remove the git remote ``name`` (``jj git remote remove``), publishing one operation.

        Also drops the remote's tracking refs from jj's view. Raises
        :class:`~pyjutsu.errors.GitError` if no remote ``name`` exists.
        """
        self._handle.remove_remote(name)

    def rename_remote(self, old: str, new: str) -> None:
        """Rename git remote ``old`` to ``new`` (``jj git remote rename``), publishing one operation.

        Raises :class:`~pyjutsu.errors.GitError` if ``old`` doesn't exist or ``new`` already does.
        """
        self._handle.rename_remote(old, new)

    def set_remote_url(self, name: str, url: str) -> None:
        """Change git remote ``name``'s fetch URL to ``url`` (``jj git remote set-url``).

        This is a pure git-config write — it changes no jj view and so publishes **no** jj operation
        (unlike the other remote verbs). Raises :class:`~pyjutsu.errors.GitError` if no remote
        ``name`` exists.
        """
        self._handle.set_remote_url(name, url)

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

    def is_stale(self) -> bool:
        """Whether the on-disk working copy is stale relative to the repo's current ``@``.

        The repo advanced past (or diverged from) the operation the working copy was last written
        at, and the on-disk tree no longer matches ``@`` — a ``jj`` command would auto-reconcile (or
        refuse). Mutating or snapshotting a stale ``@`` raises
        :class:`~pyjutsu.errors.StaleWorkingCopyError`; call :meth:`update_stale` to reconcile.
        """
        return self._handle.is_stale()

    def update_stale(self) -> Commit | None:
        """Reconcile a stale working copy by checking out the repo's current ``@`` → that
        :class:`Commit`, or ``None`` if the working copy was already fresh (nothing to do).

        Matches ``jj workspace update-stale``. The on-disk files are updated to ``@``'s tree and the
        working copy's recorded operation is advanced to the repo head.
        """
        row = self._handle.update_stale()
        return Commit.model_validate(row) if row is not None else None

    def undo(self, operation: str | None = None) -> Operation:
        """Revert one operation, publishing a new operation that applies its reverse → that
        :class:`Operation`. With ``operation=None`` (the default) the **head** operation is undone;
        otherwise pass an op id, prefix, or expression (``"@"``, ``"@-"``, …).

        Matches ``jj undo``. Undoing the repo-initialization operation (it has no parent) or a merge
        operation raises :class:`~pyjutsu.errors.PyjutsuError`. If the reverse moves ``@``, the
        on-disk working copy is checked out to the new ``@``.
        """
        return Operation.model_validate(self._handle.undo(operation))

    def restore_operation(self, operation: str) -> Operation:
        """Reset the repo to the state a past operation recorded, publishing a new operation → that
        :class:`Operation`. ``operation`` is an op id, prefix, or expression (``"@-"``, …).

        Matches ``jj op restore``. If the restored state moves ``@``, the on-disk working copy is
        checked out to it.
        """
        return Operation.model_validate(self._handle.restore_operation(operation))

    def head(self) -> RepoView:
        """A :class:`RepoView` of the repo at its **head** operation, scoped to this workspace.

        All reads live on the view; the conveniences below delegate to a fresh head view.
        """
        return RepoView(self._handle.head_view())

    def working_copy(self) -> Commit:
        """Read ``@`` — this workspace's working-copy commit. Read-only (no snapshot)."""
        return self.head().working_copy()

    def resolve(self, revset: str | Revset) -> Commit:
        """Resolve a single-revision revset → its :class:`Commit` (delegates to a head view)."""
        return self.head().resolve(revset)

    def log(self, revset: str | Revset, limit: int | None = None) -> list[Commit]:
        """Evaluate a revset → its :class:`Commit` list (delegates to a head view)."""
        return self.head().log(revset, limit)

    def iter_log(self, revset: str | Revset, limit: int | None = None) -> Iterator[Commit]:
        """Lazily yield a revset's commits one model at a time (delegates to a head view).

        Same commits/order as :meth:`log`, for huge histories; see :meth:`RepoView.iter_log`.
        """
        return self.head().iter_log(revset, limit)

    def operations(self, limit: int | None = None) -> list[Operation]:
        """The op log (head operation + ancestors, newest first), capped at ``limit``."""
        return self.head().operations(limit)

    def bookmarks(self) -> list[Bookmark]:
        """All bookmarks (local + remote-tracking) at the head operation."""
        return self.head().bookmarks()

    def conflicts(self, revset: str | Revset) -> list[Conflict]:
        """The conflicts in the single commit named by ``revset`` (delegates to a head view)."""
        return self.head().conflicts(revset)

    def diff_stat(self, revset: str | Revset) -> DiffStat:
        """The diff stat of the single commit named by ``revset`` (delegates to a head view)."""
        return self.head().diff_stat(revset)

    def diff(self, revset: str | Revset) -> Diff:
        """The name-status diff of the single commit named by ``revset`` (delegates to a head view)."""
        return self.head().diff(revset)

    def head_operation(self) -> str:
        """The id of the current head operation."""
        return self._handle.head_operation()

    def at_operation(self, op: str) -> RepoView:
        """A historical :class:`RepoView` at the operation named by ``op`` (id/prefix/expr).

        Reads observe that past repo state; the on-disk working copy is untouched.
        """
        return RepoView(self._handle.at_operation(op))

    def run_jj(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        input: str | None = None,
        jj_binary: str | None = None,
    ) -> JjResult:
        """**Escape hatch:** run the external ``jj`` binary against this workspace → its raw result.

        A deliberate, clearly-labeled *exit* from pyjutsu's typed in-process surface for operations
        it doesn't (yet) bind. It returns the captured :class:`~pyjutsu.JjResult` (args, exit code,
        stdout, stderr) and **parses nothing** into models — that is the whole point. ``args`` is the
        ``jj`` command **without** the leading ``jj`` (e.g. ``["describe", "-m", "msg"]``); it is
        passed verbatim with no shell, so values are never shell-interpreted.

        The subprocess runs with this workspace's root as its cwd and inherits the current process
        environment (so ``JJ_CONFIG`` and friends flow through). The binary is resolved in order:
        the ``jj_binary`` argument, the ``PYJUTSU_JJ`` env var, then ``jj`` on ``PATH``.

        ``check=True`` (the default) raises :class:`~pyjutsu.errors.JjCliError` on a non-zero exit;
        ``check=False`` returns the result regardless. Pass ``input`` to send text on stdin.

        .. caution::
            Unlike the rest of pyjutsu, this depends on an **external** ``jj`` binary on ``PATH``,
            which the library cannot guarantee matches the linked engine
            (``pyjutsu.JJ_LIB_TARGET``). For fidelity it should match; this is an escape hatch, not
            part of the in-process guarantee. See :meth:`jj_version` to assert the match yourself.

        Raises :class:`~pyjutsu.errors.JjCliError` if no ``jj`` binary can be found or launched.
        """
        argv = list(args)
        binary = jj_binary or os.environ.get("PYJUTSU_JJ") or shutil.which("jj")
        if binary is None:
            raise JjCliError(
                "jj binary not found (pass jj_binary=, set PYJUTSU_JJ, or put jj on PATH)",
                command=argv,
                returncode=None,
                stdout="",
                stderr="",
            )
        try:
            proc = subprocess.run(
                [binary, *argv],
                cwd=self.root,
                env=os.environ,
                capture_output=True,
                text=True,
                input=input,
            )
        except OSError as exc:
            raise JjCliError(
                f"could not launch jj binary {binary!r}: {exc}",
                command=argv,
                returncode=None,
                stdout="",
                stderr=str(exc),
            ) from exc
        result = JjResult(
            args=argv, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
        )
        if check and proc.returncode != 0:
            raise JjCliError(
                f"jj {' '.join(argv)} exited with status {proc.returncode}",
                command=argv,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        return result

    def jj_version(self, *, jj_binary: str | None = None) -> str:
        """The external ``jj`` binary's version string (``jj --version``), for asserting it matches
        :data:`pyjutsu.JJ_LIB_TARGET` before relying on :meth:`run_jj`. Runs one subprocess."""
        return self.run_jj(["--version"], jj_binary=jj_binary).stdout.strip()

    def __repr__(self) -> str:
        return f"Workspace(name={self.name!r}, root={str(self.root)!r})"
