"""The build gate: the native ext links the pinned jj-lib and honors the version contract."""

from __future__ import annotations

import pyjutsu
from pyjutsu import _pyjutsu as ext


def test_extension_links_jj_lib_0_38() -> None:
    assert ext.version() == "0.38.0"


def test_version_contract() -> None:
    # pyjutsu X.Y.* binds jj X.Y (concept §6).
    assert pyjutsu.JJ_VERSION == "0.38.0"
    assert pyjutsu.__version__.startswith("0.38")
