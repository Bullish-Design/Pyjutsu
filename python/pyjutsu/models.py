"""Pydantic v2 models for the data Pyjutsu reads out of jj-lib.

The native `_pyjutsu` layer returns plain dicts; these models validate them at the FFI
boundary (concept §4), which also acts as a drift tripwire against jj-lib changes
(``extra="forbid"`` — an unexpected key fails loudly).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

#: A jujutsu **change id**, rendered in jj's canonical "reverse hex" (the z-k letter form
#: shown by `jj log` and typed by users), e.g. ``"qpvnssupmzyvwwwpnqusoyrntswmylyp"``.
ChangeId = Annotated[str, StringConstraints(pattern=r"^[k-z]+$", min_length=1)]

#: A jujutsu **commit id**, plain lowercase hex (git-style), e.g. ``"cc2a471e..."``.
CommitId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]+$", min_length=1)]


class Signature(BaseModel):
    """A jj author/committer signature: who, and when (tz-aware)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    email: str
    #: Tz-aware instant, built from jj's raw `(timestamp_ms, tz_offset_minutes)`.
    timestamp: datetime

    @model_validator(mode="before")
    @classmethod
    def _build_timestamp(cls, data: object) -> object:
        # Convert the native layer's raw `{timestamp_ms, tz_offset_minutes}` into a tz-aware
        # datetime, preserving jj's recorded offset (don't normalize to UTC).
        if isinstance(data, dict) and "timestamp" not in data and "timestamp_ms" in data:
            data = dict(data)
            ms = data.pop("timestamp_ms")
            offset = timedelta(minutes=data.pop("tz_offset_minutes"))
            data["timestamp"] = datetime.fromtimestamp(ms / 1000, tz=timezone(offset))
        return data


class Commit(BaseModel):
    """An immutable jj commit. ``change_id`` is stable across rewrites; ``commit_id`` is not."""

    # Immutable value object; forbid unexpected keys so a Rust/Python shape mismatch fails loudly.
    model_config = ConfigDict(frozen=True, extra="forbid")

    change_id: ChangeId
    commit_id: CommitId
    description: str
    author: Signature
    committer: Signature
    #: Parent commit ids (a merge has more than one; the root commit has none).
    parent_ids: list[CommitId]
    #: True if the commit makes no change to its parent tree.
    is_empty: bool
    #: True if the commit's tree contains a conflict (jj's first-class conflicts).
    has_conflict: bool
    #: Names of local bookmarks pointing at this commit (sorted).
    bookmarks: list[str]


