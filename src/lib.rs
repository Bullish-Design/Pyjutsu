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

/// The pinned `jj-lib` version this extension is built against. Mirrors `Cargo.toml`'s
/// `jj-lib = "=0.42.0"`; the Python layer checks this against its `JJ_LIB_TARGET` as a
/// broken-build tripwire (independent of pyjutsu's own version).
const JJ_LIB_VERSION: &str = "0.42.0";

/// Return the pinned `jj-lib` version. Proves the native ext imports and links jj-lib.
#[pyfunction]
fn version() -> &'static str {
    JJ_LIB_VERSION
}

#[pymodule]
fn _pyjutsu(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
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
        // Guards the jj-lib pin at the Rust layer (mirrors Cargo.toml's `jj-lib = "=0.42.0"`).
        assert_eq!(version(), "0.42.0");
    }
}
