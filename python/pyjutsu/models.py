"""Pydantic v2 models for the data Pyjutsu reads out of jj-lib.

The native `_pyjutsu` layer returns plain dicts; these models validate them at the FFI
boundary (concept §4), which also acts as a drift tripwire against jj-lib changes. M0 ships
the minimal `Commit`; the shape grows in M1 (signatures, parents, bookmarks, conflicts).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

#: A jujutsu **change id**, rendered in jj's canonical "reverse hex" (the z-k letter form
#: shown by `jj log` and typed by users), e.g. ``"qpvnssupmzyvwwwpnqusoyrntswmylyp"``.
ChangeId = Annotated[str, StringConstraints(pattern=r"^[k-z]+$", min_length=1)]

#: A jujutsu **commit id**, plain lowercase hex (git-style), e.g. ``"cc2a471e..."``.
CommitId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]+$", min_length=1)]


class Commit(BaseModel):
    """An immutable jj commit. ``change_id`` is stable across rewrites; ``commit_id`` is not."""

    # Immutable value object; forbid unexpected keys so a Rust/Python shape mismatch fails loudly.
    model_config = ConfigDict(frozen=True, extra="forbid")

    change_id: ChangeId
    commit_id: CommitId
    description: str
