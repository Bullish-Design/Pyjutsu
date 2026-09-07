"""The build gate: the native ext links the pinned jj-lib, independent of pyjutsu's own version."""

from __future__ import annotations

import pathlib
import tomllib

import pyjutsu
from pyjutsu import _pyjutsu as ext


def test_extension_links_jj_lib_0_44() -> None:
    # `version()` is build-derived (build.rs parses Cargo.lock), so this asserts the resolved pin.
    assert ext.version() == "0.44.0"


def test_linked_jj_lib_matches_target() -> None:
    # `JJ_LIB_TARGET` is now an alias of the build-derived `JJ_VERSION` (no second hand-maintained
    # copy); both equal the resolved jj-lib pin.
    assert pyjutsu.JJ_VERSION == pyjutsu.JJ_LIB_TARGET == "0.44.0"


def test_pyjutsu_version_matches_extension() -> None:
    # The stale-build invariant (project 10 §P3): the Python package version must equal the compiled
    # extension's `pyjutsu_version()`. If a bump to `__version__` lands without `maturin develop`,
    # importing pyjutsu raises at module load — so reaching this assertion already proves the two
    # agree; we also pin the current release value.
    assert ext.pyjutsu_version() == pyjutsu.__version__ == "0.21.1"


def _manifest_version(relative: str, table: str) -> str:
    manifest = tomllib.loads((pathlib.Path(__file__).parents[1] / relative).read_text())
    return str(manifest[table]["version"])


def test_every_manifest_agrees_on_the_version() -> None:
    # The version is hand-maintained in four places: Cargo.toml, pyproject.toml, __init__.py and
    # the assertion above. A bump that misses one used to reach a published release — 0.21.0 was
    # cut with __version__ still at 0.20.0, so the wheel raised on import and the sdist was
    # unbuildable. Compare the files directly, because the two tests above both read the *built*
    # extension and therefore agree with each other while disagreeing with the manifests.
    assert _manifest_version("Cargo.toml", "package") == _manifest_version("pyproject.toml", "project")
    assert pyjutsu.__version__ == _manifest_version("pyproject.toml", "project")
