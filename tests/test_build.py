"""The build gate: the native ext links the pinned jj-lib, independent of pyjutsu's own version."""

from __future__ import annotations

import pyjutsu
from pyjutsu import _pyjutsu as ext


def test_extension_links_jj_lib_0_42() -> None:
    # `version()` is build-derived (build.rs parses Cargo.lock), so this asserts the resolved pin.
    assert ext.version() == "0.42.0"


def test_linked_jj_lib_matches_target() -> None:
    # `JJ_LIB_TARGET` is now an alias of the build-derived `JJ_VERSION` (no second hand-maintained
    # copy); both equal the resolved jj-lib pin.
    assert pyjutsu.JJ_VERSION == pyjutsu.JJ_LIB_TARGET == "0.42.0"


def test_pyjutsu_version_matches_extension() -> None:
    # The stale-build invariant (project 10 §P3): the Python package version must equal the compiled
    # extension's `pyjutsu_version()`. If a bump to `__version__` lands without `maturin develop`,
    # importing pyjutsu raises at module load — so reaching this assertion already proves the two
    # agree; we also pin the current release value.
    assert ext.pyjutsu_version() == pyjutsu.__version__ == "0.12.1"
