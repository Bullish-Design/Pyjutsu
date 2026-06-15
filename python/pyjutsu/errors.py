"""Pyjutsu exception hierarchy.

`PyjutsuError` is defined in the native `_pyjutsu` extension (so the Rust layer can raise it
directly when mapping `jj-lib` errors). The Python-side subclasses below give callers a
faithful, catchable taxonomy; the Rust layer will raise the specific subclasses as the
binding grows (concept §8.2). For now the native layer raises the `PyjutsuError` base.
"""

from __future__ import annotations

from ._pyjutsu import PyjutsuError

__all__ = [
    "PyjutsuError",
    "RevsetError",
    "ConflictError",
    "BackendError",
    "WorkspaceError",
]


class RevsetError(PyjutsuError):
    """A revset string failed to parse or evaluate."""


class ConflictError(PyjutsuError):
    """An operation could not proceed because of an unresolved conflict."""


class BackendError(PyjutsuError):
    """The underlying store/backend (git or native) reported an error."""


class WorkspaceError(PyjutsuError):
    """A workspace could not be loaded, or is in an unusable state (e.g. stale)."""
