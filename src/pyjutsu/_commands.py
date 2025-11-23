# src/pyjutsu/_commands.py
"""Internal command execution wrapper using sh library."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pyjutsu.exceptions import JjCommandError, JjNotFoundError

try:  # pragma: no cover - environment dependent
    from sh import Command, ErrorReturnCode
    _sh_import_error: Exception | None = None
except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
    Command = None  # type: ignore[assignment]
    ErrorReturnCode = Exception  # type: ignore[assignment]
    _sh_import_error = exc


def check_jj_installed() -> None:
    """Verify jj is installed and accessible.

    Raises:
        JjNotFoundError: If jj is not installed or not callable
    """
    if _sh_import_error is not None:
        raise JjNotFoundError() from _sh_import_error

    assert Command is not None  # For type checkers
    try:
        jj = Command("jj")
        jj("--version")
    except Exception as exc:  # pragma: no cover - environment dependent
        raise JjNotFoundError() from exc


class JjCommand:
    """Wrapper around jj command using sh library."""

    def __init__(self, repo_path: Path) -> None:
        """Initialize command wrapper for repository.

        Args:
            repo_path: Path to jj repository

        Raises:
            JjNotFoundError: If jj is not installed
        """
        check_jj_installed()
        assert Command is not None  # For type checkers
        self.repo_path = repo_path
        self._jj = Command("jj").bake(_cwd=str(repo_path), _tty_out=False)

    def run(self, *args: str, **kwargs: Any) -> str:
        """Run jj command and return stdout.

        Args:
            *args: Command arguments
            **kwargs: Additional sh keyword arguments

        Returns:
            Command stdout as string

        Raises:
            JjCommandError: If command fails
        """
        assert Command is not None  # For type checkers
        try:
            result = self._jj(*args, **kwargs)
            return str(result).strip()
        except ErrorReturnCode as exc:  # type: ignore[misc]
            command = "jj " + " ".join(args)
            stdout = str(getattr(exc, "stdout", ""))
            stderr = str(getattr(exc, "stderr", ""))
            raise JjCommandError(
                command=command,
                returncode=getattr(exc, "exit_code", 1),
                stdout=stdout,
                stderr=stderr,
            ) from exc

    def run_lines(self, *args: str, **kwargs: Any) -> list[str]:
        """Run jj command and return stdout as lines.

        Args:
            *args: Command arguments
            **kwargs: Additional sh keyword arguments

        Returns:
            List of output lines (empty lines removed)

        Raises:
            JjCommandError: If command fails
        """
        output = self.run(*args, **kwargs)
        return [line for line in output.split("\n") if line.strip()]
