//! `PyWorkspace` — opaque, `Send` handle to one jj workspace (one working-copy path).
//!
//! `jj_lib::Workspace` is `Send` but not `Sync`, so it's held behind a `Mutex` (concept §8.4).
//! M1 reads; M2 adds the write layer: an owned, at-most-one in-flight `Transaction` (also behind
//! a `Mutex`, since it owns a `MutableRepo`). The Python `tx` object is a thin token whose methods
//! re-enter this handle. A mutation transaction publishes exactly one jj operation on commit.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

use pyo3::prelude::*;
use pyo3::types::PyDict;

use jj_lib::commit::Commit;
use jj_lib::config::{ConfigSource, StackedConfig};
use jj_lib::gitignore::GitIgnoreFile;
use jj_lib::matchers::{EverythingMatcher, NothingMatcher};
use jj_lib::object_id::ObjectId;
use jj_lib::op_store::OperationId;
use jj_lib::op_walk;
use jj_lib::ref_name::{WorkspaceName, WorkspaceNameBuf};
use jj_lib::repo::{ReadonlyRepo, Repo, StoreFactories};
use jj_lib::settings::UserSettings;
use jj_lib::working_copy::{SnapshotOptions, WorkingCopyFreshness};
use jj_lib::workspace::{Workspace, default_working_copy_factories, default_working_copy_factory};
use jj_lib::workspace_store::{SimpleWorkspaceStore, WorkspaceStore};

use crate::convert::{CommitData, OperationData, WorkspaceInfoData};
use crate::errors::{
    PyjutsuError, StaleWorkingCopyError, map_backend_err, map_edit_err, map_workingcopy_err,
    map_workspace_err, to_py_err,
};
use crate::repo_view::PyRepoView;
use crate::transaction::PyTransaction;

/// Build the `UserSettings` the workspace authors commits with, replicating the CLI's config
/// stacking so the binding and the pinned `jj` CLI share one identity (→ identical commit ids).
///
/// jj-lib hands us the layering primitives but not the env policy: `JJ_CONFIG` is a CLI concept,
/// so we reproduce it here (concept §2.3). Precedence (low→high): built-in defaults → user config
/// (`JJ_CONFIG`, else the platform config dir) → this repo's `.jj/repo/config.toml`.
fn load_user_settings(workspace_root: &Path) -> Result<UserSettings, PyErr> {
    let mut config = StackedConfig::with_defaults();

    // User layer. `JJ_CONFIG` may name a file or a directory, or be an OS-path-separated list of
    // them (matching the CLI); when unset, fall back to the platform user config directory.
    if let Some(raw) = std::env::var_os("JJ_CONFIG") {
        for path in std::env::split_paths(&raw) {
            if path.as_os_str().is_empty() {
                continue;
            }
            load_config_path(&mut config, ConfigSource::User, &path)?;
        }
    } else if let Some(dir) = default_user_config_dir()
        && dir.is_dir()
    {
        config
            .load_dir(ConfigSource::User, &dir)
            .map_err(map_workspace_err)?;
    }

    // Repo layer (highest precedence here): the default workspace's `.jj/repo/config.toml`. For
    // secondary workspaces `.jj/repo` is a pointer file, so we only load a regular config file.
    let repo_config = workspace_root.join(".jj").join("repo").join("config.toml");
    if repo_config.is_file() {
        config
            .load_file(ConfigSource::Repo, repo_config)
            .map_err(map_workspace_err)?;
    }

    UserSettings::from_config(config).map_err(map_workspace_err)
}

/// Load a single `JJ_CONFIG` entry as `source`, treating a directory as a config dir and anything
/// else as a config file. A missing path is skipped (lenient; the path may simply not exist yet).
fn load_config_path(
    config: &mut StackedConfig,
    source: ConfigSource,
    path: &Path,
) -> Result<(), PyErr> {
    match std::fs::metadata(path) {
        Ok(meta) if meta.is_dir() => config.load_dir(source, path).map_err(map_workspace_err),
        Ok(_) => config.load_file(source, path).map_err(map_workspace_err),
        Err(_) => Ok(()),
    }
}

