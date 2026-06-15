"""Pyjutsu — a Pythonic + Pydantic binding to jujutsu's jj-lib engine (via PyO3/maturin).

Public surface (M0): :class:`Workspace`, the :class:`Commit` model and id types, and the
:class:`PyjutsuError` hierarchy. Reads, transactions, the op log, workspaces, and git interop
arrive in M1–M3 (see ``docs/PYJUTSU_CONCEPT.md``).
"""

from __future__ import annotations

from . import _pyjutsu as _ext
from .errors import (
    BackendError,
    ConflictError,
    PyjutsuError,
    RevsetError,
    WorkspaceError,
)
from .models import (
    Bookmark,
    ChangeId,
    Commit,
    CommitId,
    Conflict,
    DiffStat,
    FileStat,
    Operation,
    Signature,
)
from .repo_view import RepoView
from .workspace import Workspace

#: This package's version. Encodes the jj it targets: ``pyjutsu X.Y.*`` binds jj ``X.Y``.
__version__ = "0.38.0"

#: The jj / jj-lib version the compiled extension is linked against (concept §6).
JJ_VERSION: str = _ext.version()

# Enforce the version contract at import: the built extension's jj-lib must match this
# package's major.minor. A mismatch means a broken/mixed build.
if JJ_VERSION.rsplit(".", 1)[0] != __version__.rsplit(".", 1)[0]:
    raise PyjutsuError(
        f"version mismatch: pyjutsu {__version__} was built against jj-lib {JJ_VERSION}"
    )

__all__ = [
    "Workspace",
    "RepoView",
    "Commit",
    "Signature",
    "Operation",
    "Bookmark",
    "Conflict",
    "DiffStat",
    "FileStat",
    "ChangeId",
    "CommitId",
    "PyjutsuError",
    "RevsetError",
    "ConflictError",
    "BackendError",
    "WorkspaceError",
    "JJ_VERSION",
    "__version__",
]
