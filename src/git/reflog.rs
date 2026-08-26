//! D8 — the colocated repository's git reflog.
//!
//! jj's operation log covers what **jj** did. It does not cover a `git reset` or `git checkout`
//! run outside jj — exactly the case a colocated recovery tool is called for. The git reflog is
//! the other half of that history.
//!
//! Depth: `Head::log_iter` and `Reference::log_iter` are the shallow gix calls. Read-only.

use gix::refs::file::log::iter::Platform;
use jj_lib::git;
use jj_lib::repo::Repo as _;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::errors::{PyjutsuError, map_backend_err, map_git_err};
use crate::workspace::PyWorkspace;

/// One reflog line as plain data. The signature is split into the same four fields every other
/// Pyjutsu signature uses, so Python assembles the tz-aware datetime.
struct ReflogData {
    old_oid: String,
    new_oid: String,
    name: String,
    email: String,
    timestamp_ms: i64,
    tz_offset_minutes: i32,
    message: String,
}

impl ReflogData {
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("old_oid", &self.old_oid)?;
        dict.set_item("new_oid", &self.new_oid)?;
        let signature = PyDict::new(py);
        signature.set_item("name", &self.name)?;
        signature.set_item("email", &self.email)?;
        signature.set_item("timestamp_ms", self.timestamp_ms)?;
        signature.set_item("tz_offset_minutes", self.tz_offset_minutes)?;
        dict.set_item("signature", signature)?;
        dict.set_item("message", &self.message)?;
        Ok(dict)
    }
}

/// Drain a reflog platform newest-first, up to `limit` entries.
fn collect(platform: &mut Platform<'_, '_>, limit: Option<usize>) -> PyResult<Vec<ReflogData>> {
    // `rev()` yields most-recent first, which is `git reflog show`'s order.
    let Some(iter) = platform.rev().map_err(map_git_err)? else {
        return Ok(Vec::new()); // no reflog file for this ref
    };
    let mut out = Vec::new();
    for line in iter {
        if limit.is_some_and(|n| out.len() >= n) {
            break;
        }
        let line = line.map_err(map_git_err)?;
        let time = line.signature.time;
        out.push(ReflogData {
            old_oid: line.previous_oid.to_hex().to_string(),
            new_oid: line.new_oid.to_hex().to_string(),
            name: String::from_utf8_lossy(&line.signature.name).into_owned(),
            email: String::from_utf8_lossy(&line.signature.email).into_owned(),
            timestamp_ms: time.seconds * 1000,
            tz_offset_minutes: time.offset / 60,
            message: String::from_utf8_lossy(&line.message).into_owned(),
        });
    }
    Ok(out)
}

/// Read the reflog of `ref_name` (default `HEAD`), newest entry first.
///
/// A ref with no reflog file yields an empty list rather than an error: git does not create one
/// until something moves the ref, and "nothing has happened yet" is not a failure.
pub(crate) fn read<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
    ref_name: &str,
    limit: Option<usize>,
) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let ref_name = ref_name.to_owned();
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    let rows = py.allow_threads(move || -> PyResult<Vec<ReflogData>> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        if ref_name == "HEAD" {
            let head = git_repo.head().map_err(map_git_err)?;
            return collect(&mut head.log_iter(), limit);
        }
        // A bare name is a branch, like `git reflog show <branch>`.
        let full = if ref_name.starts_with("refs/") {
            ref_name.clone()
        } else {
            format!("refs/heads/{ref_name}")
        };
        let reference = git_repo
            .try_find_reference(full.as_str())
            .map_err(map_git_err)?
            .ok_or_else(|| PyjutsuError::new_err(format!("no such git ref: {ref_name}")))?;
        collect(&mut reference.log_iter(), limit)
    })?;
    rows.iter().map(|r| r.to_dict(py)).collect()
}