/// The platform user config directory jj reads when `JJ_CONFIG` is unset: `$XDG_CONFIG_HOME/jj`
/// (or `$HOME/.config/jj`) on Unix. Only the env-driven path is reproduced here; differential
/// tests always set `JJ_CONFIG`, so this is the convenience path for real usage.
fn default_user_config_dir() -> Option<PathBuf> {
    if let Some(xdg) = std::env::var_os("XDG_CONFIG_HOME").filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(xdg).join("jj"));
    }
    let home = std::env::var_os("HOME").filter(|s| !s.is_empty())?;
    Some(PathBuf::from(home).join(".config").join("jj"))
}

#[pyclass(module = "pyjutsu._pyjutsu")]
pub(crate) struct PyWorkspace {
    inner: Mutex<Workspace>,
    /// The authoring email from settings — carried into the revset context of any view this
    /// workspace produces (so `author()`/`mine()` resolve consistently).
    user_email: String,
    /// Single-open-transaction guard. The native `jj_lib::Transaction` is **not** `Send` (its
    /// `MutableRepo` holds a `Box<dyn MutableIndex>`, and `MutableIndex: Any` has no `Send`
    /// bound), so it cannot live in this `Send` handle — it lives in an `unsendable`
    /// `PyTransaction` instead (see `transaction.rs`). This flag, shared with the live
    /// `PyTransaction`, enforces "one open transaction per workspace" while keeping `PyWorkspace`
    /// `Send` (concept §8.4: the workspace handle stays movable; only the transaction is pinned).
    tx_open: Arc<AtomicBool>,
}

impl PyWorkspace {
    fn locked(&self) -> PyResult<std::sync::MutexGuard<'_, Workspace>> {
        self.inner
            .lock()
            .map_err(|_| PyjutsuError::new_err("workspace lock poisoned"))
    }

    /// Check out `new_commit` into the **already-locked** `ws`, recording it at `op_id`. The file
    /// I/O runs off the GIL. Shared by `checkout_wc` (which locks first) and the op-log writes
    /// (`undo`/`restore_operation`, which already hold the lock for the whole load → tx → checkout
    /// sequence — calling `checkout_wc` there would re-lock the workspace `Mutex` and deadlock).
    fn checkout_locked(
        py: Python<'_>,
        ws: &mut Workspace,
        op_id: OperationId,
        new_commit: &Commit,
    ) -> PyResult<()> {
        // The tree the in-memory workspace believes is on disk; `check_out` compares it against
        // the freshly-locked working copy to detect a concurrent checkout by another process.
        let old_tree = ws
            .working_copy()
            .tree()
            .map_err(map_workingcopy_err)?
            .clone();
        py.allow_threads(move || ws.check_out(op_id, Some(&old_tree), new_commit))
            .map_err(map_workingcopy_err)?;
        Ok(())
    }

    /// Update the on-disk working copy to `new_commit`, recording it at `op_id` so the working
    /// copy's operation stays in lockstep with the repo head (matching `Workspace::check_out`,
    /// workspace.rs:437). Called by `PyTransaction::commit` whenever a committed transaction
    /// moved `@`. The file I/O runs **off the GIL**: `Workspace` is `Send`, so the only thing on
    /// the GIL is acquiring the handle's `Mutex`.
    pub(crate) fn checkout_wc(
        &self,
        py: Python<'_>,
        op_id: OperationId,
        new_commit: &Commit,
    ) -> PyResult<()> {
        let mut guard = self.locked()?;
        Self::checkout_locked(py, &mut guard, op_id, new_commit)
    }

    /// After an op-log write commits: if `@` moved between `old_repo` and `new_repo`, check out the
    /// new `@` on disk (reusing the held lock via `checkout_locked`), then return the published
    /// operation as a plain dict. Shared tail of `undo`/`restore_operation`.
    fn finish_op<'py>(
        &self,
        py: Python<'py>,
        ws: &mut Workspace,
        name: &WorkspaceName,
        old_repo: &ReadonlyRepo,
        new_repo: &ReadonlyRepo,
    ) -> PyResult<Bound<'py, PyDict>> {
        let old_wc = old_repo.view().get_wc_commit_id(name).cloned();
        let new_wc = new_repo.view().get_wc_commit_id(name).cloned();
        if new_wc != old_wc
            && let Some(new_id) = new_wc
        {
            let new_commit = new_repo.store().get_commit(&new_id).map_err(map_backend_err)?;
            let op_id = new_repo.operation().id().clone();
            Self::checkout_locked(py, ws, op_id, &new_commit)?;
        }
        OperationData::build(new_repo.operation()).to_dict(py)
    }
}

