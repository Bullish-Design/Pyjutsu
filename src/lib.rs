//! `_pyjutsu` — the thin PyO3 native extension binding jujutsu's `jj-lib` engine.
//!
//! Design rule (concept §4): this layer stays thin and dumb. It exposes opaque handles
//! (`PyWorkspace`, `PyRepoView`) and **plain Python data** (dicts/lists/strings) only — never
//! `jj-lib` types — and holds no business logic. All ergonomics, Pydantic modeling, defaults,
//! and the public contract live in the pure-Python `pyjutsu` package, which validates the
//! plain data at the boundary.
//!
//! Errors from `jj-lib` are mapped to the `PyjutsuError` hierarchy (concept §8.2). PyO3 wraps
//! `#[pymethods]` bodies in `catch_unwind`, so a panic surfaces as a Python exception rather
//! than aborting the process.

mod convert;
mod diff;
mod diff_stat;
mod errors;
mod repo_view;
mod revset;
mod transaction;
mod workspace;

use pyo3::prelude::*;

use repo_view::{PyCommitStream, PyRepoView};
use transaction::PyTransaction;
use workspace::PyWorkspace;

/// The `jj-lib` version this extension is built against, **derived at build time** from the
/// resolved `Cargo.lock` by `build.rs` (emitted as `PYJUTSU_JJ_LIB_VERSION`). Because it comes
/// from the lock file cargo actually resolved, it cannot drift from the linked dependency — it is
/// a build-derived fact, not a hand-maintained copy (project 10 §P3).
const JJ_LIB_VERSION: &str = env!("PYJUTSU_JJ_LIB_VERSION");

/// pyjutsu's *own* crate version, baked in by cargo at compile time (`CARGO_PKG_VERSION`, i.e.
/// `Cargo.toml`'s `version`). The Python layer guards its `__version__` against this to catch a
/// stale compiled extension (a version bump not followed by a rebuild) — see `pyjutsu_version`.
const PYJUTSU_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Return the build-derived `jj-lib` version. Proves the native ext imports and links jj-lib, and
/// reports the exact dependency version that was compiled in.
#[pyfunction]
fn version() -> &'static str {
    JJ_LIB_VERSION
}

/// Return the compiled pyjutsu crate version. The Python package compares its hand-maintained
/// `__version__` against this so a forgotten rebuild after a version bump surfaces as a clear
/// "stale build" error instead of silently shipping a mismatched extension.
#[pyfunction]
fn pyjutsu_version() -> &'static str {
    PYJUTSU_VERSION
}

#[pymodule]
fn _pyjutsu(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(pyjutsu_version, m)?)?;
    m.add_class::<PyWorkspace>()?;
    m.add_class::<PyRepoView>()?;
    m.add_class::<PyCommitStream>()?;
    m.add_class::<PyTransaction>()?;
    errors::register(m)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_is_pinned() {
        // The build-derived jj-lib version (from Cargo.lock via build.rs) must equal the Cargo pin
        // (`jj-lib = "=0.42.0"`). This asserts build.rs parsed the lock correctly, not a second
        // hand-maintained copy of the number.
        assert_eq!(version(), "0.42.0");
    }

    #[test]
    fn pyjutsu_version_matches_crate() {
        // The compiled pyjutsu version is cargo's own `CARGO_PKG_VERSION`; the Python guard leans
        // on this to detect a stale extension after a version bump.
        assert_eq!(pyjutsu_version(), env!("CARGO_PKG_VERSION"));
    }
}
