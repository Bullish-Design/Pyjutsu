//! `PyTransaction` — the opaque handle holding one in-flight `jj_lib::Transaction` (concept §4, M2).
//!
//! Unlike every other handle, this one is **`unsendable`**: `jj_lib::Transaction` owns a
//! `MutableRepo`, which holds a `Box<dyn MutableIndex>`, and `MutableIndex: Any` carries **no**
//! `Send` bound (verified in jj-lib 0.38, `index.rs:175`). So the transaction is pinned to the
//! thread that started it — it cannot live in the `Send` `PyWorkspace`, nor cross
//! `Python::allow_threads`. We isolate that constraint here and keep `PyWorkspace` `Send`
//! (concept §8.4). As a consequence the in-transaction graph work + commit run **on the GIL**;
//! the genuinely I/O-heavy paths (snapshot, checkout, git) release the GIL around the `Send`
//! `Workspace`/working-copy calls instead (slices 5+).
//!
//! Lifecycle: `Workspace.transaction()`'s `__enter__` starts a tx (via `PyWorkspace`) and gets one
//! of these; `__exit__` calls `commit` (clean) or `rollback` (exception). Either consumes the tx
//! and releases the workspace's single-transaction slot; `Drop` releases it too, so an abandoned
//! handle never wedges the workspace.

use std::cell::RefCell;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use pyo3::prelude::*;
use pyo3::types::PyDict;

use jj_lib::commit::Commit;
use jj_lib::object_id::ObjectId;
use jj_lib::ref_name::WorkspaceNameBuf;
use jj_lib::repo::Repo;
use jj_lib::transaction::Transaction;

use crate::convert::CommitData;
use crate::errors::{PyjutsuError, RevsetError, map_backend_err};
use crate::revset;

#[pyclass(unsendable, module = "pyjutsu._pyjutsu")]
pub(crate) struct PyTransaction {
    /// The native transaction, taken out (left `None`) by whichever of `commit`/`rollback` fires
    /// first; subsequent calls then raise instead of double-consuming.
    tx: RefCell<Option<Transaction>>,
    /// The owning workspace's single-transaction guard, released when this tx is consumed/dropped.
    tx_open: Arc<AtomicBool>,
    /// Revset-resolution context (mirrors `PyRepoView`): the workspace's name + root + author
    /// email, so `@`, `file()`, `mine()`, … resolve the same way reads do — but here against the
    /// open `MutableRepo`, which sees this transaction's in-flight rewrites.
    workspace_name: WorkspaceNameBuf,
    workspace_root: PathBuf,
    user_email: String,
}

impl PyTransaction {
    pub(crate) fn new(
        tx: Transaction,
        tx_open: Arc<AtomicBool>,
        workspace_name: WorkspaceNameBuf,
        workspace_root: PathBuf,
        user_email: String,
    ) -> Self {
        Self {
            tx: RefCell::new(Some(tx)),
            tx_open,
            workspace_name,
            workspace_root,
            user_email,
        }
    }

    /// Take the native transaction out, erroring if it was already committed or rolled back.
    fn take(&self) -> PyResult<Transaction> {
        self.tx
            .borrow_mut()
            .take()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))
    }

    /// Release the workspace's single-transaction slot so the next `transaction()` can proceed.
    fn release_slot(&self) {
        self.tx_open.store(false, Ordering::Release);
    }

    /// Resolve a revset that must name **exactly one** revision → that commit, evaluated against
    /// the open transaction's `MutableRepo` (so it sees in-flight rewrites). More or fewer matches
    /// is a `RevsetError`, mirroring the read surface's single-revision contract.
    fn resolve_single(&self, repo: &dyn Repo, revset_str: &str) -> PyResult<Commit> {
        let mut commits = revset::evaluate(
            repo,
            revset_str,
            &self.workspace_name,
            &self.workspace_root,
            &self.user_email,
        )?;
        if commits.len() != 1 {
            return Err(RevsetError::new_err(format!(
                "revset '{revset_str}' resolved to {} revisions, expected exactly 1",
                commits.len()
            )));
        }
        Ok(commits.pop().expect("len checked == 1"))
    }
}

#[pymethods]
impl PyTransaction {
    /// Set the description of the single commit named by `revset_str` to `message`, returning the
    /// rewritten commit as a plain dict (decision 2: full `Commit` read back from the open repo).
    ///
    /// `rewrite_commit().set_description().write()` records the rewrite; `rebase_descendants()`
    /// then fixes up descendants, bookmarks, and the `@` pointer **before** we read the result, so
    /// the returned commit reflects moved bookmarks. It also clears the pending rewrite, keeping
    /// the tx safe against `commit`'s `!has_rewrites()` assert (landmine #1).
    fn describe<'py>(
        &self,
        py: Python<'py>,
        revset_str: &str,
        message: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs), so the rewrite can't move into
        // `allow_threads`; this is in-memory graph work plus a small object write.
        let repo = tx.repo_mut();
        let commit = self.resolve_single(&*repo, revset_str)?;
        let new_commit = repo
            .rewrite_commit(&commit)
            .set_description(message)
            .write()
            .map_err(map_backend_err)?;
        repo.rebase_descendants().map_err(map_backend_err)?;
        let data = CommitData::build(&*repo, &new_commit)?;
        data.to_dict(py)
    }

    /// Commit the transaction with `description`, publishing exactly one operation, and return
    /// the new head operation id. Centralizes `rebase_descendants()` so every rewriting mutation
    /// is safe against `Transaction::commit`'s `!has_rewrites()` assert (landmine #1: a violation
    /// aborts the process); for a non-rewriting tx it is a harmless no-op. Raises if already closed.
    fn commit(&self, description: String) -> PyResult<String> {
        let mut tx = self.take()?;
        // NOTE: on the GIL — `Transaction` is `!Send` (see module docs), so it cannot be moved
        // into `allow_threads`. The op-store write here is light; heavy I/O is off-GIL elsewhere.
        tx.repo_mut().rebase_descendants().map_err(map_backend_err)?;
        let new_repo = tx.commit(description).map_err(map_backend_err)?;
        self.release_slot();
        Ok(new_repo.operation().id().hex())
    }

    /// Roll back the transaction: drop it, discarding its in-memory changes without publishing
    /// any operation. Raises if already closed.
    fn rollback(&self) -> PyResult<()> {
        self.take()?;
        self.release_slot();
        Ok(())
    }
}

impl Drop for PyTransaction {
    fn drop(&mut self) {
        // If neither commit nor rollback ran, the slot is still claimed — free it so a leaked or
        // never-entered handle doesn't permanently block the workspace.
        if self.tx.get_mut().is_some() {
            self.release_slot();
        }
    }
}