#[pymethods]
impl PyWorkspace {
    /// Load the workspace whose working copy is rooted at `path`.
    #[staticmethod]
    fn load(path: PathBuf) -> PyResult<Self> {
        // M2 authors commits, so load the *real* stacked config (user + repo), not just defaults:
        // `CommitBuilder` and op metadata take author/committer from these settings, and they must
        // match the CLI's to produce identical commit ids (concept §2.3).
        let settings = load_user_settings(&path)?;
        let user_email = settings.user_email().to_owned();
        let store_factories = StoreFactories::default();
        let working_copy_factories = default_working_copy_factories();
        let inner = Workspace::load(&settings, &path, &store_factories, &working_copy_factories)
            .map_err(map_workspace_err)?;
        Ok(Self {
            inner: Mutex::new(inner),
            user_email,
            tx_open: Arc::new(AtomicBool::new(false)),
        })
    }

    /// This workspace's name/id (e.g. "default").
    fn name(&self) -> PyResult<String> {
        Ok(self.locked()?.workspace_name().as_str().to_owned())
    }

    /// The filesystem root of this workspace's working copy.
    fn workspace_root(&self) -> PyResult<PathBuf> {
        Ok(self.locked()?.workspace_root().to_owned())
    }

    /// A `PyRepoView` of the repo at its **head** operation, scoped to this workspace.
    fn head_view(&self, py: Python<'_>) -> PyResult<PyRepoView> {
        let ws = self.locked()?;
        let name = ws.workspace_name().to_owned();
        let root = ws.workspace_root().to_owned();
        let loader = ws.repo_loader();
        let repo = py
            .allow_threads(|| loader.load_at_head())
            .map_err(map_backend_err)?;
        Ok(PyRepoView::new(repo, name, root, self.user_email.clone()))
    }

    /// The id of the current head operation (what a fresh `head_view` loads at).
    fn head_operation(&self, py: Python<'_>) -> PyResult<String> {
        let ws = self.locked()?;
        let loader = ws.repo_loader();
        let repo = py
            .allow_threads(|| loader.load_at_head())
            .map_err(map_backend_err)?;
        Ok(repo.operation().id().hex())
    }

    /// A historical `PyRepoView` of the repo at the operation named by `op_str` (an op id,
    /// prefix, or expression like `@-`). Reads see that past state; nothing is written.
    fn at_operation(&self, py: Python<'_>, op_str: &str) -> PyResult<PyRepoView> {
        let ws = self.locked()?;
        let name = ws.workspace_name().to_owned();
        let root = ws.workspace_root().to_owned();
        let loader = ws.repo_loader();
        let repo = py.allow_threads(|| -> PyResult<_> {
            // An invalid/ambiguous op spec is user-input error → PyjutsuError base; a load
            // failure of a valid op is a backend problem.
            let op = op_walk::resolve_op_for_load(loader, op_str).map_err(to_py_err)?;
            loader.load_at(&op).map_err(map_backend_err)
        })?;
        Ok(PyRepoView::new(repo, name, root, self.user_email.clone()))
    }

    /// Snapshot the working copy: record any on-disk changes to `@` as a separate
    /// `snapshot working copy` operation (concept §0.1), returning that operation as a plain dict —
    /// or `None` if the working copy already matched `@` (no operation published). Mirrors what the
    /// pinned `jj` CLI does automatically before each command; this is the explicit form and the
    /// auto-snapshot primitive.
    ///
    /// I/O-heavy and **off the GIL** wherever the work is `Send` (lock, disk walk, tree write,
    /// `finish`); only the `!Send` recording `Transaction` runs on the GIL, between those off-GIL
    /// spans. The workspace `Mutex` is held for the whole sequence.
    fn snapshot<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
        let mut guard = self.locked()?;
        let ws: &mut Workspace = &mut guard;

