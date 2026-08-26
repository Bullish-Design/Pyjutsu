"""The `Workspace` facade — Pyjutsu's main entry point.

A `Workspace` wraps one opaque native handle (one working-copy path); the repo behind it is
shared (concept §11). M0 exposes loading + reading `@`; reads/transactions/op-log/git follow
in M1–M3.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import warnings
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from ._pyjutsu import PyWorkspace
from .errors import HookAbort, JjCliError, PostHookError, PyjutsuError
from .git import GitView
from .hooks import CONFIG_FILENAME, HookRegistry
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
from .revset import Revset, _revset_str
from .transaction import Transaction

__all__ = ["Workspace"]


def _normalize_revisions(
    revisions: str | Revset | Sequence[str | Revset] | None,
) -> list[str] | None:
    """Render public revision inputs into the plain list accepted by the native layer."""
    if revisions is None:
        return None
    if isinstance(revisions, (str, Revset)):
        return [_revset_str(revisions)]
    return [_revset_str(revision) for revision in revisions]


class Workspace:
    """A loaded jj workspace bound to a single working-copy path.

    **Async usage:** every method releases the GIL while it touches the backend, so in an asyncio
    app wrap a call in :func:`asyncio.to_thread` (e.g. ``await asyncio.to_thread(ws.git_fetch,
    "origin")``) to run it off the event loop. A native async facade is intentionally not provided.

    **Concurrency:** only :meth:`transaction` is guarded against re-entry (at most one open
    transaction per workspace). The other mutators (:meth:`undo`, :meth:`restore_operation`,
    :meth:`snapshot`, the ``git_*`` verbs, the remote-CRUD verbs) are **not** mutually excluded with
    an open transaction — an open transaction does not hold the workspace lock. If you run one of
    them (e.g. via :func:`asyncio.to_thread`) while a ``with ws.transaction(...)`` block is open,
    both publish operations; jj records them as divergent operations that later merge (this is jj's
    normal concurrency model, not corruption), but the interleaving may surprise you. Keep writes on
    one workspace serialized unless you specifically want concurrent operations.

    **Performance:** each read *shortcut* on this facade (:meth:`log`, :meth:`diff_stat`,
    :meth:`bookmarks`, …) loads a fresh head view — i.e. re-reads the repo at its latest operation,
    like the CLI. For several reads of the same state, obtain one view with ``view = ws.head()`` and
    reuse it (``view.log(...)``, ``view.diff_stat(...)``) to load the repo once.
    """

    __slots__ = ("_handle", "_hooks", "_git")

    def __init__(self, handle: PyWorkspace) -> None:
        # Internal: construct via `Workspace.load(...)`, not directly.
        self._handle = handle
        # The workspace's hook registry — zero hooks until one is added or a config is loaded.
        # Rooted at the working copy so declarative command hooks run with the right cwd.
        self._hooks = HookRegistry(root=Path(self._handle.workspace_root()))
        # Lazily-created `GitView` (the `ws.git` namespace); cached in a slot.
        self._git: GitView | None = None

    @classmethod
    def load(cls, path: str | os.PathLike[str], *, hooks_config: str = "auto") -> Workspace:
        """Load the workspace whose working copy is rooted at ``path``.

        ``hooks_config`` selects the declarative hook config (pre-commit-config style, see
        :mod:`pyjutsu.hooks`):

        - ``"auto"`` (default): load ``<path>/.pyjutsu-hooks.toml`` when it exists. A repo without
          one is unchanged (no hooks, no file reads).
        - ``"off"``: never read a config file — imperative :attr:`hooks` registration only.
        - any other value: treated as a config file path to load.
        """
        ws = cls(PyWorkspace.load(os.fspath(path)))
        ws._load_hooks_config(hooks_config)
        return ws

    @classmethod
    def init(
        cls,
        path: str | os.PathLike[str],
        *,
        colocate: bool = False,
        trunk: str | None = None,
        hooks_config: str = "auto",
    ) -> Workspace:
        """Create or adopt a jj repo + default workspace at ``path`` → a :class:`Workspace`.

        Matches ``jj git init`` (``colocate=False``, an internal git store under
        ``.jj/repo/store/git``) / ``jj git init --colocate`` (``colocate=True``, a ``.git`` sharing
        the working copy). For a fresh repo the new ``@`` is an empty commit on the root commit.

        When ``colocate=True`` and ``path`` already holds a ``.git``, that git repo is **adopted**:
        its HEAD + refs are imported (existing branches become jj bookmarks) and ``@`` becomes an
        empty child of the imported HEAD, with any uncommitted working-tree edits preserved. A repo
        with no commits yet leaves the empty ``@`` on the root commit.

        Re-adopting does not import or display the prior workspace's internal GC-anchor refs. Those
        refs remain in ``.git`` until :meth:`gc` performs Jujutsu's normal backend cleanup.

        ``trunk`` is an optional branch name for the colocated ``.git``'s initial HEAD symref when
        colocating onto a directory with no pre-existing ``.git`` — so there is no leftover default
        branch ref (e.g. ``refs/heads/master``) to clean up. Ignored when a ``.git`` already exists
        (the adopt path) and for ``colocate=False``.

        ``hooks_config`` behaves like :meth:`load`'s: ``"auto"`` (default) reads
        ``<path>/.pyjutsu-hooks.toml`` when present, ``"off"`` never reads one, any other value is
        a config file path.
        """
        ws = cls(PyWorkspace.init(os.fspath(path), colocate, trunk))
        ws._load_hooks_config(hooks_config)
        return ws

    def gc(self, keep_newer: datetime | None = None) -> None:
        """Run Jujutsu backend garbage collection without publishing an operation.

        Objects created after ``keep_newer`` are preserved as protection against concurrent
        writers. ``None`` mirrors ``jj util gc`` from the pinned jj 0.44.0 CLI: preserve objects
        newer than two weeks. Pass an aware :class:`datetime.datetime` to choose another cutoff;
        ``datetime.now(timezone.utc)`` is equivalent to the CLI's ``--expire now``.

        Garbage collection also refreshes Jujutsu's internal Git keep-refs. After re-adopting a
        colocated repository whose ``.jj`` was deleted out of band, obsolete keep-refs remain
        invisible until this method removes them.
        """
        if keep_newer is None:
            keep_newer = datetime.now(timezone.utc) - timedelta(weeks=2)
        if keep_newer.tzinfo is None or keep_newer.utcoffset() is None:
            raise ValueError("keep_newer must be timezone-aware")
        self._handle.gc(keep_newer.timestamp())

    def add_workspace(
        self,
        path: str | os.PathLike[str],
        *,
        name: str | None = None,
        revisions: str | Revset | Sequence[str | Revset] | None = None,
        sparse_patterns: Literal["copy", "full", "empty"] = "copy",
    ) -> WorkspaceInfo:
        """Add a secondary workspace at ``path`` → its :class:`WorkspaceInfo` (``jj workspace add``).

        ``revisions`` accepts one revset, a sequence of revsets, or ``None``. Strings count as one
        revset. With ``None`` or an empty sequence, the new ``@`` uses the source ``@``'s parents.
        Explicit revsets must each resolve to one commit. Multiple parents use Jujutsu's merged-tree
        semantics and preserve conflicts. Pass ``"root()"`` to request the former Pyjutsu default.

        ``sparse_patterns`` matches the CLI choices. ``"copy"`` inherits the source patterns,
        ``"full"`` includes all paths, and ``"empty"`` includes none. Workspace registration and
        initial commit creation publish separate operations. A failure after registration raises
        :class:`~pyjutsu.errors.PartialWorkspaceError` with a recovery action.
        """
        if sparse_patterns not in {"copy", "full", "empty"}:
            raise ValueError("sparse_patterns must be 'copy', 'full', or 'empty'")
        return WorkspaceInfo.model_validate(
            self._handle.add_workspace(
                os.fspath(path),
                name,
                _normalize_revisions(revisions),
                sparse_patterns,
            )
        )

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

        Hooks: fires ``pre-import`` (veto before the import) and ``post-import``.
        """
        self._fire_pre("pre-import")
        row = self._handle.git_import()
        op = Operation.model_validate(row) if row is not None else None
        self._fire_post("post-import", op.id if op is not None else None, op)
        return op

    def git_export(self) -> Operation | None:
        """Export jj's bookmarks to the backing git repo's refs → the published :class:`Operation`,
        or ``None`` if nothing changed (no operation published).

        Matches ``jj git export``: writes each jj bookmark to its ``refs/heads/<name>`` git ref.
        Raises :class:`~pyjutsu.errors.GitError` listing any bookmark that failed to export.

        Hooks: fires ``pre-export`` (veto before the export) and ``post-export``.
        """
        self._fire_pre("pre-export")
        row = self._handle.git_export()
        op = Operation.model_validate(row) if row is not None else None
        self._fire_post("post-export", op.id if op is not None else None, op)
        return op

    def sync_colocated(self) -> Operation | None:
        """Repair the colocated git checkout — reset git ``HEAD`` (detached at ``@``'s parent) and
        the git index to match ``@``'s parent tree → the published :class:`Operation`, or ``None``
        if the view was already in sync (HEAD unchanged).

        The on-disk git index is rebuilt **unconditionally**, so a stale index that misled raw-git
        tooling (``git status`` / ``git check-ignore`` reporting a just-removed file) is repaired
        even when ``None`` is returned. Idempotent and ``@``-neutral: safe to call after any
        mutation. Requires a colocated git backend; raises :class:`~pyjutsu.errors.GitError`
        otherwise. This is the standalone form of the HEAD/index sync :meth:`git_export` also does.

        Hooks: fires ``pre-sync`` (veto before the repair) and ``post-sync``.
        """
        self._fire_pre("pre-sync")
        row = self._handle.sync_colocated()
        op = Operation.model_validate(row) if row is not None else None
        self._fire_post("post-sync", op.id if op is not None else None, op)
        return op

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

        Hooks: fires ``pre-fetch`` (veto before any network I/O) and ``post-fetch``.
        """
        self._fire_pre("pre-fetch", remote, bookmarks)
        row = self._handle.git_fetch(remote, bookmarks)
        op = Operation.model_validate(row) if row is not None else None
        self._fire_post("post-fetch", op.id if op is not None else None, op, remote)
        return op

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
        jj 0.44 (deletions need ``delete=True``).

        Raises :class:`~pyjutsu.errors.GitError` if no bookmark is given without a bulk mode (or one
        is given with a bulk mode), both ``all`` and ``tracked`` are set, ``delete`` is combined with
        a bulk mode, a (non-delete) named bookmark is missing/conflicted or new without
        ``allow_new``, a delete target has no remote ref, or the remote rejects the push. Force-push,
        ``--deleted``/``--change``/``-r <rev>`` selection remain out of scope.

        Hooks: fires ``pre-push`` (veto by raising :class:`~pyjutsu.errors.HookAbort`; nothing is
        pushed) before the push, and ``post-push`` after — a post-hook failure raises
        :class:`~pyjutsu.errors.PostHookError` carrying the published operation id (the push
        landed; only the hook failed).
        """
        if bookmark is None:
            names: list[str] = []
        elif isinstance(bookmark, str):
            names = [bookmark]
        else:
            names = list(bookmark)
        self._fire_pre("pre-push", remote, names)
        row = self._handle.git_push(remote, names, allow_new, delete, all, tracked)
        op = Operation.model_validate(row) if row is not None else None
        self._fire_post("post-push", op.id if op is not None else None, op, remote)
        return op

    def create_tag(
        self,
        name: str,
        target: str | Revset,
        message: str | None = None,
        *,
        force: bool = False,
    ) -> Operation | None:
        """Create tag ``name`` at the single commit named by ``target``.

        The default ``message=None`` creates a lightweight tag through jj-lib. A string ``message``
        keeps the annotated Git path available and emits :class:`DeprecationWarning`; use
        ``ws.git.create_tag``. Existing positional message arguments continue to work.

        ``force=False`` refuses to overwrite an existing tag. ``force=True`` replaces it.

        Requires a colocated git backend. Raises :class:`~pyjutsu.errors.RevsetError` unless
        ``target`` names exactly one revision, or :class:`~pyjutsu.errors.GitError` on a git-side
        failure (including a name clash when ``force=False``).
        """
        if message is not None:
            warnings.warn(
                "Workspace.create_tag(..., message=...) is deprecated; "
                "use ws.git.create_tag(...)",
                DeprecationWarning,
                stacklevel=2,
            )
            return self.git.create_tag(name, _revset_str(target), message, force=force)
        row = self._handle.create_tag(name, _revset_str(target), None, force)
        return Operation.model_validate(row) if row is not None else None

    def push_tag(self, name: str, remote: str) -> Operation | None:
        """Push tag ``name`` to git ``remote`` → the published :class:`Operation`, or ``None`` if
        the remote already has the tag at that target.

        An annotated tag retains its tag object. A lightweight tag points at the commit. Raises
        :class:`~pyjutsu.errors.GitError` if there is no local tag ``name``, the push is rejected,
        or the remote/tag is conflicted.
        """
        row = self._handle.push_tag(name, remote)
        return Operation.model_validate(row) if row is not None else None

    def git_refs(self, prefix: str = "refs/heads/") -> dict[str, str]:
        """Read the colocated git refs under ``prefix`` → ``{short_name: hex_oid}`` (prefix stripped).

        .. deprecated::
            Use :attr:`git` ``.refs(...)`` instead. This alias keeps working but emits
            :class:`DeprecationWarning`.
        """
        warnings.warn(
            "Workspace.git_refs is deprecated; use Workspace.git.refs()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.git.refs(prefix)

    def git_default_branch(self, remote: str) -> str | None:
        """The name of ``remote``'s default branch (what ``git remote show`` reports as ``HEAD``).

        ``None`` if the remote advertises none (the remote has no commits / an ambiguous default).
        Reads the remote's advertised HEAD via a ``git`` subprocess; raises
        :class:`~pyjutsu.errors.GitError` on an unknown remote or subprocess failure.
        """
        return self._handle.git_default_branch(remote)

    def tracked_ignored_paths(self) -> list[str]:
        """Paths tracked in ``@`` that the working-copy ``.gitignore`` would also ignore.

        Detects the tracked-but-ignored churn source (e.g. a committed ``.claude/settings.local.json``)
        that :meth:`untrack_paths` fixes. Intersects ``@``'s tracked tree with the working-copy ignore
        matcher (``.git/info/exclude`` + the repo-root ``.gitignore``) — no git subprocess. Returns
        repo-relative paths, sorted.
        """
        return list(self._handle.tracked_ignored_paths())

    def write_git_ref(self, name: str, target: str) -> None:
        """Force ``refs/heads/<name>`` to ``target`` (a commit oid) directly in the colocated ``.git``.

        .. deprecated::
            Use :attr:`git` ``.write_ref(...)`` instead. This alias keeps working but emits
            :class:`DeprecationWarning`.
        """
        warnings.warn(
            "Workspace.write_git_ref is deprecated; use Workspace.git.write_ref()",
            DeprecationWarning,
            stacklevel=2,
        )
        self.git.write_ref(name, target)

    def delete_git_ref(self, name: str) -> None:
        """Delete ``refs/heads/<name>`` directly in the colocated ``.git`` (reconcile-only escape
        hatch; see :meth:`write_git_ref`). No-op-safe if the ref is already absent.

        .. deprecated::
            Use :attr:`git` ``.delete_ref(...)`` instead. This alias keeps working but emits
            :class:`DeprecationWarning`.
        """
        warnings.warn(
            "Workspace.delete_git_ref is deprecated; use Workspace.git.delete_ref()",
            DeprecationWarning,
            stacklevel=2,
        )
        self.git.delete_ref(name)

    @classmethod
    def git_clone(
        cls,
        url: str,
        path: str | os.PathLike[str],
        *,
        colocate: bool = False,
        remote: str = "origin",
        hooks_config: str = "auto",
    ) -> Workspace:
        """Clone the git repo at ``url`` into a new jj workspace at ``path`` → a :class:`Workspace`.

        Matches ``jj git clone``. jj-lib has no clone primitive, so this composes existing verbs:
        :meth:`init` a fresh repo, :meth:`add_remote` ``remote`` → ``url``, then :meth:`git_fetch`
        the remote's bookmarks. If the remote advertises a default branch, ``@`` is set to a new
        empty commit on top of that branch's tip (so the clone is immediately usable); if discovery
        is ambiguous (no default branch advertised), ``@`` is left as the empty root child.

        Raises :class:`~pyjutsu.errors.WorkspaceError` if ``path`` already holds a repo, or
        :class:`~pyjutsu.errors.GitError` on a remote/fetch failure.

        ``hooks_config`` behaves like :meth:`load`'s: ``"auto"`` (default) reads
        ``<path>/.pyjutsu-hooks.toml`` when the clone contains one (so hook config travels with the
        repo, like ``.pre-commit-config.yaml``), ``"off"`` never reads one, any other value is a
        config file path.
        """
        # `jj git clone` creates the destination directory; `init` (like `jj git init`) needs it to
        # exist already, so create it here first.
        Path(path).mkdir(parents=True, exist_ok=True)
        ws = cls.init(path, colocate=colocate, hooks_config="off")  # config loads after the clone
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
        ws._load_hooks_config(hooks_config)
        return ws

    def remotes(self) -> list[Remote]:
        """The configured git remotes → their :class:`Remote` rows (``jj git remote list``).

        .. deprecated::
            Use :attr:`git` ``.remotes()`` instead. This alias keeps working but emits
            :class:`DeprecationWarning`.
        """
        warnings.warn(
            "Workspace.remotes is deprecated; use Workspace.git.remotes()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.git.remotes()

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
    def hooks(self) -> HookRegistry:
        """This workspace's hook registry — register pre/post-hook callbacks here.

        Events fired: ``pre-commit``/``post-commit`` (transaction commit), ``pre-push``/``post-push``
        (:meth:`git_push`), ``pre-fetch``/``post-fetch``, ``pre-import``/``post-import``,
        ``pre-export``/``post-export``, ``pre-sync``/``post-sync`` (:meth:`sync_colocated`),
        ``pre-snapshot``/``post-snapshot``, ``pre-untrack``/``post-untrack``,
        ``pre-undo``/``post-undo``, ``pre-restore``/``post-restore``. See :mod:`pyjutsu.hooks` for
        the declarative ``.pyjutsu-hooks.toml`` layer, the per-event call signatures, and the
        abort/post semantics.
        """
        return self._hooks

    def load_config_hooks(self, path: str | os.PathLike[str] | None = None) -> int:
        """Load a ``.pyjutsu-hooks.toml`` into this workspace's hook registry → hooks registered.

        ``path=None`` reads ``<workspace>/.pyjutsu-hooks.toml`` (raising if absent). Same parser
        :meth:`Workspace.load`'s ``hooks_config`` uses, for imperative setups. Returns the number
        of hooks registered.

        Config hooks register at load time, so they run **before** hooks added later imperatively.
        Re-loading **appends** (re-registering duplicates) — call ``ws.hooks.clear()`` first to
        reload a file cleanly after editing it.
        """
        if path is None:
            path = self.root / CONFIG_FILENAME
        return self._hooks.load_config(path)

    def _load_hooks_config(self, spec: str) -> None:
        if spec == "off":
            return
        path = self.root / CONFIG_FILENAME if spec == "auto" else Path(spec)
        if spec == "auto" and not path.is_file():
            return
        self._hooks.load_config(path)

    def _fire_pre(self, event: str, *args: object) -> None:
        """Run a ``pre-*`` hook set before an operation starts; any failure vetoes it."""
        try:
            self._hooks.fire(event, *args)
        except HookAbort:
            raise
        except Exception as e:
            raise HookAbort(f"{event} hook failed: {e}") from e

    def _fire_post(self, event: str, operation_id: str | None, *args: object) -> None:
        """Run a ``post-*`` hook set after an operation published.

        A failure raises :class:`PostHookError` (carrying the published operation id) — or, with
        ``ws.hooks.on_post_failure == "warn"``, surfaces as a :class:`UserWarning` and the
        operation's result is returned normally.
        """
        try:
            self._hooks.fire(event, *args)
        except Exception as e:
            if self._hooks.on_post_failure == "warn":
                warnings.warn(
                    f"{event} hook failed after the operation was published"
                    + (f" (operation {operation_id})" if operation_id else "")
                    + f": {e}",
                    stacklevel=2,
                )
                return
            raise PostHookError(
                operation_id,
                f"{event} hook failed after the operation was published: {e}",
            ) from e

    @property
    def name(self) -> str:
        """This workspace's name/id (e.g. ``"default"``)."""
        return self._handle.name()

    @property
    def root(self) -> Path:
        """The filesystem root of this workspace's working copy (canonicalized)."""
        return Path(self._handle.workspace_root())

    @property
    def git(self) -> GitView:
        """The git half of this colocated repository, under one namespace.

        Reads and writes the on-disk ``.git`` directly: refs (``refs()``,
        ``write_ref()``, ``delete_ref()``), remotes (``remotes()``), and — in later
        releases — annotated tags, configuration, ``HEAD``, worktrees, objects,
        submodules, the reflog, and the index. The jj-side git verbs
        (:meth:`git_import`, :meth:`git_export`, :meth:`sync_colocated`,
        :meth:`git_fetch`, :meth:`git_push`) stay here on :class:`Workspace`,
        because they publish jj operations.
        """
        if self._git is None:
            self._git = GitView(self._handle)
        return self._git

    def transaction(
        self,
        description: str,
        *,
        auto_snapshot: bool = True,
        ignore_immutable: bool = False,
    ) -> Transaction:
        """Open a write transaction committing as ``description`` (concept §4, M2).

        Use it as a context manager: the ``with`` block begins the transaction, publishes it on
        clean exit, and rolls it back on any exception (atomicity). At most one transaction may
        be open on a workspace at a time. A mutation transaction publishes exactly one jj
        operation::

            with ws.transaction("describe @") as tx:
                ...  # mutation methods arrive in later slices

        ``auto_snapshot`` (default ``True``) snapshots a dirty ``@`` as a separate preceding
        operation on open (matching the CLI); set it ``False`` to have the mutation see ``@`` as-is.

        ``ignore_immutable`` (default ``False``) temporarily bypasses configured
        ``immutable_heads()`` protection for this transaction. It never permits rewriting the
        root commit.
        """
        return Transaction(
            self._handle,
            description,
            auto_snapshot=auto_snapshot,
            ignore_immutable=ignore_immutable,
            hooks=self._hooks,
        )

    def snapshot(self) -> Operation | None:
        """Snapshot a dirty ``@`` as a separate ``snapshot working copy`` operation → that
        :class:`Operation`, or ``None`` if ``@`` was already clean (no operation published).

        This is what the ``jj`` CLI does automatically before each command; :meth:`transaction`
        does it for you on open when ``auto_snapshot`` is set. Raises
        :class:`~pyjutsu.errors.StaleWorkingCopyError` if ``@`` is stale.

        Hooks: fires ``pre-snapshot`` (veto before the snapshot) and ``post-snapshot``. The
        auto-snapshot inside :meth:`transaction` (``auto_snapshot=True``) is part of the tx
        lifecycle and does not fire these events.
        """
        self._fire_pre("pre-snapshot")
        row = self._handle.snapshot()
        op = Operation.model_validate(row) if row is not None else None
        self._fire_post("post-snapshot", op.id if op is not None else None, op)
        return op

    def untrack_paths(self, paths: list[str]) -> Operation | None:
        """Stop tracking each path in ``paths`` (and anything under it) → the published
        :class:`Operation`, or ``None`` if none of the paths were tracked (no operation published).

        Matches ``jj file untrack``: the path is removed from ``@``'s tree and its working-copy
        file-state is dropped, but **the file stays on disk**. Untracking is not durable on its own
        — the next :meth:`snapshot` re-adds the path unless it is excluded from tracking first. The
        intended path is to ``.gitignore`` it: jj evaluates gitignore before the
        ``snapshot.auto-track`` fileset, so an ignored, now-untracked path stays out. Raises
        :class:`~pyjutsu.errors.StaleWorkingCopyError` if ``@`` is stale.

        Hooks: fires ``pre-untrack`` (veto before the rewrite) and ``post-untrack``.
        """
        self._fire_pre("pre-untrack", paths)
        row = self._handle.untrack_paths(paths)
        op = Operation.model_validate(row) if row is not None else None
        self._fire_post("post-untrack", op.id if op is not None else None, op, paths)
        return op

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

        Hooks: fires ``pre-undo`` (veto before the undo) and ``post-undo``.
        """
        self._fire_pre("pre-undo", operation)
        op = Operation.model_validate(self._handle.undo(operation))
        self._fire_post("post-undo", op.id, op)
        return op

    def restore_operation(self, operation: str) -> Operation:
        """Reset the repo to the state a past operation recorded, publishing a new operation → that
        :class:`Operation`. ``operation`` is an op id, prefix, or expression (``"@-"``, …).

        Matches ``jj op restore``. If the restored state moves ``@``, the on-disk working copy is
        checked out to it.

        Hooks: fires ``pre-restore`` (veto before the restore) and ``post-restore``.
        """
        self._fire_pre("pre-restore", operation)
        op = Operation.model_validate(self._handle.restore_operation(operation))
        self._fire_post("post-restore", op.id, op)
        return op

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

    def diff_stat(self, revset: str | Revset, to: str | Revset | None = None) -> DiffStat:
        """The diff stat of a commit, or the range ``revset``→``to`` (delegates to a head view)."""
        return self.head().diff_stat(revset, to)

    def diff(self, revset: str | Revset, to: str | Revset | None = None) -> Diff:
        """The name-status diff of a commit, or the range ``revset``→``to`` (delegates to a head view)."""
        return self.head().diff(revset, to)

    def is_ancestor(self, ancestor: str | Revset, descendant: str | Revset) -> bool:
        """Whether ``ancestor`` is an ancestor of ``descendant`` (delegates to a head view)."""
        return self.head().is_ancestor(ancestor, descendant)

    def patch_id(self, revset: str | Revset) -> str:
        """A stable content identity for the change ``revset`` introduces (delegates to a head view)."""
        return self.head().patch_id(revset)

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
                # Snapshot the environment at call time (so JJ_CONFIG and friends flow through).
                env={**os.environ},
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
