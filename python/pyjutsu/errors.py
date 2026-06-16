"""Pyjutsu exception hierarchy.

The whole taxonomy is defined in the native `_pyjutsu` extension (concept §8.2) so the Rust
layer raises the precise subclass when it maps a `jj-lib` error. This module re-exports them
so callers can `from pyjutsu.errors import RevsetError` without reaching into the extension.
"""

from __future__ import annotations

from ._pyjutsu import (
    BackendError,
    ConflictError,
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
]