        // 1. Load the repo at head + the current `@` commit. No `@` ⇒ nothing to snapshot.
        let repo = {
            let loader = ws.repo_loader();
            py.allow_threads(|| loader.load_at_head())
                .map_err(map_backend_err)?
        };
        let name = ws.workspace_name().to_owned();
        let Some(wc_commit_id) = repo.view().get_wc_commit_id(&name).cloned() else {
            return Ok(None);
        };
        let wc_commit = repo
            .store()
            .get_commit(&wc_commit_id)
            .map_err(map_backend_err)?;

        // 2. Lock the WC and check freshness (working_copy.rs:363).
        let mut locked_ws = ws
            .start_working_copy_mutation()
            .map_err(map_workingcopy_err)?;
        match WorkingCopyFreshness::check_stale(locked_ws.locked_wc(), &wc_commit, &repo)
            .map_err(map_backend_err)?
        {
            WorkingCopyFreshness::Fresh => {}
            // Slice 6 adds the full stale surface (`is_stale`/`update_stale`); here we refuse to
            // snapshot a stale/sibling `@` rather than clobber it.
            WorkingCopyFreshness::WorkingCopyStale | WorkingCopyFreshness::SiblingOperation => {
                return Err(StaleWorkingCopyError::new_err(
                    "working copy is stale; another operation moved `@`",
                ));
            }
            // The WC moved under us between load-at-head and taking the lock (rare in-process).
            // The full reload-and-retry is slice 6; here we surface it rather than rewrite a
            // commit whose parent we no longer hold.
            WorkingCopyFreshness::Updated(_) => {
                return Err(StaleWorkingCopyError::new_err(
                    "working copy was updated concurrently; reload and retry",
                ));
            }
        }

        // 3. Snapshot the on-disk tree (off the GIL — `LockedWorkspace` is `Send`).
        //
        // NOTE: `SnapshotOptions` fidelity is the one documented refinement (slice 5 guide §2).
        // `base_ignores = empty` + `max_new_file_size = 1 MiB` reproduce the CLI for repos without
        // a `.gitignore` and without oversized files (every fixture; `.jj`/`.git` are excluded
        // internally by the snapshotter). Full fidelity — chain the user/repo `.gitignore` and read
        // `snapshot.max-new-file-size`/`snapshot.auto-track` from settings — is future work.
        let everything = EverythingMatcher;
        let nothing = NothingMatcher;
        let options = SnapshotOptions {
            base_ignores: GitIgnoreFile::empty(),
            progress: None,
            start_tracking_matcher: &everything,
            force_tracking_matcher: &nothing,
            max_new_file_size: 1 << 20, // 1 MiB — the jj CLI's `snapshot.max-new-file-size` default.
        };
        let new_tree = py
            .allow_threads(|| pollster::block_on(locked_ws.locked_wc().snapshot(&options)))
            .map_err(map_workingcopy_err)?
            .0;

        // 4. Clean WC ⇒ tree unchanged ⇒ no operation (drop the lock without writing).
        if new_tree.tree_ids() == wc_commit.tree_ids() {
            return Ok(None);
        }

        // 5. Record the snapshot as a rewrite of `@` (on the GIL — `Transaction` is `!Send`).
        let mut tx = repo.start_transaction();
        tx.set_is_snapshot(true);
        {
            let mrepo = tx.repo_mut();
            mrepo
                .rewrite_commit(&wc_commit)
                .set_tree(new_tree)
                .write()
                .map_err(map_backend_err)?;
            // Satisfies `commit`'s `!has_rewrites()` assert (landmine #1); fixes up any descendants.
            mrepo.rebase_descendants().map_err(map_backend_err)?;
        }
        let new_repo = tx
            .commit("snapshot working copy")
            .map_err(map_backend_err)?;

        // 6. Save the WC state at the new op (off the GIL). The tree is already on disk — `finish`
        //    records "this WC is at <new op> with <new tree>"; it does **not** check out, which is
        //    why snapshot never moves files.
        let op_id = new_repo.operation().id().clone();
        py.allow_threads(|| locked_ws.finish(op_id))
            .map_err(map_workingcopy_err)?;

