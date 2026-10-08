//! Guarded publication: `PyWorkspace::publish_if`.
//!
//! jj has no conditional publication. A normal transaction commit updates the operation heads
//! with no comparison, and the jj CLI releases the working-copy lock between its snapshot and its
//! publish. This module holds the working-copy lock across the whole sequence and compares the
//! operation heads before it publishes. Lock order follows jj: Git import/export lock (colocated
//! repositories only), then working-copy lock, then operation-heads lock.
//!
//! Sequence, in one hold of the working-copy lock:
//!  1. take the locks, then load the repository at head;
//!  2. refuse a stale working copy;
//!  3. snapshot the disk **in memory only**. `LockedWorkingCopy::snapshot` changes in-memory state;
//!     only `finish` writes the state file. A dirty working copy is a rejection and its bytes stay
//!     on disk;
//!  4. compare the working-copy commit with the caller's expectation;
//!  5. prepare a new empty child of `onto` and write the operation without publishing it;
//!  6. under the operation-heads lock, compare the heads with the operation loaded in step 1 and
//!     update them only on a match;
//!  7. check out the new commit, synchronize colocated Git, and `finish` the working copy.
//!
//! `UnpublishedOperation::publish` is never called. It updates the heads with no comparison.
//!
//! The native layer returns plain dicts with a `status` key (`published`, `stale`, `incomplete`,
//! or `error`). The Python layer turns them into results and exceptions.

use std::path::Path;
use std::sync::atomic::Ordering;

use jj_lib::backend::BackendError;
use jj_lib::commit::Commit;
use jj_lib::git;
use jj_lib::git_backend::GitBackend;
use jj_lib::lock::FileLock;
use jj_lib::matchers::NothingMatcher;
use jj_lib::object_id::ObjectId;
use jj_lib::op_heads_store::OpHeadsStoreError;
use jj_lib::op_store::OperationId;
use jj_lib::repo::{ReadonlyRepo, Repo, RepoLoader};
use jj_lib::backend::CommitId;
use jj_lib::simple_op_heads_store::SimpleOpHeadsStore;
use jj_lib::working_copy::WorkingCopyFreshness;
use jj_lib::workspace::Workspace;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use super::{PyWorkspace, SnapshotInputs};
use crate::errors::{PyjutsuError, map_backend_err, map_workingcopy_err};

/// The working-copy type this guard supports. Another type may not honor the same lock.
const SUPPORTED_WORKING_COPY: &str = "local";

#[cfg(feature = "test-hooks")]
fn hook(stage: &str) {
    crate::test_hooks::hook(stage);
}
#[cfg(not(feature = "test-hooks"))]
fn hook(_stage: &str) {}

#[cfg(feature = "test-hooks")]
fn skip_op_heads_comparison() -> bool {
    crate::test_hooks::skip_op_heads_comparison()
}
#[cfg(not(feature = "test-hooks"))]
fn skip_op_heads_comparison() -> bool {
    false
}

/// What the caller sent, plus the ids this call observed. Every result dict carries it.
struct Facts {
    expected: String,
    onto: String,
}

fn new_result<'py>(py: Python<'py>, status: &str, facts: &Facts) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("status", status)?;
    dict.set_item("expected_wc_commit", &facts.expected)?;
    dict.set_item("onto", &facts.onto)?;
    Ok(dict)
}

/// The call changed nothing visible. `reason` is a stable code.
fn stale<'py>(
    py: Python<'py>,
    facts: &Facts,
    reason: &str,
    observed_wc_commit: &str,
    head_operations: &[String],
    dirty: bool,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = new_result(py, "stale", facts)?;
    dict.set_item("reason", reason)?;
    dict.set_item("observed_wc_commit", observed_wc_commit)?;
    dict.set_item("head_operations", PyList::new(py, head_operations)?)?;
    dict.set_item("dirty", dirty)?;
    Ok(dict)
}

/// The call cannot run on this input or store. It changed nothing.
fn refused<'py>(
    py: Python<'py>,
    facts: &Facts,
    reason: &str,
    message: &str,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = new_result(py, "error", facts)?;
    dict.set_item("reason", reason)?;
    dict.set_item("message", message)?;
    Ok(dict)
}

/// The operation may be, or is, in the operation log, but a later step failed.
fn incomplete<'py>(
    py: Python<'py>,
    facts: &Facts,
    stage: &str,
    operation: &str,
    message: &str,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = new_result(py, "incomplete", facts)?;
    dict.set_item("stage", stage)?;
    dict.set_item("operation", operation)?;
    dict.set_item("message", message)?;
    Ok(dict)
}

