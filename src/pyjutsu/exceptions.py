# src/pyjutsu/exceptions.py
"""Exception classes for Pyjutsu."""

from __future__ import annotations


class PyjutsuError(Exception):
    """Base exception for all Pyjutsu errors."""


class JjNotFoundError(PyjutsuError):
    """Raised when jj executable is not found in PATH."""

    def __init__(self, message: str = "jj executable not found in PATH") -> None:
        super().__init__(message)


class JjCommandError(PyjutsuError):
    """Raised when a jj command fails."""

    def __init__(
        self,
        command: str,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        message = f"Command '{command}' failed with exit code {returncode}"
        if stderr:
            message += f"\nstderr: {stderr}"
        super().__init__(message)


class RepositoryNotFoundError(PyjutsuError):
    """Raised when path is not a jj repository."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Not a jj repository: {path}")


class ConflictError(PyjutsuError):
    """Raised when repository has unresolved conflicts."""

    def __init__(self, message: str = "Repository has unresolved conflicts") -> None:
        super().__init__(message)


class InvalidRevisionError(PyjutsuError):
    """Raised when a revision specifier is invalid."""

    def __init__(self, revision: str) -> None:
        self.revision = revision
        super().__init__(f"Invalid revision: {revision}")
