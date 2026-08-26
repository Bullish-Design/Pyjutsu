//! D7 — the colocated repository's git submodules. **Read-only.**
//!
//! jj has no submodule support: its `submodule_store` is a stub, so a colocated repository with
//! submodules is invisible to Pyjutsu without this. Listing and state only — update, init, and
//! clone would mutate a working copy jj knows nothing about, and the plan rules them out.
//!
//! gix feature: `Repository::submodules` is gated on `attributes`, which jj-lib already enables.
//! Pyjutsu declares it on its own edge anyway, because relying on a transitive crate's feature
//! choice is the mistake finding F1 recorded.

use jj_lib::git;
use jj_lib::repo::Repo as _;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::errors::{map_backend_err, map_git_err};
use crate::workspace::PyWorkspace;

/// One submodule as plain data.
struct SubmoduleData {
    name: String,
    path: Option<String>,
    url: Option<String>,
    head_oid: Option<String>,
    index_oid: Option<String>,
    active: bool,
}

impl SubmoduleData {
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("name", &self.name)?;
        dict.set_item("path", self.path.as_deref())?;
        dict.set_item("url", self.url.as_deref())?;
        dict.set_item("head_oid", self.head_oid.as_deref())?;
        dict.set_item("index_oid", self.index_oid.as_deref())?;
        dict.set_item("active", self.active)?;
        Ok(dict)
    }
}

/// Every submodule declared in `.gitmodules`, sorted by name. An empty list when the repository
/// declares none — the absence of a configuration file is not an error.
pub(crate) fn read<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    let rows = py.allow_threads(move || -> PyResult<Vec<SubmoduleData>> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let Some(submodules) = git_repo.submodules().map_err(map_git_err)? else {
            return Ok(Vec::new());
        };
        let mut out: Vec<SubmoduleData> = submodules
            .map(|submodule| SubmoduleData {
                name: submodule.name().to_string(),
                path: submodule.path().ok().map(|p| p.to_string()),
                url: submodule.url().ok().map(|u| u.to_bstring().to_string()),
                // The commit checked out *inside* the submodule — the oid
                // `git submodule status` prints for a populated submodule.
                //
                // `None` means git's leading `-`: the worktree is not checked out. That test is
                // `state().worktree_checkout`, not `open().is_none()` — `git submodule deinit`
                // empties the worktree but leaves the module repository under `.git/modules`,
                // so `open()` still succeeds and would report a HEAD git does not show.
                //
                // Note also that gix's `Submodule::head_id` is NOT this: it reports the
                // superproject's `HEAD^{tree}` record. Only `open()` reaches the submodule.
                head_oid: submodule
                    .state()
                    .ok()
                    .filter(|state| state.worktree_checkout)
                    .and_then(|_| submodule.open().ok().flatten())
                    // `head_id` borrows the opened repository, so detach the id before it drops.
                    .and_then(|sub_repo| sub_repo.head_id().ok().map(|id| id.detach()))
                    .map(|id| id.to_hex().to_string()),
                // The commit the superproject's *index* records for this path. It differs from
                // `head_oid` exactly when the submodule's checkout has moved — the state
                // `git submodule status` marks with a leading `+`.
                index_oid: submodule
                    .index_id()
                    .ok()
                    .flatten()
                    .map(|id| id.to_hex().to_string()),
                active: submodule.is_active().unwrap_or(false),
            })
            .collect();
        out.sort_by(|a, b| a.name.cmp(&b.name));
        Ok(out)
    })?;
    rows.iter().map(|r| r.to_dict(py)).collect()
}