fn parse_commit_id(hex: &str, length: usize) -> Option<CommitId> {
    if hex.len() != length * 2 {
        return None;
    }
    CommitId::try_from_hex(hex)
}

/// A repository is colocated when its Git working directory is the jj workspace root.
fn is_colocated(loader: &RepoLoader, workspace_root: &Path) -> bool {
    let Some(git_backend) = loader.store().backend_impl::<GitBackend>() else {
        return false;
    };
    let Some(workdir) = git_backend.git_workdir() else {
        return false;
    };
    match (workdir.canonicalize(), workspace_root.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        _ => false,
    }
}

/// Reset Git `HEAD` and the Git index to the new working-copy commit. Publishes one extra
/// operation only when the view's Git `HEAD` changed. Returns that operation, if any.
fn sync_git(
    repo: &std::sync::Arc<ReadonlyRepo>,
    new_wc: &Commit,
) -> Result<Option<std::sync::Arc<ReadonlyRepo>>, String> {
    let mut tx = repo.start_transaction();
    pollster::block_on(git::reset_head(tx.repo_mut(), new_wc)).map_err(|e| e.to_string())?;
    if !tx.repo_mut().has_changes() {
        return Ok(None);
    }
    pollster::block_on(tx.commit("sync colocated git"))
        .map(Some)
        .map_err(|e| e.to_string())
}

