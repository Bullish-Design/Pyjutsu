//! D4 — the colocated repository's `HEAD`.
//!
//! jj-lib gap (checked 0.42.0 and 0.44.0): jj-lib's `git.rs` only *reads* `HEAD`. It exposes no
//! `set_head`, and `reset_head` owns a different job (detaching `HEAD` at `@`'s parent and
//! rewriting the index). gix 0.85 has no `set_head` either, so the write is one `RefEdit` through
//! the shallow `Repository::edit_reference` — not the low-level file-store transaction
//! `apply_head_ref_packed` drives.

use gix::refs::transaction::{Change, LogChange, PreviousValue, RefEdit, RefLog};
use gix::refs::{FullName, Target};
use jj_lib::git;
use jj_lib::repo::Repo as _;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::errors::{PyjutsuError, map_backend_err, map_git_err};
use crate::workspace::PyWorkspace;

/// `HEAD`'s state as plain data: the full ref name it points at (`None` when detached), the
/// commit oid (`None` when the branch is unborn), and whether it is detached.
struct HeadData {
    name: Option<String>,
    oid: Option<String>,
    detached: bool,
}

impl HeadData {
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("name", self.name.as_deref())?;
        dict.set_item("oid", self.oid.as_deref())?;
        dict.set_item("detached", self.detached)?;
        Ok(dict)
    }
}

/// Normalize a caller-supplied branch name to a full ref name. A bare `main` becomes
/// `refs/heads/main`; anything already starting with `refs/` is taken as written.
fn full_ref_name(name: &str) -> PyResult<FullName> {
    let candidate = if name.starts_with("refs/") {
        name.to_owned()
    } else {
        format!("refs/heads/{name}")
    };
    // gix validates the whole ref name here — this is what replaced the hand-rolled newline check.
    FullName::try_from(candidate.as_str())
        .map_err(|e| PyjutsuError::new_err(format!("invalid ref name '{name}': {e}")))
}

/// Read `HEAD` from the colocated `.git`.
pub(crate) fn read<'py>(workspace: &PyWorkspace, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    let data = py.allow_threads(move || -> PyResult<HeadData> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let head = git_repo.head().map_err(map_git_err)?;
        Ok(match &head.kind {
            gix::head::Kind::Symbolic(reference) => HeadData {
                name: Some(reference.name.as_bstr().to_string()),
                oid: reference.target.try_id().map(|id| id.to_hex().to_string()),
                detached: false,
            },
            // A newly initialized repository: `HEAD` names a branch that has no commit yet.
            gix::head::Kind::Unborn(name) => HeadData {
                name: Some(name.as_bstr().to_string()),
                oid: None,
                detached: false,
            },
            gix::head::Kind::Detached { target, .. } => HeadData {
                name: None,
                oid: Some(target.to_hex().to_string()),
                detached: true,
            },
        })
    })?;
    data.to_dict(py)
}

/// Point `HEAD` at a branch symbolically (`git symbolic-ref HEAD refs/heads/<name>`).
///
/// The branch need not exist: pointing at an absent branch is how git models an unborn branch,
/// and `git symbolic-ref` allows it too.
pub(crate) fn set(workspace: &PyWorkspace, py: Python<'_>, name: &str) -> PyResult<()> {
    let target = full_ref_name(name)?;
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    py.allow_threads(move || -> PyResult<()> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let head_name = FullName::try_from("HEAD").expect("HEAD is a valid ref name");
        git_repo
            .edit_reference(RefEdit {
                change: Change::Update {
                    log: LogChange {
                        mode: RefLog::AndReference,
                        force_create_reflog: false,
                        message: "pyjutsu: set HEAD".into(),
                    },
                    expected: PreviousValue::Any,
                    new: Target::Symbolic(target),
                },
                name: head_name,
                // Edit `HEAD` itself, not whatever it currently points at.
                deref: false,
            })
            .map_err(map_git_err)?;
        Ok(())
    })
}

/// Point a freshly created colocated `.git`'s `HEAD` at `trunk`, during `init`.
///
/// `init` has no `PyWorkspace` yet — the handle is built afterwards — so this takes the jj store
/// the initializer just returned. Replaces the raw `std::fs::write(".git/HEAD", …)` this lane
/// removed, so the ref name is validated by gix rather than by a hand-rolled newline check.
pub(crate) fn set_on_store(store: &jj_lib::store::Store, trunk: &str) -> PyResult<()> {
    let target = full_ref_name(trunk)?;
    let git_repo = git::get_git_repo(store).map_err(map_git_err)?;
    let head_name = FullName::try_from("HEAD").expect("HEAD is a valid ref name");
    git_repo
        .edit_reference(RefEdit {
            change: Change::Update {
                log: LogChange {
                    mode: RefLog::AndReference,
                    force_create_reflog: false,
                    message: "pyjutsu: set HEAD".into(),
                },
                expected: PreviousValue::Any,
                new: Target::Symbolic(target),
            },
            name: head_name,
            deref: false,
        })
        .map_err(map_git_err)?;
    Ok(())
}
