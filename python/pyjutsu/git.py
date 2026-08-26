"""`GitView` — the git half of a colocated repository, under one namespace.

A colocated repository has a git side jj deliberately does not model: annotated
tags, git configuration, ``HEAD``, worktrees, submodules, the reflog, the
index. ``Workspace.git`` exposes that half here instead of scattering verbs
across ``Workspace``.

Native layout rule (concept §4): ``#[pymethods]`` stay flat on ``PyWorkspace``;
this class is the pure-Python namespace. Every method delegates to the same
flat handle the workspace owns, so a ``ws.git.*`` call and a ``ws.*`` call see
the same repo.

Reads publish no jj operation. The write verbs (``create_tag``, ``write_ref``,
``delete_ref``, and later ``config_set``/``set_head``) write git state that jj
does not own; callers reconcile it into jj's view with
:meth:`~pyjutsu.Workspace.git_import` / :meth:`~pyjutsu.Workspace.sync_colocated`.
"""

from __future__ import annotations

from .models import GitHead, GitSubmodule, GitTag, GitWorktree, Operation, Remote

__all__ = ["GitView"]


class GitView:
    """The git half of a colocated repository: refs, remotes, and (later) tags,
    config, HEAD, worktrees, objects, submodules, reflog, and index.

    Obtain one via :attr:`~pyjutsu.Workspace.git`. Every verb here reads or
    writes the on-disk ``.git`` directly; the jj-side verbs (``git_import``,
    ``git_export``, ``sync_colocated``, ``git_fetch``, ``git_push``) stay on
    :class:`~pyjutsu.Workspace`, because they publish jj operations.
    """

    __slots__ = ("_handle",)

    def __init__(self, handle: object) -> None:
        # Internal: construct via `Workspace.git`, never directly.
        self._handle = handle

    def refs(self, prefix: str = "refs/heads/") -> dict[str, str]:
        """Read the colocated git refs under ``prefix`` → ``{short_name: hex_oid}`` (prefix
        stripped).

        Reads the on-disk git refs directly — these can differ from jj's last-imported
        ``@git`` view, and *seeing that drift* is the point (so
        :meth:`~pyjutsu.RepoView.bookmarks` is not a substitute). Requires a colocated
        git backend. Values are commit oids (jj commit ids ARE the git oids in a
        colocated repo, so they compare directly to
        :attr:`~pyjutsu.Commit.commit_id`).
        """
        return self._handle.git_refs(prefix)

    def write_ref(self, name: str, target: str) -> None:
        """Force ``refs/heads/<name>`` to ``target`` (a commit oid) directly in the colocated
        ``.git``.

        A **reconcile-only escape hatch**: bypasses the jj view to repair colocated-ref
        drift when ``git_export`` is itself broken by a bad/leftover ref. Not a normal-path
        writer — for ordinary bookmark moves use a transaction + ``git_export``. The caller
        must re-import/``sync_colocated`` afterward to bring the write into jj's view.
        Requires a colocated git backend.
        """
        self._handle.write_git_ref(name, target)

    def delete_ref(self, name: str) -> None:
        """Delete ``refs/heads/<name>`` directly in the colocated ``.git`` (reconcile-only
        escape hatch; see :meth:`write_ref`). No-op-safe if the ref is already absent."""
        self._handle.delete_git_ref(name)

    def remotes(self) -> list[Remote]:
        """The configured git remotes → their :class:`~pyjutsu.Remote` rows.

        Each row carries the remote's name and **fetch** URL (``None`` if none is
        configured). Read-only; matches ``jj git remote list``.
        """
        return [Remote.model_validate(row) for row in self._handle.remotes()]

    def config_get(self, key: str) -> str | None:
        """The **effective** value of the git configuration ``key``, or ``None`` if nothing sets it.

        ``key`` is ``"section.key"`` or ``"section.subsection.key"`` (the subsection may itself
        contain dots, as in ``"remote.my.remote.url"``). The read is *effective*: it sees the
        merged configuration git itself would use — system, then global, then repository-local.
        That is the answer to "what is ``core.hooksPath`` in this repo". A key with no section
        raises :class:`~pyjutsu.errors.PyjutsuError`.

        Note the asymmetry with :meth:`config_set` and :meth:`config_unset`, which write the
        **repository-local** file only.
        """
        return self._handle.git_config_get(key)

    def config_set(self, key: str, value: str) -> None:
        """Set the git configuration ``key`` to ``value`` in the **repository-local** file.

        Never writes the user's global configuration. Publishes no jj operation — this is git
        state, not jj state. A key with no section raises
        :class:`~pyjutsu.errors.PyjutsuError`; a git-side failure raises
        :class:`~pyjutsu.errors.GitError`.
        """
        self._handle.git_config_set(key, value)

    def config_unset(self, key: str) -> None:
        """Remove the git configuration ``key`` from the **repository-local** file.

        A key that is not set locally is left alone rather than raising: "already absent" is
        what the caller asked for. A value inherited from the global or system configuration is
        untouched, so :meth:`config_get` may still return it afterwards.
        """
        self._handle.git_config_unset(key)

    def head(self) -> GitHead:
        """The colocated ``.git``'s ``HEAD`` → its :class:`~pyjutsu.GitHead`.

        ``name`` is the full ref name ``HEAD`` points at, exactly as ``git symbolic-ref HEAD``
        prints it, or ``None`` when detached. ``oid`` is ``None`` for an unborn branch. This
        reads git's ``HEAD``, not jj's ``@`` — in a colocated repo jj keeps ``HEAD`` detached at
        ``@``'s parent, and seeing that is the point.
        """
        return GitHead.model_validate(self._handle.git_head())

    def set_head(self, name: str) -> None:
        """Point ``HEAD`` at a branch symbolically (``git symbolic-ref HEAD refs/heads/<name>``).

        A bare ``"main"`` becomes ``refs/heads/main``; a name already starting with ``refs/`` is
        taken as written. The branch need not exist — that is how git models an unborn branch,
        and ``git symbolic-ref`` allows it too. gix validates the ref name; an invalid one raises
        :class:`~pyjutsu.errors.PyjutsuError`. Publishes no jj operation.

        Note that jj's own verbs move ``HEAD`` back: ``git_export`` and ``sync_colocated`` leave
        it detached at ``@``'s parent.
        """
        self._handle.git_set_head(name)

    def worktrees(self) -> list[GitWorktree]:
        """The colocated repository's git worktrees → their :class:`~pyjutsu.GitWorktree` rows.

        The repository's own worktree comes first (``main is True``), then the linked ones —
        the order ``git worktree list --porcelain`` uses. Read-only: this lists and reports
        state, it does not add, move, or prune anything.

        jj workspaces and git worktrees are different things sharing a directory, and they
        coexist badly; this is how you see the git half.
        """
        return [GitWorktree.model_validate(row) for row in self._handle.git_worktrees()]

    def object_type(self, oid: str) -> str | None:
        """The git object kind at ``oid`` — ``"commit"``, ``"tree"``, ``"blob"``, or ``"tag"`` —
        or ``None`` when no such object exists (``git cat-file -t``).

        ``oid`` is a full hex id in the repository's own object format: 40 characters for SHA-1,
        64 for SHA-256. A malformed or wrong-width id raises
        :class:`~pyjutsu.errors.PyjutsuError` rather than reporting ``None``, so a typo cannot
        look like a missing object.
        """
        return self._handle.git_object_type(oid)

    def exists(self, oid: str) -> bool:
        """Whether an object with ``oid`` is in the git object database."""
        return self._handle.git_object_exists(oid)

    def read_blob(self, oid: str) -> bytes:
        """The raw bytes of the blob at ``oid`` (``git cat-file -p`` on a blob).

        Deliberately narrow: a missing object, or one that is not a blob, raises
        :class:`~pyjutsu.errors.PyjutsuError`, so a caller cannot silently read a commit's
        serialized form. To read a file at a revision, use
        :meth:`~pyjutsu.RepoView.file_content` — that goes through jj's model and handles
        conflicts.
        """
        return self._handle.git_read_blob(oid)

    def submodules(self) -> list[GitSubmodule]:
        """The submodules declared in ``.gitmodules`` → their :class:`~pyjutsu.GitSubmodule` rows,
        sorted by name. Empty when the repository declares none.

        **Read-only.** jj has no submodule support — its submodule store is a stub — so a
        colocated repository with submodules is otherwise invisible to Pyjutsu. Listing and state
        only: update, init, and clone would mutate a working copy jj knows nothing about.
        """
        return [GitSubmodule.model_validate(row) for row in self._handle.git_submodules()]

    def create_tag(
        self,
        name: str,
        target: str,
        message: str,
        *,
        force: bool = False,
    ) -> Operation | None:
        """Create an **annotated** git tag ``name`` at the single commit named by ``target``.

        Writes a git tag object plus the ``refs/tags/<name>`` ref directly in the
        colocated ``.git``, then imports the new ref into jj's view, publishing one
        operation. ``force=False`` refuses to overwrite an existing tag;
        ``force=True`` replaces it. ``target`` must name exactly one revision
        (:class:`~pyjutsu.errors.RevsetError` otherwise); a git-side failure raises
        :class:`~pyjutsu.errors.GitError`.

        This is the annotated path of the old ``Workspace.create_tag(...,
        message=...)``. For a lightweight jj tag use
        :meth:`~pyjutsu.Workspace.create_tag` without a message.
        """
        row = self._handle.create_tag(name, target, message, force)
        return Operation.model_validate(row) if row is not None else None

    def tag(self, name: str) -> GitTag | None:
        """Read one tag by name from the on-disk git refs → its :class:`~pyjutsu.GitTag`, or
        ``None`` if no such ref exists.

        Reads ``refs/tags/<name>`` directly; the tag need not be imported into jj's
        view. ``annotated`` distinguishes a git tag object (message/tagger/date
        populated) from a lightweight tag (all null).
        """
        row = self._handle.git_tag(name)
        return GitTag.model_validate(row) if row is not None else None

    def tags(self) -> list[GitTag]:
        """Every tag in the on-disk git refs → its :class:`~pyjutsu.GitTag` rows, sorted by name.

        Read-only; equivalent to ``git for-each-ref refs/tags``. Requires a colocated
        git backend.
        """
        return [GitTag.model_validate(row) for row in self._handle.git_tags()]