pub(super) fn publish_if<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
    expected_wc_commit: &str,
    onto: &str,
    description: String,
) -> PyResult<Bound<'py, PyDict>> {
    let facts = Facts {
        expected: expected_wc_commit.to_owned(),
        onto: onto.to_owned(),
    };
    if workspace.tx_open.load(Ordering::Acquire) {
        return Err(PyjutsuError::new_err(
            "a transaction is open on this workspace; commit or roll it back first",
        ));
    }
    let mut guard = workspace.locked()?;
    let ws: &mut Workspace = &mut guard;
    let name = ws.workspace_name().to_owned();
    let loader = ws.repo_loader().clone();

    // Refuse a store this guard does not understand. The comparison needs the operation-heads
    // lock to mean what the simple store says it means, and the working-copy lock to be jj's.
    let heads_store_name = loader.op_heads_store().name().to_owned();
    if heads_store_name != SimpleOpHeadsStore::name() {
        return refused(
            py,
            &facts,
            "unsupported-store",
            &format!(
                "operation heads store `{heads_store_name}` is not supported; \
                 guarded publication needs `{}`",
                SimpleOpHeadsStore::name()
            ),
        );
    }
    if ws.working_copy().name() != SUPPORTED_WORKING_COPY {
        return refused(
            py,
            &facts,
            "unsupported-store",
            &format!(
                "working copy type `{}` is not supported; guarded publication needs `{SUPPORTED_WORKING_COPY}`",
                ws.working_copy().name()
            ),
        );
    }

    let id_length = loader.store().commit_id_length();
    let Some(expected) = parse_commit_id(expected_wc_commit, id_length) else {
        return refused(
            py,
            &facts,
            "invalid-commit-id",
            &format!("expected_wc_commit must be a full {}-digit hex commit id", id_length * 2),
        );
    };
    let Some(onto_id) = parse_commit_id(onto, id_length) else {
        return refused(
            py,
            &facts,
            "invalid-commit-id",
            &format!("onto must be a full {}-digit hex commit id", id_length * 2),
        );
    };

    let inputs = SnapshotInputs::read(ws)?;
    let colocated = is_colocated(&loader, ws.workspace_root());
    let git_lock_path = ws.repo_path().join("git_import_export.lock");

    // Lock 1 (colocated only): the Git import/export lock. A jj CLI command takes it before its
    // working-copy lock, and again before it writes Git `HEAD`. Holding it here keeps a CLI writer
    // from importing a half-synchronized Git `HEAD` (measured: without it, a writer that starts
    // after the guard imports `HEAD` as a new operation). It has a cost: a CLI writer that loaded
    // the old head and already left its snapshot phase waits here, then publishes on its old head.
    // That fork is merged by the next jj command (see docs/PUBLISH_IF.md, limit 2).
    hook("BEFORE_LOCK");
    let _git_lock = if colocated {
        Some(
            py.allow_threads(|| FileLock::lock(git_lock_path))
                .map_err(map_workingcopy_err)?,
        )
    } else {
        None
    };

    // Lock 2: the working-copy lock. It stays held until `finish` or an early return.
    let mut locked_ws = py
        .allow_threads(|| pollster::block_on(ws.start_working_copy_mutation()))
        .map_err(map_workingcopy_err)?;
    hook("LOCKED");

    // Load the repository AFTER taking the lock, so every operation published before this point
    // is visible and the comparison below uses a value read inside the guard.
    let repo = py
        .allow_threads(|| pollster::block_on(loader.load_at_head()))
        .map_err(map_backend_err)?;
    let Some(wc_commit_id) = repo.view().get_wc_commit_id(&name).cloned() else {
        return Err(PyjutsuError::new_err("workspace has no working-copy commit"));
    };
    let wc_commit = repo
        .store()
        .get_commit(&wc_commit_id)
        .map_err(map_backend_err)?;
    let observed = wc_commit_id.hex();
    let loaded_head = repo.operation().id().clone();
    let loaded_heads = vec![loaded_head.hex()];

    // A stale working copy holds the files of an older `@`. Do not snapshot it.
    match pollster::block_on(WorkingCopyFreshness::check_stale(
        locked_ws.locked_wc(),
        &wc_commit,
        &repo,
    ))
    .map_err(map_backend_err)?
    {
        WorkingCopyFreshness::Fresh => {}
        _ => {
            return stale(py, &facts, "stale-working-copy", &observed, &loaded_heads, false);
        }
    }

    // Snapshot in memory. Nothing persists unless `finish` runs, so a rejection below leaves the
    // working-copy state file and the disk files as they were.
    let nothing = NothingMatcher;
    let options = inputs.options(&nothing);
    let (snapshot_tree, _stats) = py
        .allow_threads(|| pollster::block_on(locked_ws.locked_wc().snapshot(&options)))
        .map_err(map_workingcopy_err)?;
    let dirty = snapshot_tree.tree_ids_and_labels() != wc_commit.tree().tree_ids_and_labels();
    if dirty {
        return stale(py, &facts, "dirty-working-copy", &observed, &loaded_heads, true);
    }
    if wc_commit_id != expected {
        return stale(py, &facts, "commit-moved", &observed, &loaded_heads, false);
    }

    let onto_commit = match repo.store().get_commit(&onto_id) {
        Ok(commit) => commit,
        Err(BackendError::ObjectNotFound { .. }) => {
            return refused(py, &facts, "onto-not-found", "onto commit does not exist");
        }
        Err(err) => return Err(map_backend_err(err)),
    };

    // Prepare: a new empty child of `onto`, set as this workspace's `@`.
    let mut tx = repo.start_transaction();
    let new_wc = pollster::block_on(tx.repo_mut().check_out(name.clone(), &onto_commit))
        .map_err(map_backend_err)?;
    pollster::block_on(tx.repo_mut().rebase_descendants()).map_err(map_backend_err)?;

    // Write the operation without publishing it, then compare and update the heads.
    let unpublished = pollster::block_on(tx.write(description)).map_err(map_backend_err)?;
    let new_op_id: OperationId = unpublished.operation().id().clone();
    let parent_ids = unpublished.operation().parent_ids().to_vec();
    let op_hex = new_op_id.hex();
    hook("BEFORE_PUBLISH");

    let heads_store = loader.op_heads_store().clone();
    enum Cas {
        Moved(Vec<OperationId>),
        Updated,
        LockFailed(OpHeadsStoreError),
        UpdateFailed(OpHeadsStoreError),
    }
    let cas = py.allow_threads(|| {
        pollster::block_on(async {
            let _lock = match heads_store.lock().await {
                Ok(lock) => lock,
                Err(err) => return Cas::LockFailed(err),
            };
            let heads = match heads_store.get_op_heads().await {
                Ok(heads) => heads,
                Err(err) => return Cas::LockFailed(err),
            };
            if !skip_op_heads_comparison() && heads.as_slice() != std::slice::from_ref(&loaded_head)
            {
                return Cas::Moved(heads);
            }
            match heads_store.update_op_heads(&parent_ids, &new_op_id).await {
                Ok(()) => Cas::Updated,
                Err(err) => Cas::UpdateFailed(err),
            }
        })
    });
    match cas {
        Cas::Updated => {}
        Cas::Moved(heads) => {
            // Someone published between our load and our comparison. Our operation stays in the
            // operation store as unreachable garbage; `jj util gc` collects it. Report the heads
            // we saw. Do not reload: a reload would publish a merge of the divergent heads.
            let _ = unpublished.leave_unpublished();
            let heads: Vec<String> = heads.iter().map(|id| id.hex()).collect();
            return stale(py, &facts, "op-heads-moved", &observed, &heads, false);
        }
        Cas::LockFailed(err) => {
            let _ = unpublished.leave_unpublished();
            return Err(map_backend_err(err));
        }
        Cas::UpdateFailed(err) => {
            // The head update can fail after it wrote the new head file. The landed state is
            // unknown. Never report it as a clean refusal.
            let _ = unpublished.leave_unpublished();
            return incomplete(py, &facts, "publish-uncertain", &op_hex, &err.to_string());
        }
    }
    // The heads now name our operation. `leave_unpublished` only consumes the `#[must_use]`
    // handle and returns the repository at the new operation. It undoes nothing.
    let published_repo = unpublished.leave_unpublished();
    hook("AFTER_PUBLISH");

    // Check out the new `@`. The file writes can fail after publication.
    let checkout = py.allow_threads(|| {
        pollster::block_on(locked_ws.locked_wc().check_out(&new_wc)).map_err(|e| e.to_string())
    });
    if let Err(err) = checkout {
        // Dropping `locked_ws` without `finish` leaves the state file at the old operation, so
        // the working copy reads as stale and `recover` can repair it.
        return incomplete(py, &facts, "checkout", &op_hex, &err);
    }
    hook("AFTER_CHECKOUT");

    // Synchronize colocated Git while the locks are still held. This can add one operation.
    let mut git_sync = "not-colocated";
    let mut git_sync_operation: Option<String> = None;
    let mut git_error: Option<String> = None;
    let mut finish_op = new_op_id.clone();
    let mut head_op = op_hex.clone();
    if colocated {
        match py.allow_threads(|| sync_git(&published_repo, &new_wc)) {
            Ok(Some(synced)) => {
                git_sync = "synced";
                finish_op = synced.operation().id().clone();
                head_op = finish_op.hex();
                git_sync_operation = Some(head_op.clone());
            }
            Ok(None) => git_sync = "unchanged",
            Err(err) => {
                git_sync = "failed";
                git_error = Some(err);
            }
        }
    }
    hook("AFTER_GIT_SYNC");

    // Record the working copy at the newest operation. Without this the state file names an old
    // operation and the working copy reads as stale.
    let finished = py
        .allow_threads(|| pollster::block_on(locked_ws.finish(finish_op)))
        .map_err(|e| e.to_string());
    if let Err(err) = finished {
        return incomplete(py, &facts, "checkout", &op_hex, &format!("the working copy state could not be saved: {err}"));
    }
    hook("AFTER_FINISH");

    if let Some(err) = git_error {
        return incomplete(py, &facts, "git-sync", &op_hex, &err);
    }

    let dict = new_result(py, "published", &facts)?;
    dict.set_item("operation", &op_hex)?;
    dict.set_item("head_operation", head_op)?;
    dict.set_item("wc_commit", new_wc.id().hex())?;
    dict.set_item("git_sync", git_sync)?;
    dict.set_item("git_sync_operation", git_sync_operation)?;
    Ok(dict)
}