class FileStat(BaseModel):
    """Per-file line counts within a :class:`DiffStat`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    insertions: int
    deletions: int


class DiffStat(BaseModel):
    """A commit's diff stat vs its parent(s): per-file counts and summed totals.

    Symlinks, submodules, conflicts, and binary files are listed with zero line counts (they
    don't contribute to the totals), matching ``jj diff --stat``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    files: list[FileStat]
    total_insertions: int
    total_deletions: int


class HunkLine(BaseModel):
    """One line within a :class:`Hunk`. ``added`` lines exist only on the new side, ``removed``
    only on the old; ``content`` keeps its trailing newline (lossy-utf8 decoded).

    Pyjutsu emits hunks with no surrounding context (see :class:`Hunk`), so a line is only ever
    ``added`` or ``removed`` — there is no ``context`` kind.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["added", "removed"]
    content: str


class Hunk(BaseModel):
    """A contiguous changed span of a text file, with 1-based old/new line ranges.

    Pyjutsu groups one hunk per changed span with **no surrounding context** (so ``lines`` holds
    only ``added``/``removed`` lines). This is a faithful structured diff but not a byte-exact
    ``@@`` unified-diff header; git-style 3-line-context grouping is intentionally not emitted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[HunkLine]


class FileChange(BaseModel):
    """One changed path in a :class:`Diff`: how it changed, and its content hunks.

    ``kind`` mirrors jj's name-status: ``added`` / ``modified`` / ``removed``, plus
    ``type_changed`` when a path switches entry kind (e.g. file↔symlink) — jj's ``--summary``
    renders that as a plain ``M``, but the binding distinguishes it. An executable-bit-only edit
    stays ``modified``. A delete+add pair the backend recognizes as a move is ``renamed`` (or
    ``copied`` if the source survives), with ``source`` set to the origin ``path``; jj detects
    renames but not similarity-based copies, so ``copied`` is rare.

    ``binary`` is ``True`` for a non-line-diffable file (binary, symlink, submodule, or
    conflict); such files carry no ``hunks``, matching how :class:`DiffStat` lists them with zero
    counts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    kind: Literal["added", "modified", "removed", "type_changed", "renamed", "copied"]
    binary: bool = False
    hunks: list[Hunk] = []
    #: The origin path for a ``renamed``/``copied`` change; ``None`` otherwise.
    source: str | None = None


class Diff(BaseModel):
    """A commit's name-status diff vs its parent(s): one :class:`FileChange` per changed path.

    The diff stream never yields unchanged or pure-directory paths, so every entry is a real
    file-level change. Same single-commit-vs-parent framing as :class:`DiffStat`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    files: list[FileChange]


class Conflict(BaseModel):
    """A conflicted path in a commit's tree. jj conflicts are first-class and N-sided (concept §8.9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    #: Number of positive (add) terms in the conflict's merge.
    num_sides: int
    #: Number of negative (base/remove) terms. A regular 3-way conflict is 2 sides / 1 base.
    num_bases: int


class Bookmark(BaseModel):
    """A jj bookmark (jj's named pointer). One per local bookmark and per remote-tracking ref."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    #: ``None`` for a local bookmark; the remote name for a remote-tracking ref.
    remote: str | None
    #: Commit ids the bookmark points at — more than one means a conflicted bookmark.
    target_ids: list[CommitId]
    #: Whether this is a remote-tracking ref jj merges into the local. Tracking is a remote-ref
    #: property, so local rows are always ``False`` (matches jj's ``tracked`` template keyword).
    tracked: bool

    @property
    def conflicted(self) -> bool:
        """True if the bookmark points at more than one commit (an unresolved bookmark conflict)."""
        return len(self.target_ids) > 1


class WorkspaceInfo(BaseModel):
    """A workspace tracked in the repo: its name, on-disk root, and current ``@`` commit id.

    jj's headline feature over git is multiple working copies sharing one repo (concept §124); each
    is a *workspace* with its own ``@``. ``default`` is the primary one created at ``jj git init``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    #: Absolute path to the workspace's working-copy root; ``None`` if not recorded in the store.
    path: str | None
    #: The commit id of this workspace's ``@`` (working-copy commit).
    wc_commit_id: CommitId


class Remote(BaseModel):
    """A configured git remote: its name and **fetch** URL (``jj git remote list``).

    jj stores its history in a git backend (concept §134); a *remote* is a named URL jj can later
    fetch from / push to. ``url`` is the fetch URL; a remote's push URL is available in jj but out
    of scope here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    #: The remote's fetch URL; ``None`` if the remote has no fetch URL configured.
    url: str | None


class JjResult(BaseModel):
    """The captured result of a ``jj`` subprocess run by :meth:`pyjutsu.Workspace.run_jj`.

    This is the escape hatch's return value — **raw** text and exit code, parsed into no further
    structure (that is the point: leaving the typed in-process surface, not re-entering it).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The ``jj`` args that were run (without the leading ``jj``).
    args: list[str]
    #: The process exit code (``0`` on success).
    returncode: int
    stdout: str
    stderr: str


class Operation(BaseModel):
    """One entry in jj's operation log — an atomic change to the repo state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    #: Parent operation ids (more than one indicates a merge of divergent operations).
    parent_ids: list[str]
    description: str
    hostname: str
    username: str
    #: True if this operation is a pure working-copy snapshot.
    is_snapshot: bool
    tags: dict[str, str]
    #: When the operation started / finished (tz-aware).
    start_time: datetime
    end_time: datetime

    @model_validator(mode="before")
    @classmethod
    def _build_times(cls, data: object) -> object:
        # Assemble tz-aware start/end datetimes from the native layer's raw ms + offset pairs.
        if isinstance(data, dict) and "start_ms" in data:
            data = dict(data)
            for prefix in ("start", "end"):
                ms = data.pop(f"{prefix}_ms")
                offset = timedelta(minutes=data.pop(f"{prefix}_tz_offset_minutes"))
                data[f"{prefix}_time"] = datetime.fromtimestamp(ms / 1000, tz=timezone(offset))
        return data
