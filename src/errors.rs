//! Exception hierarchy + error mapping for the thin layer.
//!
//! The taxonomy lives in Rust (concept §8.2) so the native layer raises the precise subclass
//! when mapping a `jj-lib` error; `python/pyjutsu/errors.py` merely re-exports these. The thin
//! layer never leaks the concrete `jj-lib` error type — only its `Display` message is carried.

use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;

create_exception!(
    _pyjutsu,
    PyjutsuError,
    PyException,
    "Base class for errors raised by the pyjutsu native layer."
);
create_exception!(_pyjutsu, RevsetError, PyjutsuError, "A revset failed to parse, resolve, or evaluate.");
create_exception!(_pyjutsu, ConflictError, PyjutsuError, "A conflict blocked an operation.");
create_exception!(_pyjutsu, BackendError, PyjutsuError, "The underlying store/backend reported an error.");
create_exception!(_pyjutsu, WorkspaceError, PyjutsuError, "A workspace could not be loaded or is unusable.");

/// Register the exception types on the module (one `add` per type so Python can import them).
pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("PyjutsuError", m.py().get_type::<PyjutsuError>())?;
    m.add("RevsetError", m.py().get_type::<RevsetError>())?;
    m.add("ConflictError", m.py().get_type::<ConflictError>())?;
    m.add("BackendError", m.py().get_type::<BackendError>())?;
    m.add("WorkspaceError", m.py().get_type::<WorkspaceError>())?;
    Ok(())
}

/// Fallback mapper: any displayable error → `PyjutsuError` base. Used where no more specific
/// subclass applies; specific paths use the `map_*` helpers below.
pub(crate) fn to_py_err<E: std::fmt::Display>(err: E) -> PyErr {
    PyjutsuError::new_err(err.to_string())
}

/// Workspace load / unusable-state errors → `WorkspaceError`.
pub(crate) fn map_workspace_err<E: std::fmt::Display>(err: E) -> PyErr {
    WorkspaceError::new_err(err.to_string())
}

/// Store / op-store / repo-load errors → `BackendError`.
pub(crate) fn map_backend_err<E: std::fmt::Display>(err: E) -> PyErr {
    BackendError::new_err(err.to_string())
}

/// Revset parse / resolution / evaluation errors → `RevsetError`.
pub(crate) fn map_revset_err<E: std::fmt::Display>(err: E) -> PyErr {
    RevsetError::new_err(err.to_string())
}
