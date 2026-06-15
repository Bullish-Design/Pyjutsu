//! `_pyjutsu` — the thin PyO3 native extension binding jujutsu's `jj-lib` engine.
//!
//! Design rule (concept §4): this layer stays thin and dumb. It exposes opaque handles
//! (`PyWorkspace`) and **plain Python data** (dicts/strings) only — never `jj-lib` types —
//! and holds no business logic. All ergonomics, Pydantic modeling, and the public contract
//! live in the pure-Python `pyjutsu` package, which validates the plain data at the boundary.
//!
//! Errors from `jj-lib` are mapped to `PyjutsuError` (concept §8.2). PyO3 already wraps
//! `#[pymethods]` bodies in `catch_unwind`, so a panic surfaces as a Python exception rather
//! than aborting the process.

use std::path::PathBuf;
use std::sync::Mutex;

use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use jj_lib::commit::Commit;
use jj_lib::config::StackedConfig;
use jj_lib::object_id::ObjectId;
use jj_lib::repo::{Repo, StoreFactories};
use jj_lib::settings::UserSettings;
use jj_lib::workspace::{Workspace, default_working_copy_factories};

create_exception!(
    _pyjutsu,
    PyjutsuError,
    PyException,
    "Base class for errors raised by the pyjutsu native layer."
);

/// The pinned `jj-lib` version this extension is built against. Mirrors `Cargo.toml`'s
/// `jj-lib = "=0.38.0"`; the Python layer asserts the version contract against it.
const JJ_LIB_VERSION: &str = "0.38.0";

/// Map any displayable `jj-lib` error into a `PyjutsuError`. The thin layer never leaks the
/// concrete jj-lib error type; the message is preserved for diagnostics.
fn to_py_err<E: std::fmt::Display>(err: E) -> PyErr {
    PyjutsuError::new_err(err.to_string())
}

/// Return the pinned `jj-lib` version. Proves the native ext imports and links jj-lib.
#[pyfunction]
fn version() -> &'static str {
    JJ_LIB_VERSION
}

/// Opaque, `Send` handle to a single jj workspace (one working-copy path). The shared repo
/// behind it is `Arc`-loaded via the workspace's `RepoLoader`.
///
/// `jj_lib::Workspace` is `Send` (its `WorkingCopy` is `Any + Send`) but not `Sync` — so it's
/// held behind a `Mutex` (concept §8.4) to satisfy PyO3's `Sync` requirement for `#[pyclass]`
/// and to serialize the path-affine working copy across threads.
#[pyclass(module = "pyjutsu._pyjutsu")]
struct PyWorkspace {
    inner: Mutex<Workspace>,
}

impl PyWorkspace {
    /// Lock the inner workspace, mapping a poisoned lock to a `PyjutsuError`.
    fn locked(&self) -> PyResult<std::sync::MutexGuard<'_, Workspace>> {
        self.inner
            .lock()
            .map_err(|_| PyjutsuError::new_err("workspace lock poisoned"))
    }
}

#[pymethods]
impl PyWorkspace {
    /// Load the workspace whose working copy is rooted at `path`.
    #[staticmethod]
    fn load(path: PathBuf) -> PyResult<Self> {
        // Built-in default config is enough to *read* a repo (no user name/email needed until
        // we author commits). UserSettings carries it through the RepoLoader.
        let settings = UserSettings::from_config(StackedConfig::with_defaults()).map_err(to_py_err)?;
        let store_factories = StoreFactories::default();
        let working_copy_factories = default_working_copy_factories();
        let inner = Workspace::load(&settings, &path, &store_factories, &working_copy_factories)
            .map_err(to_py_err)?;
        Ok(Self {
            inner: Mutex::new(inner),
        })
    }

    /// This workspace's name/id (e.g. "default").
    fn name(&self) -> PyResult<String> {
        Ok(self.locked()?.workspace_name().as_str().to_owned())
    }

    /// The filesystem root of this workspace's working copy.
    fn workspace_root(&self) -> PyResult<PathBuf> {
        Ok(self.locked()?.workspace_root().to_owned())
    }

    /// Read `@` — this workspace's working-copy commit — as a plain dict at the head operation.
    /// (Read-only: does not snapshot the working copy.)
    fn working_copy<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let ws = self.locked()?;
        let repo = ws.repo_loader().load_at_head().map_err(to_py_err)?;
        let name = ws.workspace_name();
        let commit_id = repo.view().get_wc_commit_id(name).ok_or_else(|| {
            PyjutsuError::new_err(format!("workspace '{}' has no working-copy commit", name.as_str()))
        })?;
        let commit = repo.store().get_commit(commit_id).map_err(to_py_err)?;
        commit_to_dict(py, &commit)
    }
}

/// Convert a jj-lib `Commit` into plain Python data. Kept minimal for M0; the Python
/// `Commit` model validates these fields and is where the shape grows.
fn commit_to_dict<'py>(py: Python<'py>, commit: &Commit) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    // change_id uses jj's canonical "reverse hex" (z-k digits) — the letter form `jj` shows
    // and users type; commit_id is plain hex (git-style), as `jj` displays it.
    dict.set_item("change_id", commit.change_id().reverse_hex())?;
    dict.set_item("commit_id", commit.id().hex())?;
    dict.set_item("description", commit.description())?;
    Ok(dict)
}

#[pymodule]
fn _pyjutsu(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_class::<PyWorkspace>()?;
    m.add("PyjutsuError", m.py().get_type::<PyjutsuError>())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_is_pinned() {
        // Guards the version contract at the Rust layer (mirrors Cargo.toml's `=0.38.0`).
        assert_eq!(version(), "0.38.0");
    }
}
