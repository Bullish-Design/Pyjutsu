//! D6 — raw object access in the colocated repository.
//!
//! jj's store answers questions about jj's model; this answers questions about the git object
//! database underneath it — what kind of object is this oid, does it exist at all, what bytes
//! does this blob hold. A colocated tool reconciling the two halves needs both.
//!
//! Depth: `Repository::find_object` / `try_find_object` are the shallow gix calls. Read-only.

use jj_lib::git;
use jj_lib::repo::Repo as _;
use pyo3::PyResult;
use pyo3::Python;

use crate::errors::{PyjutsuError, map_backend_err, map_git_err};
use crate::workspace::PyWorkspace;

/// Parse a hex oid against the repository's own object format, so a SHA-256 repository accepts
/// 64-hex ids and a SHA-1 one does not.
fn parse_oid(git_repo: &gix::Repository, oid: &str) -> PyResult<gix::ObjectId> {
    let kind = git_repo.object_hash();
    gix::ObjectId::from_hex(oid.as_bytes())
        .ok()
        .filter(|id| id.kind() == kind)
        .ok_or_else(|| {
            PyjutsuError::new_err(format!(
                "invalid object id '{oid}': expected {} hex characters",
                kind.len_in_hex()
            ))
        })
}

/// The git object kind at `oid` — `"commit"`, `"tree"`, `"blob"`, or `"tag"` — or `None` when no
/// such object exists.
pub(crate) fn object_type(
    workspace: &PyWorkspace,
    py: Python<'_>,
    oid: &str,
) -> PyResult<Option<String>> {
    let oid = oid.to_owned();
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    py.allow_threads(move || -> PyResult<Option<String>> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let id = parse_oid(&git_repo, &oid)?;
        Ok(git_repo
            .try_find_object(id)
            .map_err(map_git_err)?
            .map(|object| object.kind.to_string()))
    })
}

/// Whether an object with `oid` exists in the git object database.
pub(crate) fn exists(workspace: &PyWorkspace, py: Python<'_>, oid: &str) -> PyResult<bool> {
    let oid = oid.to_owned();
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    py.allow_threads(move || -> PyResult<bool> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let id = parse_oid(&git_repo, &oid)?;
        Ok(git_repo.has_object(id))
    })
}

/// The raw bytes of the blob at `oid`. A missing object, or one that is not a blob, is an error —
/// this verb is deliberately narrow, so a caller cannot silently read a commit's serialized form.
pub(crate) fn read_blob(workspace: &PyWorkspace, py: Python<'_>, oid: &str) -> PyResult<Vec<u8>> {
    let oid = oid.to_owned();
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    py.allow_threads(move || -> PyResult<Vec<u8>> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let id = parse_oid(&git_repo, &oid)?;
        let object = git_repo
            .try_find_object(id)
            .map_err(map_git_err)?
            .ok_or_else(|| PyjutsuError::new_err(format!("no such git object: {oid}")))?;
        if object.kind != gix::object::Kind::Blob {
            return Err(PyjutsuError::new_err(format!(
                "git object {oid} is a {}, not a blob",
                object.kind
            )));
        }
        Ok(object.data.clone())
    })
}
