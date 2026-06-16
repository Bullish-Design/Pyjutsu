//! Exception hierarchy + error mapping for the thin layer.
//!
//! The taxonomy lives in Rust (concept §8.2) so the native layer raises the precise subclass
//! when mapping a `jj-lib` error; `python/pyjutsu/errors.py` merely re-exports these. The thin
//! layer never leaks the concrete `jj-lib` error type — only its `Display` message is carried.

use jj_lib::repo::EditCommitError;
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
create_exception!(_pyjutsu, WorkingCopyError, PyjutsuError, "The working copy could not be locked, snapshotted, or checked out.");
// StaleWorkingCopyError ⊂ WorkingCopyError: operating on a `@` another operation has moved past.
create_exception!(_pyjutsu, StaleWorkingCopyError, WorkingCopyError, "The working copy is stale (another operation moved `@`).");
create_exception!(_pyjutsu, ImmutableCommitError, PyjutsuError, "An attempt was made to rewrite or abandon an immutable commit (e.g. the root).");
// GitError ⊂ BackendError: a git import/export or remote-management operation failed (the backing
// git repo or its config). Subclasses BackendError because git is jj's store/backend (concept §134).
create_exception!(_pyjutsu, GitError, BackendError, "A git import/export or remote operation failed.");

/// Register the exception types on the module (one `add` per type so Python can import them).
pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("PyjutsuError", m.py().get_type::<PyjutsuError>())?;
    m.add("RevsetError", m.py().get_type::<RevsetError>())?;
    m.add("ConflictError", m.py().get_type::<ConflictError>())?;
    m.add("BackendError", m.py().get_type::<BackendError>())?;
    m.add("WorkspaceError", m.py().get_type::<WorkspaceError>())?;
    m.add("WorkingCopyError", m.py().get_type::<WorkingCopyError>())?;
    m.add("StaleWorkingCopyError", m.py().get_type::<StaleWorkingCopyError>())?;
    m.add("ImmutableCommitError", m.py().get_type::<ImmutableCommitError>())?;
    m.add("GitError", m.py().get_type::<GitError>())?;
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

/// Working-copy lock/snapshot/checkout failures (`CheckoutError`, `WorkingCopyStateError`) →
/// `WorkingCopyError`. Reused by every `@`-rewriting slice's post-commit on-disk checkout.
pub(crate) fn map_workingcopy_err<E: std::fmt::Display>(err: E) -> PyErr {
    WorkingCopyError::new_err(err.to_string())
}

/// Git import/export + remote-management failures (`GitImportError`, `GitExportError`,
/// `GitRemoteManagementError`, `UnexpectedGitBackendError`) → `GitError`. Display-only crosses FFI.
pub(crate) fn map_git_err<E: std::fmt::Display>(err: E) -> PyErr {
    GitError::new_err(err.to_string())
}

/// `edit` failures: rewriting the root → `ImmutableCommitError`; everything else is a backend
/// problem. Variant-matching (not `Display`-only) so the immutable case raises the precise subclass.
pub(crate) fn map_edit_err(err: EditCommitError) -> PyErr {
    match err {
        EditCommitError::RewriteRootCommit(_) => ImmutableCommitError::new_err(err.to_string()),
        _ => BackendError::new_err(err.to_string()),
    }
}
