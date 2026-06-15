"""The `Workspace` facade — Pyjutsu's main entry point.

A `Workspace` wraps one opaque native handle (one working-copy path); the repo behind it is
shared (concept §11). M0 exposes loading + reading `@`; reads/transactions/op-log/git follow
in M1–M3.
"""

from __future__ import annotations

import os
from pathlib import Path

from ._pyjutsu import PyWorkspace
from .models import Commit

__all__ = ["Workspace"]


class Workspace:
    """A loaded jj workspace bound to a single working-copy path."""

    __slots__ = ("_handle",)

    def __init__(self, handle: PyWorkspace) -> None:
        # Internal: construct via `Workspace.load(...)`, not directly.
        self._handle = handle

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Workspace:
        """Load the workspace whose working copy is rooted at ``path``."""
        return cls(PyWorkspace.load(os.fspath(path)))

    @property
    def name(self) -> str:
        """This workspace's name/id (e.g. ``"default"``)."""
        return self._handle.name()

    @property
    def root(self) -> Path:
        """The filesystem root of this workspace's working copy (canonicalized)."""
        return Path(self._handle.workspace_root())

    def working_copy(self) -> Commit:
        """Read ``@`` — this workspace's working-copy commit. Read-only (no snapshot)."""
        return Commit.model_validate(self._handle.working_copy())

    def __repr__(self) -> str:
        return f"Workspace(name={self.name!r}, root={str(self.root)!r})"
