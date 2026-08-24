"""Pyjutsu exception hierarchy.

The whole taxonomy is defined in the native `_pyjutsu` extension (concept §8.2) so the Rust
layer raises the precise subclass when it maps a `jj-lib` error. This module re-exports them
so callers can `from pyjutsu.errors import RevsetError` without reaching into the extension.
"""

from __future__ import annotations

from ._pyjutsu import (
    BackendError,
    ConflictError,
    GitError,
    ImmutableCommitError,
    PartialWorkspaceError,
    PyjutsuError,
    RevsetError,
    StaleWorkingCopyError,
    WorkingCopyError,
    WorkspaceError,
)

__all__ = [
    "PyjutsuError",
    "RevsetError",
    "ConflictError",
    "BackendError",
    "WorkspaceError",
    "PartialWorkspaceError",
    "WorkingCopyError",
    "StaleWorkingCopyError",
    "ImmutableCommitError",
    "GitError",
    "JjCliError",
    "HookAbort",
    "PostHookError",
]


class JjCliError(PyjutsuError):
    """The ``jj`` subprocess invoked by :meth:`pyjutsu.Workspace.run_jj` failed.

    Raised **only** by the ``run_jj`` escape hatch (never by the in-process typed surface): when the
    ``jj`` binary can't be found, or — under ``check=True`` — when it exits non-zero. Defined in pure
    Python (unlike the rest of the hierarchy, which the native layer raises) since the escape hatch
    is pure Python too.

    Attributes:
        command: the ``jj`` args that were run (without the leading ``jj``).
        returncode: the process exit code, or ``None`` if ``jj`` could not be launched.
        stdout: captured standard output (empty if ``jj`` could not be launched).
        stderr: captured standard error (or the launch error message).
    """

    def __init__(
        self,
        message: str,
        *,
        command: list[str],
        returncode: int | None,
        stdout: str,
        stderr: str,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class HookAbort(PyjutsuError):
    """A pre-hook vetoed (or failed) before its operation ran.

    Raised by the hook machinery when a registered ``pre-*`` hook raises: either the hook raises
    :class:`HookAbort` itself, or any other exception is wrapped in one (fail-closed, like git).
    For a transaction the pending transaction is rolled back (nothing is published) and the error
    propagates out of the ``with`` block; for a git verb (:meth:`~pyjutsu.Workspace.git_push`,
    :meth:`~pyjutsu.Workspace.git_fetch`) the operation is never started.
    """


class PostHookError(PyjutsuError):
    """A ``post-*`` hook failed *after* its operation was published.

    The operation is already in the op log (or, for git verbs, already on the remote) — this error
    says *the hook* failed, not the operation. Carries the published operation id so the caller
    can act on the landed op; a transaction that raised this did commit.

    Attributes:
        operation_id: the id of the published operation, or ``None`` if the event published none
            (e.g. a push that changed nothing).
    """

    def __init__(self, operation_id: str | None, message: str) -> None:
        super().__init__(message)
        self.operation_id = operation_id
