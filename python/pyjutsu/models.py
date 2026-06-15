"""Pydantic v2 models for the data Pyjutsu reads out of jj-lib.

The native `_pyjutsu` layer returns plain dicts; these models validate them at the FFI
boundary (concept §4), which also acts as a drift tripwire against jj-lib changes
(``extra="forbid"`` — an unexpected key fails loudly).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

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
