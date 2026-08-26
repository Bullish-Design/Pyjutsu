//! D5 — the colocated repository's git worktrees.
//!
//! jj workspaces and git worktrees are different things that share a directory, and they coexist
//! badly. jj-lib's own `export_some_refs` walks `git_repo.worktrees()` to detach `HEAD` in each
//! one, so a caller reconciling the two halves needs the same list.
//!
//! Depth: `Repository::worktrees` and `worktree::Proxy` are the shallow gix calls. Read-only.

use std::path::Path;

use jj_lib::git;
use jj_lib::repo::Repo as _;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::errors::{map_backend_err, map_git_err};
use crate::workspace::PyWorkspace;

/// One git worktree as plain data.
struct WorktreeData {
    path: String,
    head_oid: Option<String>,
    branch: Option<String>,
    locked: bool,
    prunable: bool,
    main: bool,
}

impl WorktreeData {
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("path", &self.path)?;
        dict.set_item("head_oid", self.head_oid.as_deref())?;
        dict.set_item("branch", self.branch.as_deref())?;
        dict.set_item("locked", self.locked)?;
        dict.set_item("prunable", self.prunable)?;
        dict.set_item("main", self.main)?;
        Ok(dict)
    }
}

/// `HEAD`'s oid and the branch it points at, for one worktree's repository.
fn head_of(repo: &gix::Repository) -> (Option<String>, Option<String>) {
    let Ok(head) = repo.head() else {
        return (None, None);
    };
    match &head.kind {
        gix::head::Kind::Symbolic(reference) => (
            reference.target.try_id().map(|id| id.to_hex().to_string()),
            Some(reference.name.as_bstr().to_string()),
        ),
        gix::head::Kind::Unborn(name) => (None, Some(name.as_bstr().to_string())),
        gix::head::Kind::Detached { target, .. } => (Some(target.to_hex().to_string()), None),
    }
}

/// Every git worktree of the colocated repository: the main one first, then the linked ones
/// sorted by their private git directory (gix's own order).
pub(crate) fn read<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    let rows = py.allow_threads(move || -> PyResult<Vec<WorktreeData>> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let mut out = Vec::new();
        // gix counts only *linked* worktrees, but `git worktree list` starts with the main one —
        // and that is the oracle, so list it too.
        if let Some(workdir) = git_repo.workdir() {
            let (head_oid, branch) = head_of(&git_repo);
            out.push(WorktreeData {
                path: display_path(workdir),
                head_oid,
                branch,
                locked: false,
                prunable: false,
                main: true,
            });
        }
        for proxy in git_repo.worktrees().map_err(map_git_err)? {
            let base = proxy.base().ok();
            // git calls a worktree prunable when its checkout is gone — "gitdir file points to
            // non-existent location". Same test, without parsing git's prose.
            let prunable = !base.as_deref().is_some_and(Path::is_dir);
            let path = base
                .as_deref()
                .map(display_path)
                .unwrap_or_else(|| display_path(proxy.git_dir()));
            let locked = proxy.is_locked();
            // The possibly-inaccessible variant, so a prunable worktree still reports its HEAD.
            let (head_oid, branch) = match proxy.into_repo_with_possibly_inaccessible_worktree() {
                Ok(linked) => head_of(&linked),
                Err(_) => (None, None),
            };
            out.push(WorktreeData {
                path,
                head_oid,
                branch,
                locked,
                prunable,
                main: false,
            });
        }
        Ok(out)
    })?;
    rows.iter().map(|r| r.to_dict(py)).collect()
}

/// A path as a string. No `gix` or `Path` type crosses the FFI, and a non-UTF-8 path degrades
/// to its lossy form rather than failing the whole listing.
fn display_path(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}
