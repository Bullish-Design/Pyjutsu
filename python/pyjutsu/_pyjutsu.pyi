"""Type stub for the `_pyjutsu` native extension (the thin PyO3 layer).

The extension returns plain data; the pure-Python package validates/wraps it. Keep this stub
in sync with `src/lib.rs`.
"""

from __future__ import annotations

import os

class PyjutsuError(Exception):
    """Base class for errors raised by the native layer."""

def version() -> str:
    """Return the pinned jj-lib version this extension was built against (e.g. ``"0.38.0"``)."""

class PyWorkspace:
    """Opaque handle to one jj workspace (one working-copy path)."""

    @staticmethod
    def load(path: str | os.PathLike[str]) -> PyWorkspace: ...
    def name(self) -> str: ...
    def workspace_root(self) -> str: ...
    def working_copy(self) -> dict[str, str]: ...