        let data = OperationData::build(new_repo.operation());
        Ok(Some(data.to_dict(py)?))
    }

    /// Whether the on-disk working copy is **stale** relative to the repo's current `@` — i.e. the
    /// repo advanced past (or diverged from) the operation the working copy was last written at, and
    /// the on-disk tree no longer matches `@`. A read-only probe (matches what `jj` checks before
    /// each command); mutating or snapshotting a stale `@` raises `StaleWorkingCopyError`.
    fn is_stale(&self, py: Python<'_>) -> PyResult<bool> {
        let mut guard = self.locked()?;
        let ws: &mut Workspace = &mut guard;

        let repo = {
            let loader = ws.repo_loader();
            py.allow_threads(|| loader.load_at_head())
                .map_err(map_backend_err)?
        };
        let name = ws.workspace_name().to_owned();
        let Some(wc_commit_id) = repo.view().get_wc_commit_id(&name).cloned() else {
            return Ok(false); // no `@` in this workspace ⇒ nothing can be stale
        };
        let wc_commit = repo
            .store()
            .get_commit(&wc_commit_id)
            .map_err(map_backend_err)?;

        // `check_stale` needs the WC lock (`old_operation_id` + `old_tree`); take it, check, drop it.
        let mut locked_ws = ws
            .start_working_copy_mutation()
            .map_err(map_workingcopy_err)?;
        let freshness = WorkingCopyFreshness::check_stale(locked_ws.locked_wc(), &wc_commit, &repo)
            .map_err(map_backend_err)?;
        Ok(matches!(
            freshness,
            WorkingCopyFreshness::WorkingCopyStale | WorkingCopyFreshness::SiblingOperation
        ))
    }

    /// Reconcile a stale working copy: check out the repo's current `@` into it (matches
    /// `jj workspace update-stale`), returning the now-current `@` as a plain dict — or `None` if the
    /// working copy was already fresh (nothing to do). The checkout is I/O and runs **off the GIL**.
    fn update_stale<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
        let mut guard = self.locked()?;
        let ws: &mut Workspace = &mut guard;

        let repo = {
            let loader = ws.repo_loader();
            py.allow_threads(|| loader.load_at_head())
                .map_err(map_backend_err)?
        };
        let name = ws.workspace_name().to_owned();
        let Some(wc_commit_id) = repo.view().get_wc_commit_id(&name).cloned() else {
            return Ok(None);
        };
        let wc_commit = repo
            .store()
            .get_commit(&wc_commit_id)
            .map_err(map_backend_err)?;

        // 1. Staleness check (own lock scope; dropped before the forced checkout re-locks).
        let stale = {
            let mut locked_ws = ws
                .start_working_copy_mutation()
                .map_err(map_workingcopy_err)?;
            let freshness =
                WorkingCopyFreshness::check_stale(locked_ws.locked_wc(), &wc_commit, &repo)
                    .map_err(map_backend_err)?;
            matches!(
                freshness,
                WorkingCopyFreshness::WorkingCopyStale | WorkingCopyFreshness::SiblingOperation
            )
        };
        if !stale {
            return Ok(None); // matches the CLI's "the working copy is not stale" no-op
        }

        // 2. Forced checkout of `@` at head. `old_tree = None` bypasses the `ConcurrentCheckout`
        //    guard — which would otherwise trip on exactly the stale on-disk tree we mean to
        //    overwrite (so the slice-2 `checkout_wc`, which passes `Some`, cannot be reused here).
        let op_id = repo.operation().id().clone();
        py.allow_threads(|| ws.check_out(op_id, None, &wc_commit))
            .map_err(map_workingcopy_err)?;

        // 3. Return the reconciled `@` (build off the GIL — `is_empty` touches the backend).
        let data = py.allow_threads(|| CommitData::build(&*repo, &wc_commit))?;
        Ok(Some(data.to_dict(py)?))
    }

    /// Revert one operation, publishing a new operation that applies its reverse — matches
    /// `jj undo`. `operation` is an op spec (id, prefix, or expression like `@`/`@-`); `None`
    /// undoes the head op. Reverting the repo-initialization op (no parent) or a merge op (>1
    /// parent) is a user error (`PyjutsuError`). If the reverse moves `@`, the on-disk working copy
    /// is checked out to the new `@` (off the GIL).
    ///
    /// The op-store reads run off the GIL; the `!Send` `Transaction` (merge/commit) runs on the
    /// GIL between those spans; the workspace `Mutex` is held for the whole sequence (atomicity),
    /// so the checkout goes through `checkout_locked`, not the re-locking `checkout_wc`.
    #[pyo3(signature = (operation=None))]
    fn undo<'py>(&self, py: Python<'py>, operation: Option<&str>) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.locked()?;
        let ws: &mut Workspace = &mut guard;
        let name = ws.workspace_name().to_owned();
        let op_spec = operation.unwrap_or("@").to_owned();

        // Load head + the to-undo op's repo and its single parent's repo (backend I/O → off GIL).
        let (repo, bad_repo, parent_repo, bad_op_hex) = {
            let loader = ws.repo_loader();
            py.allow_threads(|| -> PyResult<_> {
                let repo = loader.load_at_head().map_err(map_backend_err)?;
                // A bad/ambiguous op spec is user input → PyjutsuError base (matches `at_operation`).
                let bad_op = op_walk::resolve_op_for_load(loader, &op_spec).map_err(to_py_err)?;
                let mut parents = bad_op.parents();
                let Some(parent) = parents.next() else {
                    return Err(PyjutsuError::new_err(
                        "cannot undo the repo-initialization operation (it has no parent)",
                    ));
                };
                let parent_op = parent.map_err(map_backend_err)?;
                if parents.next().is_some() {
                    return Err(PyjutsuError::new_err("cannot undo a merge operation"));
                }
                let bad_repo = loader.load_at(&bad_op).map_err(map_backend_err)?;
                let parent_repo = loader.load_at(&parent_op).map_err(map_backend_err)?;
                Ok((repo, bad_repo, parent_repo, bad_op.id().hex()))
            })?
        };

        // Build the reverse op on the GIL (Transaction is !Send). merge(base = bad, other = parent)
        // applies (parent − bad) onto head = the reverse of the bad op. `merge` records the reverted
        // commit as a rewrite (repo.rs:record_rewrites), so descendants must be rebased onto it
        // before commit — both to satisfy `commit`'s `!has_rewrites()` assert (transaction.rs:136)
        // and to faithfully move any children of the reverted commit, exactly as `jj undo` does.
        let mut tx = repo.start_transaction();
        {
            let mrepo = tx.repo_mut();
            mrepo.merge(&bad_repo, &parent_repo).map_err(map_backend_err)?;
            mrepo.rebase_descendants().map_err(map_backend_err)?;
        }
        let new_repo = tx
            .commit(format!("undo operation {bad_op_hex}"))
            .map_err(map_backend_err)?;

        self.finish_op(py, ws, &name, &repo, &new_repo)
    }

    /// Reset the repo to the view a past operation recorded, publishing a new operation — matches
    /// `jj op restore <op>` (all portions). `operation` is an op spec (id, prefix, or `@`/`@-`).
    /// If the restored view moves `@`, the on-disk working copy is checked out to it (off the GIL).
    fn restore_operation<'py>(
        &self,
        py: Python<'py>,
        operation: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.locked()?;
        let ws: &mut Workspace = &mut guard;
        let name = ws.workspace_name().to_owned();
        let op_spec = operation.to_owned();

        let (repo, target_view) = {
            let loader = ws.repo_loader();
            py.allow_threads(|| -> PyResult<_> {
                let repo = loader.load_at_head().map_err(map_backend_err)?;
                let target_op = op_walk::resolve_op_for_load(loader, &op_spec).map_err(to_py_err)?;
                // Operation::view() is the high-level View; set_view wants op_store::View.
                let view = target_op.view().map_err(map_backend_err)?.store_view().clone();
                Ok((repo, view))
            })?
        };

        let mut tx = repo.start_transaction();
        tx.repo_mut().set_view(target_view);
        let new_repo = tx
            .commit(format!("restore to operation {op_spec}"))
            .map_err(map_backend_err)?;

        self.finish_op(py, ws, &name, &repo, &new_repo)
    }

    /// Create a brand-new jj repo + default workspace at `path`, returning a handle to it.
    /// `colocate=false` uses an internal git store (`.jj/repo/store/git`); `colocate=true` colocates
    /// a `.git` sharing the working copy. Matches `jj git init` / `jj git init --colocate`. The new
    /// workspace's `@` is an empty commit on `root()`; one initialization operation is published.
    ///
    /// I/O-heavy and `Send` → the constructor runs **off the GIL**. The returned `Workspace` is
    /// wrapped in a fresh `PyWorkspace` (same shape as `load`).
    #[staticmethod]
    #[pyo3(signature = (path, colocate=false))]
    fn init(py: Python<'_>, path: PathBuf, colocate: bool) -> PyResult<Self> {
        // At init time the repo config doesn't exist yet, so this loads `JJ_CONFIG` + built-in
        // defaults (the repo layer is silently skipped) — the same identity the CLI's `jj git init`
        // authors with, so any commit this workspace later makes shares the CLI's commit ids.
        let settings = load_user_settings(&path)?;
        let user_email = settings.user_email().to_owned();
        let (workspace, _repo) = py
            .allow_threads(|| {
                if colocate {
                    Workspace::init_colocated_git(&settings, &path)
                } else {
                    Workspace::init_internal_git(&settings, &path)
                }
            })
            .map_err(map_workspace_err)?;
        Ok(Self {
            inner: Mutex::new(workspace),
            user_email,
            tx_open: Arc::new(AtomicBool::new(false)),
        })
    }

    /// Add a secondary workspace rooted at `path`, sharing this repo's store; returns its
    /// `WorkspaceInfo` (name + path + the fresh empty `@`). `name` defaults to `path`'s basename.
    /// jj-lib's `init_workspace_with_existing_repo` does everything here — it creates the new `.jj`,
    /// checks out a fresh empty commit on `root()` for the new workspace, and **publishes its own
    /// `add workspace '<name>'` operation** — so this is one off-GIL constructor call, not a
    /// hand-rolled transaction. Matches `jj workspace add`, except the new `@` lands on `root()`; the
    /// CLI's default instead bases it on the current `@`'s parents (the `-r <revs>` placement and
    /// `--sparse-patterns` inheritance are out-of-scope refinements — flagged, not faked).
    #[pyo3(signature = (path, name=None))]
    fn add_workspace<'py>(
        &self,
        py: Python<'py>,
        path: PathBuf,
        name: Option<&str>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let guard = self.locked()?;
        let repo_path = guard.repo_path().to_owned();
        let name_buf = WorkspaceNameBuf::from(match name {
            Some(n) => n.to_owned(),
            None => path
                .file_name()
                .and_then(|s| s.to_str())
                .ok_or_else(|| PyjutsuError::new_err("workspace path has no valid basename"))?
                .to_owned(),
        });

        // Load this repo at head, then let jj-lib create the new workspace (+ its op). The new `@`
        // is an empty commit on root, so there are no files to check out. All `Send` → off the GIL.
        // The `!Send` `Transaction` jj-lib opens internally is created and dropped on this one
        // worker thread, so it never crosses a thread boundary.
        let loader = guard.repo_loader();
        let (wc_id, new_root) = py.allow_threads(|| -> PyResult<_> {
            // `init_workspace_with_existing_repo` creates `<path>/.jj` but not `<path>` itself;
            // `jj workspace add` creates the destination dir, so do the same here.
            std::fs::create_dir_all(&path).map_err(map_workspace_err)?;
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            let factory = default_working_copy_factory();
            let (new_ws, new_repo) = Workspace::init_workspace_with_existing_repo(
                &path,
                &repo_path,
                &repo,
                &*factory,
                name_buf.clone(),
            )
            .map_err(map_workspace_err)?;
            let wc_id = new_repo
                .view()
                .get_wc_commit_id(&name_buf)
                .ok_or_else(|| PyjutsuError::new_err("new workspace has no working-copy commit"))?
                .hex();
            Ok((wc_id, new_ws.workspace_root().to_owned()))
        })?;
        WorkspaceInfoData::new(name_buf.as_str(), Some(&new_root), &wc_id).to_dict(py)
    }

    /// Stop tracking workspace `name`'s working-copy commit in the repo (the on-disk files are left
    /// untouched), publishing one operation. Matches `jj workspace forget <name>`. Errors with
    /// `PyjutsuError` if no workspace `name` is tracked.
    ///
    /// `remove_wc_commit` abandons the workspace's `@` when it is discardable, which registers a
    /// rewrite — so `rebase_descendants()` runs before commit (landmine #1). The `!Send`
    /// `Transaction` is created **and dropped inside one synchronous closure on one thread**, so the
    /// op-store write runs off the GIL without the transaction crossing a thread boundary.
    fn forget_workspace(&self, py: Python<'_>, name: &str) -> PyResult<()> {
        let guard = self.locked()?;
        let name_buf = WorkspaceNameBuf::from(name.to_owned());
        let loader = guard.repo_loader();
        py.allow_threads(|| -> PyResult<_> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            if repo.view().get_wc_commit_id(&name_buf).is_none() {
                return Err(PyjutsuError::new_err(format!("no such workspace '{name}'")));
            }
            let mut tx = repo.start_transaction();
            tx.repo_mut()
                .remove_wc_commit(&name_buf)
                .map_err(map_edit_err)?;
            tx.repo_mut().rebase_descendants().map_err(map_backend_err)?;
            tx.commit(format!("forget workspace '{name}'"))
                .map_err(map_backend_err)?;
            Ok(())
        })
    }

    /// List all workspaces tracked in the repo view: each name + its on-disk root + `@` commit id.
    /// The `WorkspaceStore` trait has no list-all, so names are enumerated from the view and each
    /// path is looked up in the store (`None` if the store has no entry). Matches `jj workspace
    /// list`. Read-only; the backend reads run **off the GIL**.
    fn workspaces<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let guard = self.locked()?;
        let repo_path = guard.repo_path().to_owned();
        let loader = guard.repo_loader();
        let rows = py.allow_threads(|| -> PyResult<Vec<WorkspaceInfoData>> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            let store = SimpleWorkspaceStore::load(&repo_path).map_err(map_workspace_err)?;
            repo.view()
                .wc_commit_ids()
                .iter()
                .map(|(name, id)| {
                    let path = store.get_workspace_path(name).map_err(map_workspace_err)?;
                    Ok(WorkspaceInfoData::new(
                        name.as_str(),
                        path.as_deref(),
                        &id.hex(),
                    ))
                })
                .collect()
        })?;
        rows.iter().map(|r| r.to_dict(py)).collect()
    }

    /// Open a transaction: claim the single-tx slot, reload the repo at head, start a native
    /// `Transaction`, and hand it back wrapped in a `PyTransaction`. Raises if one is already
    /// open. Reloading at head mirrors the CLI, which observes the latest op before each command.
    ///
    /// Takes `slf` by `Bound` so the `PyTransaction` can hold a `Py<PyWorkspace>` back-reference:
    /// it needs the workspace to drive the post-commit on-disk checkout (`checkout_wc`) when a
    /// committed transaction moves `@`. We also capture the starting `@` commit id here so commit
    /// can tell whether `@` actually moved.
    ///
    /// (Auto-snapshot of a dirty `@` is layered on in slice 5; this is the bare start.)
    fn begin_transaction(slf: Bound<'_, Self>, py: Python<'_>) -> PyResult<PyTransaction> {
        let this = slf.borrow();
        // Claim the slot atomically; bail (without claiming) if a tx is already live.
        if this.tx_open.swap(true, Ordering::AcqRel) {
            return Err(PyjutsuError::new_err(
                "a transaction is already open on this workspace",
            ));
        }
        // From here, any early return must release the slot or the workspace stays wedged.
        let started = (|| -> PyResult<_> {
            let ws = this.locked()?;
            let name = ws.workspace_name().to_owned();
            let root = ws.workspace_root().to_owned();
            let loader = ws.repo_loader();
            let repo = py
                .allow_threads(|| loader.load_at_head())
                .map_err(map_backend_err)?;
            let starting_wc = repo.view().get_wc_commit_id(&name).cloned();
            Ok((repo.start_transaction(), name, root, starting_wc))
        })();
        match started {
            Ok((tx, name, root, starting_wc)) => Ok(PyTransaction::new(
                tx,
                this.tx_open.clone(),
                slf.clone().unbind(),
                name,
                root,
                this.user_email.clone(),
                starting_wc,
            )),
            Err(err) => {
                this.tx_open.store(false, Ordering::Release);
                Err(err)
            }
        }
    }
}
