"""The build gate: the native ext links the pinned jj-lib, independent of pyjutsu's own version."""

from __future__ import annotations

import pyjutsu
from pyjutsu import _pyjutsu as ext


def test_extension_links_jj_lib_0_38() -> None:
    assert ext.version() == "0.38.0"


def test_linked_jj_lib_matches_target() -> None:
    # The broken-build tripwire: the linked jj-lib equals the release's target (decoupled from
    # pyjutsu's own version, which moves on its own cadence).
    assert pyjutsu.JJ_VERSION == pyjutsu.JJ_LIB_TARGET == "0.38.0"
