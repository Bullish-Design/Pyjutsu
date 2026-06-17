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
    GitError,
    ImmutableCommitError,
    PyjutsuError,
    RevsetError,
    StaleWorkingCopyError,
    WorkingCopyError,
    WorkspaceError,
)
from .models import (
    Bookmark,
    ChangeId,
    Commit,
    CommitId,
    Conflict,
    Diff,
    DiffStat,
    FileChange,
    FileStat,
    Operation,
    Remote,
    Signature,
    WorkspaceInfo,
)
from .repo_view import RepoView
from .transaction import Transaction
from .workspace import Workspace

#: This package's version. Pyjutsu is versioned on its own cadence, **independent** of the jj
#: version it binds — the bound jj-lib is pinned in ``Cargo.toml`` and the matching CLI in
#: ``devenv.nix`` (exposed at runtime as :data:`JJ_VERSION`).
__version__ = "0.5.0"

#: The jj-lib release this build targets. Bump alongside the ``Cargo.toml`` ``jj-lib`` pin and
#: the matching jj CLI in ``devenv.nix``; kept separate from :data:`__version__`.
JJ_LIB_TARGET = "0.38.0"

#: The jj / jj-lib version the compiled extension is actually linked against.
JJ_VERSION: str = _ext.version()

# Sanity tripwire for a broken/mixed build: the linked jj-lib must be the release this build
# targets. This is independent of pyjutsu's own version.
if JJ_VERSION != JJ_LIB_TARGET:
    raise PyjutsuError(
        f"broken build: pyjutsu {__version__} targets jj-lib {JJ_LIB_TARGET} "
        f"but the extension links jj-lib {JJ_VERSION}"
    )

__all__ = [
    "Workspace",
    "Transaction",
    "RepoView",
    "Commit",
    "Signature",
    "Operation",
    "Bookmark",
    "WorkspaceInfo",
    "Remote",
    "Conflict",
    "Diff",
    "DiffStat",
    "FileChange",
    "FileStat",
    "ChangeId",
    "CommitId",
    "JJ_LIB_TARGET",
    "PyjutsuError",
    "RevsetError",
    "ConflictError",
    "BackendError",
    "WorkspaceError",
    "WorkingCopyError",
    "StaleWorkingCopyError",
    "ImmutableCommitError",
    "GitError",
    "JJ_VERSION",
    "__version__",
]