#[cfg(test)]
mod tests {
    //! These tests pin the jj-lib behavior the guard relies on. A jj-lib upgrade that changes one
    //! of them must fail here, not in a race.

    use std::path::PathBuf;

    use jj_lib::config::{ConfigLayer, ConfigSource, StackedConfig};
    use jj_lib::ref_name::WorkspaceNameBuf;
    use jj_lib::settings::UserSettings;

    use super::*;

    fn settings() -> UserSettings {
        let mut config = StackedConfig::with_defaults();
        let mut layer = ConfigLayer::empty(ConfigSource::User);
        layer.set_value("user.name", "Test").unwrap();
        layer.set_value("user.email", "test@example.invalid").unwrap();
        config.add_layer(layer);
        UserSettings::from_config(config).unwrap()
    }

    fn scratch_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("pyjutsu-publish-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn commit_ids_must_have_the_store_length() {
        let sha1 = "a".repeat(40);
        assert!(parse_commit_id(&sha1, 20).is_some());
        assert!(parse_commit_id(&sha1, 32).is_none(), "a SHA-1 id is wrong for a SHA-256 store");
        assert!(parse_commit_id(&"a".repeat(39), 20).is_none());
        assert!(parse_commit_id(&"z".repeat(40), 20).is_none());
        assert!(parse_commit_id("", 20).is_none());
    }

