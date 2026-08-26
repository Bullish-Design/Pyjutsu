//! D9 — the colocated repository's git index. **Read-only.**
//!
//! Writing the index behind jj's back is a trap: jj-lib's `reset_head` owns index updates, and a
//! hand-written index would be silently overwritten by the next colocated sync. So this reads and
//! never writes, as the plan requires.
//!
//! gix feature: `Repository::try_index` needs `index`, which jj-lib already enables. Pyjutsu
//! declares it on its own edge anyway (finding F1 again).
//!
//! Depth: `Repository::try_index` is the shallow gix call.

use jj_lib::git;
use jj_lib::repo::Repo as _;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::errors::{map_backend_err, map_git_err};
use crate::workspace::PyWorkspace;

/// One index entry as plain data.
struct IndexEntryData {
    path: String,
    oid: String,
    stage: u32,
    mode: u32,
}

impl IndexEntryData {
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("path", &self.path)?;
        dict.set_item("oid", &self.oid)?;
        dict.set_item("stage", self.stage)?;
        dict.set_item("mode", self.mode)?;
        Ok(dict)
    }
}

/// Every entry in the on-disk git index, in the index's own order (path, then stage) — the order
/// `git ls-files --stage` prints. An absent index file yields an empty list, not an error: a
/// repository with nothing staged has no index file yet.
pub(crate) fn read<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    let rows = py.allow_threads(move || -> PyResult<Vec<IndexEntryData>> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let Some(index) = git_repo.try_index().map_err(map_git_err)? else {
            return Ok(Vec::new());
        };
        Ok(index
            .entries()
            .iter()
            .map(|entry| IndexEntryData {
                path: entry.path(&index).to_string(),
                oid: entry.id.to_hex().to_string(),
                stage: entry.stage_raw(),
                // The raw octal file mode, as `git ls-files --stage` prints it (100644, 100755,
                // 120000, 160000).
                mode: entry.mode.bits(),
            })
            .collect())
    })?;
    rows.iter().map(|r| r.to_dict(py)).collect()
}
