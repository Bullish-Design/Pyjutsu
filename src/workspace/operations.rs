//! Operation-log mutation helpers.

use jj_lib::object_id::ObjectId;
use jj_lib::op_walk;
use jj_lib::workspace::Workspace;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use super::PyWorkspace;
use crate::errors::{PyjutsuError, map_backend_err, to_py_err};

pub(super) fn undo<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
    operation: Option<&str>,
) -> PyResult<Bound<'py, PyDict>> {
    let mut guard = workspace.locked()?;
    let ws: &mut Workspace = &mut guard;
    let name = ws.workspace_name().to_owned();
    let op_spec = operation.unwrap_or("@").to_owned();

    let (repo, bad_repo, parent_repo, bad_op_hex) = {
        let loader = ws.repo_loader();
        py.allow_threads(|| -> PyResult<_> {
            let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
            let bad_op = pollster::block_on(op_walk::resolve_op_for_load(loader, &op_spec))
                .map_err(to_py_err)?;
            let parents = pollster::block_on(bad_op.parents()).map_err(map_backend_err)?;
            if parents.len() > 1 {
                return Err(PyjutsuError::new_err("cannot undo a merge operation"));
            }
            let Some(parent_op) = parents.into_iter().next() else {
                return Err(PyjutsuError::new_err(
                    "cannot undo the repo-initialization operation (it has no parent)",
                ));
            };
            let bad_repo = pollster::block_on(loader.load_at(&bad_op)).map_err(map_backend_err)?;
            let parent_repo =
                pollster::block_on(loader.load_at(&parent_op)).map_err(map_backend_err)?;
            Ok((repo, bad_repo, parent_repo, bad_op.id().hex()))
        })?
    };

    // Apply the selected operation's reverse to head, then rebase all rewritten descendants.
    let mut tx = repo.start_transaction();
    {
        let mutable_repo = tx.repo_mut();
        pollster::block_on(mutable_repo.merge(&bad_repo, &parent_repo)).map_err(map_backend_err)?;
        pollster::block_on(mutable_repo.rebase_descendants()).map_err(map_backend_err)?;
    }
    let new_repo = pollster::block_on(tx.commit(format!("undo operation {bad_op_hex}")))
        .map_err(map_backend_err)?;

    workspace.finish_op(py, ws, &name, &repo, &new_repo)
}

pub(super) fn restore_operation<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
    operation: &str,
) -> PyResult<Bound<'py, PyDict>> {
    let mut guard = workspace.locked()?;
    let ws: &mut Workspace = &mut guard;
    let name = ws.workspace_name().to_owned();
    let op_spec = operation.to_owned();

    let (repo, target_view) = {
        let loader = ws.repo_loader();
        py.allow_threads(|| -> PyResult<_> {
            let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
            let target_op = pollster::block_on(op_walk::resolve_op_for_load(loader, &op_spec))
                .map_err(to_py_err)?;
            let view = pollster::block_on(target_op.view())
                .map_err(map_backend_err)?
                .store_view()
                .clone();
            Ok((repo, view))
        })?
    };

    let mut tx = repo.start_transaction();
    tx.repo_mut().set_view(target_view);
    let new_repo = pollster::block_on(tx.commit(format!("restore to operation {op_spec}")))
        .map_err(map_backend_err)?;

    workspace.finish_op(py, ws, &name, &repo, &new_repo)
}
