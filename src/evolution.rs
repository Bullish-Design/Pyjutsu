//! Evolution read: follow a change across its rewrites (lane C4).
//!
//! jj-lib owns the machinery (`evolution.rs`): `walk_predecessors` emits
//! `CommitEvolutionEntry` rows (commit + the operation that created/rewrote it
//! + that operation's predecessor record) in reverse-topological order.
//!
//! This module is a thin binding.
//!
//! `Commit.predecessor_ids` is populated **only** on the commits these entries
//! carry. Ordinary reads leave it empty: finding a commit's creating operation
//! requires an op-log walk, and paying that on every read of every commit is
//! not acceptable. The evolution machinery knows the creating operation
//! already, so it fills the field for free.

use futures::StreamExt as _;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use jj_lib::backend::ChangeId;
use jj_lib::evolution::{self, CommitEvolutionEntry};
use jj_lib::object_id::ObjectId;
use jj_lib::repo::Repo;

use crate::convert::{CommitData, OperationData};
use crate::errors::PyjutsuError;
use crate::repo_view::PyRepoView;

/// One evolution entry as plain data: the commit (with `predecessor_ids`
/// populated from the entry) plus the operation that created/rewrote it.
pub(crate) struct EvolutionEntryData {
    commit: CommitData,
    operation: Option<OperationData>,
}

impl EvolutionEntryData {
    fn build(repo: &dyn Repo, entry: &CommitEvolutionEntry) -> PyResult<Self> {
        let mut commit = CommitData::build(repo, &entry.commit)?;
        commit.predecessor_ids = entry.predecessor_ids().iter().map(ObjectId::hex).collect();
        let operation = entry.operation.as_ref().map(OperationData::build);
        Ok(Self { commit, operation })
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("commit", self.commit.to_dict(py)?)?;
        match &self.operation {
            Some(op) => dict.set_item("operation", op.to_dict(py)?)?,
            None => dict.set_item("operation", None::<&str>)?,
        }
        Ok(dict)
    }
}

/// Walk the evolution of the change named by the z-k `change_id_str` →
/// one plain entry per step, newest first, capped at `limit`. Matches
/// `jj evolog -r <change>` for that change. An unknown change id yields an
/// empty list (there is nothing to evolve).
pub(crate) fn evolution<'py>(
    view: &PyRepoView,
    py: Python<'py>,
    change_id_str: &str,
    limit: Option<usize>,
) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let repo = view.repo.clone();
    let change_id_str = change_id_str.to_owned();
    let data = py.allow_threads(move || -> PyResult<Vec<EvolutionEntryData>> {
        let repo = repo.as_ref();
        let change_id = ChangeId::try_from_reverse_hex(&change_id_str)
            .ok_or_else(|| PyjutsuError::new_err(format!("invalid change id '{change_id_str}'")))?;
        // A complete change id is 32 (SHA-1) or 64 (SHA-256) z-k characters. A shorter
        // string is a prefix, and `resolve_change_id` panics on the ambiguity.
        if change_id_str.len() != 32 && change_id_str.len() != 64 {
            return Err(PyjutsuError::new_err(format!(
                "change id '{change_id_str}' is a prefix, not a complete z-k change id"
            )));
        }
        let Some(targets) = repo
            .resolve_change_id(&change_id)
            .map_err(|e| PyjutsuError::new_err(e.to_string()))?
        else {
            return Ok(vec![]);
        };
        // Start from the visible commits with this change id (like `jj evolog`); the
        // predecessor walk then chains the hidden (older) steps by itself.
        let start_commits: Vec<jj_lib::backend::CommitId> = targets
            .visible_with_offsets()
            .map(|(_, id)| id.clone())
            .collect();
        pollster::block_on(async move {
            let mut out = Vec::new();
            let mut stream = std::pin::pin!(evolution::walk_predecessors(repo, &start_commits));
            while let Some(entry) = stream.next().await {
                if limit.is_some_and(|n| out.len() >= n) {
                    break;
                }
                let entry = entry.map_err(|e| PyjutsuError::new_err(e.to_string()))?;
                out.push(EvolutionEntryData::build(repo, &entry)?);
            }
            Ok(out)
        })
    })?;
    data.iter().map(|d| d.to_dict(py)).collect()
}
