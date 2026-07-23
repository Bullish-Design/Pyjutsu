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
    JjCliError,
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
    Hunk,
    HunkLine,
    JjResult,
    MergeResult,
    Operation,
    Remote,
    Signature,
    WorkspaceInfo,
)
from .repo_view import RepoView
from .revset import Pattern, Revset
from .transaction import Transaction
from .workspace import Workspace

#: This package's version. Pyjutsu is versioned on its own cadence, **independent** of the jj
#: version it binds — the bound jj-lib is pinned in ``Cargo.toml`` and the matching CLI in
#: ``devenv.nix`` (exposed at runtime as :data:`JJ_VERSION`). This is the one hand-maintained
#: version string; the guard below checks it against the compiled extension so a bump here without
#: a rebuild fails loudly instead of silently mismatching.
__version__ = "0.12.0"

#: The jj / jj-lib version the compiled extension is actually linked against. **Build-derived**
#: (``build.rs`` reads the resolved ``Cargo.lock``), so it cannot drift from the linked dependency.
JJ_VERSION: str = _ext.version()

#: The jj-lib release this build targets. No longer a separate hand-maintained constant — it is an
#: alias of the build-derived :data:`JJ_VERSION` (project 10 §P3: no two hand-maintained copies of
#: the same number). Kept for back-compat with consumers that read it.
JJ_LIB_TARGET: str = JJ_VERSION

# Stale-build tripwire: the installed Python package must match the compiled `.so`. During an
# editable-install workflow the Python metadata can be bumped before `maturin develop` rebuilds the
# extension; that mismatch is a genuinely stale build, and this catches it with a clear "rebuild"
# message instead of shipping a Python/native version skew. A correctly rebuilt tree imports clean.
_EXT_PYJUTSU_VERSION: str = _ext.pyjutsu_version()
if __version__ != _EXT_PYJUTSU_VERSION:
    raise PyjutsuError(
        f"stale build: pyjutsu {__version__} (Python package) does not match the compiled "
        f"extension {_EXT_PYJUTSU_VERSION}; rebuild the extension (`maturin develop`)"
    )

__all__ = [
    "Workspace",
    "Transaction",
    "RepoView",
    "Revset",
    "Pattern",
    "Commit",
    "Signature",
    "Operation",
    "Bookmark",
    "WorkspaceInfo",
    "Remote",
    "Conflict",
    "MergeResult",
    "Diff",
    "DiffStat",
    "FileChange",
    "FileStat",
    "Hunk",
    "HunkLine",
    "JjResult",
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
    "JjCliError",
    "JJ_VERSION",
    "__version__",
]