    #[test]
    fn the_supported_stores_are_the_ones_jj_lib_names() {
        assert_eq!(SimpleOpHeadsStore::name(), "simple_op_heads_store");
        assert_eq!(SUPPORTED_WORKING_COPY, "local");
    }

    /// `Transaction::write` plus `leave_unpublished` must not publish; a guarded update must.
    #[test]
    fn an_unpublished_operation_stays_out_of_the_heads_until_the_guarded_update() {
        let dir = scratch_dir("unpublished");
        let settings = settings();
        let (_workspace, repo) = pollster::block_on(Workspace::init_internal_git(
            &settings,
            &dir,
            gix::hash::Kind::Sha1,
        ))
        .unwrap();
        let base_op = repo.operation().id().clone();
        let heads_store = repo.loader().op_heads_store().clone();
        assert_eq!(pollster::block_on(heads_store.get_op_heads()).unwrap(), vec![base_op.clone()]);

        let name = WorkspaceNameBuf::from("default");
        let mut tx = repo.start_transaction();
        let root = repo.store().root_commit();
        pollster::block_on(tx.repo_mut().check_out(name, &root)).unwrap();
        pollster::block_on(tx.repo_mut().rebase_descendants()).unwrap();
        let unpublished = pollster::block_on(tx.write("guarded")).unwrap();
        let new_op = unpublished.operation().id().clone();
        let parents = unpublished.operation().parent_ids().to_vec();
        assert_eq!(parents, vec![base_op.clone()]);

        // Written, not published: the heads and a fresh load-at-head still name the base.
        let _ = unpublished.leave_unpublished();
        assert_eq!(pollster::block_on(heads_store.get_op_heads()).unwrap(), vec![base_op.clone()]);
        let loaded = pollster::block_on(repo.loader().load_at_head()).unwrap();
        assert_eq!(loaded.operation().id(), &base_op);

        // The guarded update: compare, then update under the lock.
        let moved = pollster::block_on(async {
            let _lock = heads_store.lock().await.unwrap();
            let heads = heads_store.get_op_heads().await.unwrap();
            assert_eq!(heads.as_slice(), std::slice::from_ref(&base_op));
            heads_store.update_op_heads(&parents, &new_op).await.unwrap();
            heads_store.get_op_heads().await.unwrap()
        });
        assert_eq!(moved, vec![new_op.clone()]);
        let loaded = pollster::block_on(repo.loader().load_at_head()).unwrap();
        assert_eq!(loaded.operation().id(), &new_op);

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The unguarded update forks the log, which is why the guard compares first.
    #[test]
    fn updating_from_a_stale_base_forks_the_heads_which_the_comparison_detects() {
        let dir = scratch_dir("fork");
        let settings = settings();
        let (_workspace, repo) = pollster::block_on(Workspace::init_internal_git(
            &settings,
            &dir,
            gix::hash::Kind::Sha1,
        ))
        .unwrap();
        let base_op = repo.operation().id().clone();
        let heads_store = repo.loader().op_heads_store().clone();

        let publish = |description: &'static str| {
            let mut tx = repo.start_transaction();
            let root = repo.store().root_commit();
            pollster::block_on(tx.repo_mut().check_out(WorkspaceNameBuf::from("default"), &root)).unwrap();
            pollster::block_on(tx.repo_mut().rebase_descendants()).unwrap();
            let unpublished = pollster::block_on(tx.write(description)).unwrap();
            let id = unpublished.operation().id().clone();
            let parents = unpublished.operation().parent_ids().to_vec();
            let _ = unpublished.leave_unpublished();
            (id, parents)
        };
        let (first, first_parents) = publish("first");
        let (second, second_parents) = publish("second");
        pollster::block_on(heads_store.update_op_heads(&first_parents, &first)).unwrap();
        // The comparison the guard makes: the heads are no longer `[base]`.
        let heads = pollster::block_on(heads_store.get_op_heads()).unwrap();
        assert_ne!(heads.as_slice(), std::slice::from_ref(&base_op));
        // Without the comparison, the second update succeeds and leaves two heads.
        pollster::block_on(heads_store.update_op_heads(&second_parents, &second)).unwrap();
        let mut heads = pollster::block_on(heads_store.get_op_heads()).unwrap();
        heads.sort();
        let mut expected = vec![first, second];
        expected.sort();
        assert_eq!(heads, expected);

        let _ = std::fs::remove_dir_all(&dir);
    }
}
