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
    "WorkingCopyError",
    "StaleWorkingCopyError",
    "ImmutableCommitError",
    "GitError",
    "JjCliError",
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
