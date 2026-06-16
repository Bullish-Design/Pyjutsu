//! `PyWorkspace` — opaque, `Send` handle to one jj workspace (one working-copy path).
//!
//! `jj_lib::Workspace` is `Send` but not `Sync`, so it's held behind a `Mutex` (concept §8.4).
//! M1 reads; M2 adds the write layer: an owned, at-most-one in-flight `Transaction` (also behind
//! a `Mutex`, since it owns a `MutableRepo`). The Python `tx` object is a thin token whose methods
//! re-enter this handle. A mutation transaction publishes exactly one jj operation on commit.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

use gix::remote::{Direction, fetch::Tags};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use jj_lib::commit::Commit;
use jj_lib::config::{ConfigSource, StackedConfig};
use jj_lib::git::{
    self, GitBranchPushTargets, GitFetch, GitFetchRefExpression, GitImportOptions, GitProgress,
    GitSidebandLineTerminator, GitSubprocessCallback, GitSubprocessOptions,
};
use jj_lib::gitignore::GitIgnoreFile;
use jj_lib::matchers::{EverythingMatcher, NothingMatcher};
use jj_lib::object_id::ObjectId;
use jj_lib::op_store::OperationId;
use jj_lib::op_walk;
use jj_lib::ref_name::{RefName, RefNameBuf, RemoteName, WorkspaceName, WorkspaceNameBuf};
use jj_lib::refs::BookmarkPushUpdate;
use jj_lib::repo::{ReadonlyRepo, Repo, RepoLoader, StoreFactories};
use jj_lib::settings::{HumanByteSize, UserSettings};
use jj_lib::str_util::{StringExpression, StringPattern};
use jj_lib::working_copy::{SnapshotOptions, WorkingCopyFreshness};
use jj_lib::workspace::{Workspace, default_working_copy_factories, default_working_copy_factory};
use jj_lib::workspace_store::{SimpleWorkspaceStore, WorkspaceStore};

use crate::convert::{CommitData, OperationData, RemoteData, WorkspaceInfoData};
use crate::errors::{
    PyjutsuError, StaleWorkingCopyError, map_backend_err, map_edit_err, map_git_err,
    map_workingcopy_err, map_workspace_err, to_py_err,
};
use crate::repo_view::PyRepoView;
use crate::transaction::PyTransaction;

/// jj's string-pattern kinds (`kind:value`), as understood by `StringPattern::from_str_kind`.
const STRING_PATTERN_KINDS: &[&str] = &[
    "exact",
    "exact-i",
    "substring",
    "substring-i",
    "glob",
    "glob-i",
    "regex",
    "regex-i",
];

/// Parse one `git_fetch` bookmark spec into a `StringPattern`, glob-by-default (matching jj-cli's
/// `--branch`): a `kind:value` prefix selects the kind, otherwise the whole spec is a glob (which
/// `StringPattern::glob` reduces to an exact match when it has no glob metacharacters). A bad
/// pattern (e.g. an unbalanced glob bracket) becomes a `GitError`.
fn parse_bookmark_pattern(spec: &str) -> PyResult<StringPattern> {
    if let Some((kind, value)) = spec.split_once(':')
        && STRING_PATTERN_KINDS.contains(&kind)
    {
        return StringPattern::from_str_kind(value, kind).map_err(|e| map_git_err(e.to_string()));
    }
    StringPattern::glob(spec).map_err(|e| map_git_err(e.to_string()))
}

/// Map a non-empty list of `git_fetch` bookmark specs to one `StringExpression`, mirroring jj-cli's
/// `--branch` algebra: positive entries are unioned; each `~`-prefixed entry is subtracted from the
/// running expression (set-difference via `intersection(neg.negated())`). With only negatives, the
/// subtraction starts from `all()`.
fn parse_fetch_bookmarks(specs: &[String]) -> PyResult<StringExpression> {
    let mut positives = Vec::new();
    let mut negatives = Vec::new();
    for spec in specs {
        match spec.strip_prefix('~') {
            Some(rest) => negatives.push(parse_bookmark_pattern(rest)?),
            None => positives.push(parse_bookmark_pattern(spec)?),
        }
    }
    let mut expr = if positives.is_empty() {
        StringExpression::all()
    } else {
        StringExpression::union_all(positives.into_iter().map(StringExpression::pattern).collect())
    };
    for neg in negatives {
        expr = expr.intersection(StringExpression::pattern(neg).negated());
    }
    Ok(expr)
}

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

/// No-op `GitSubprocessCallback`: the binding doesn't surface fetch/push progress or sideband
/// output yet (a future slice could route these to a Python callback). Mirrors jj-lib's own test
/// `NullCallback` — `needs_progress` is `false`, every sink is a silent `Ok(())`.
struct NullGitCallback;

impl GitSubprocessCallback for NullGitCallback {
    fn needs_progress(&self) -> bool {
        false
    }
    fn progress(&mut self, _progress: &GitProgress) -> std::io::Result<()> {
        Ok(())
    }
    fn local_sideband(
        &mut self,
        _message: &[u8],
        _terminator: Option<GitSidebandLineTerminator>,
    ) -> std::io::Result<()> {
        Ok(())
    }
    fn remote_sideband(
        &mut self,
        _message: &[u8],
        _terminator: Option<GitSidebandLineTerminator>,
    ) -> std::io::Result<()> {
        Ok(())
    }
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

    /// A freshly-loaded `RepoLoader` that re-opens the store (and its git backend) from disk. The
    /// git-config-touching verbs (`remotes`/`add_remote`/`remove_remote`/`rename_remote`/
    /// `set_remote_url`) need this: a `GitBackend`'s gix repository freezes a config **snapshot** at
    /// open time, and the workspace's own cached loader is opened once at `Workspace::load`. So a
    /// remote added through this handle would be invisible to a later read on the same handle if both
    /// went through the cached loader (the CLI sidesteps this by being a fresh process per command).
    /// Re-opening per verb reads the current on-disk git config, matching the CLI's behaviour.
    fn fresh_loader(ws: &Workspace) -> PyResult<RepoLoader> {
        let settings = ws.repo_loader().settings().clone();
        let store_factories = StoreFactories::default();
        RepoLoader::init_from_file_system(&settings, ws.repo_path(), &store_factories)
            .map_err(map_backend_err)
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

        // Read the configured new-file cap now (a plain `u64`), before the working-copy lock
        // mutably borrows `ws`. Honors `snapshot.max-new-file-size` (jj's `<N>`/`<N>KiB|MiB|…`
        // form, via `HumanByteSize`), defaulting to 1 MiB when unset or unparseable — matching
        // the CLI, which otherwise skips oversized new files (changing `@`'s tree).
        let max_new_file_size = ws
            .repo_loader()
            .settings()
            .get_value_with("snapshot.max-new-file-size", HumanByteSize::try_from)
            .map_or(1 << 20, |size| size.0);

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
        // `base_ignores` is `empty()` because the snapshotter chains every directory's own
        // `.gitignore` as it descends (rooted at `base_ignores`; local_working_copy.rs:1524), so
        // the repo's and nested `.gitignore` files are already honored — verified tree-id-identical
        // to the CLI (0.4.0 slice 4). The only layer `base_ignores` would still add is the *global*
        // git-excludes (`core.excludesFile`, `.git/info/exclude`, `~/.config/git/ignore`); chaining
        // that, and wiring `snapshot.auto-track` into `start_tracking_matcher`, remain flagged.
        // `max_new_file_size` now honors `snapshot.max-new-file-size` (read above).
        let everything = EverythingMatcher;
        let nothing = NothingMatcher;
        let options = SnapshotOptions {
            base_ignores: GitIgnoreFile::empty(),
            progress: None,
            start_tracking_matcher: &everything,
            force_tracking_matcher: &nothing,
            max_new_file_size,
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

    /// Reflect changes in the backing git repo into jj's view (`jj git import`): import HEAD + refs,
    /// publishing one operation — or `None` if nothing changed. If the import abandons the commit `@`
    /// sat on, the on-disk working copy is checked out to the new `@` (off the GIL).
    ///
    /// `import_refs` can abandon unreachable git commits (its `abandon_unreachable_commits` option),
    /// which registers rewrites — so `rebase_descendants()` runs before commit (landmine #1). The
    /// `!Send` `Transaction` is created **and dropped inside one synchronous off-GIL closure on one
    /// thread** (as in `forget_workspace`), so the backend I/O runs off the GIL without the
    /// transaction crossing a thread boundary. `has_changes()` is the no-op signal: when the import
    /// changed nothing, the tx is dropped uncommitted and no operation is published.
    fn git_import<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
        let mut guard = self.locked()?;
        let ws: &mut Workspace = &mut guard;
        let name = ws.workspace_name().to_owned();
        let repo = {
            let loader = ws.repo_loader();
            py.allow_threads(|| loader.load_at_head())
                .map_err(map_backend_err)?
        };

        let new_repo = py.allow_threads(|| -> PyResult<Option<Arc<ReadonlyRepo>>> {
            // Plain `jj git import` options (jj-lib's `default_import_options`): no
            // auto-local-bookmark, abandon unreachable git commits, no per-remote auto-track (the
            // `--remote`/track refinements are out of scope). `GitImportOptions` has no `Default`,
            // so build it explicitly — and inside this closure, since it is `!Sync` (its
            // `StringMatcher` map) and would otherwise break the `allow_threads` `Ungil` bound.
            let options = GitImportOptions {
                auto_local_bookmark: false,
                abandon_unreachable_commits: true,
                remote_auto_track_bookmarks: HashMap::new(),
            };
            let mut tx = repo.start_transaction();
            git::import_head(tx.repo_mut()).map_err(map_git_err)?;
            git::import_refs(tx.repo_mut(), &options).map_err(map_git_err)?;
            tx.repo_mut().rebase_descendants().map_err(map_backend_err)?;
            if !tx.repo_mut().has_changes() {
                return Ok(None);
            }
            Ok(Some(tx.commit("import git refs").map_err(map_backend_err)?))
        })?;
        let Some(new_repo) = new_repo else {
            return Ok(None);
        };
        Ok(Some(self.finish_op(py, ws, &name, &repo, &new_repo)?))
    }

    /// Export jj's bookmarks/tags to the backing git repo's refs (`jj git export`), publishing one
    /// operation — or `None` if nothing changed. Raises `GitError` listing any bookmark that failed
    /// to export (a partial export is a real failure the caller must see). Export is `@`-neutral in
    /// practice, but it is run through the same `finish_op` tail uniformly with import.
    fn git_export<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
        let mut guard = self.locked()?;
        let ws: &mut Workspace = &mut guard;
        let name = ws.workspace_name().to_owned();
        let repo = {
            let loader = ws.repo_loader();
            py.allow_threads(|| loader.load_at_head())
                .map_err(map_backend_err)?
        };

        let new_repo = py.allow_threads(|| -> PyResult<Option<Arc<ReadonlyRepo>>> {
            let mut tx = repo.start_transaction();
            let stats = git::export_refs(tx.repo_mut()).map_err(map_git_err)?;
            if !stats.failed_bookmarks.is_empty() {
                let names = stats
                    .failed_bookmarks
                    .iter()
                    .map(|(symbol, _reason)| symbol.to_string())
                    .collect::<Vec<_>>()
                    .join(", ");
                return Err(map_git_err(format!(
                    "failed to export some bookmarks: {names}"
                )));
            }
            if !tx.repo_mut().has_changes() {
                return Ok(None);
            }
            Ok(Some(tx.commit("export git refs").map_err(map_backend_err)?))
        })?;
        let Some(new_repo) = new_repo else {
            return Ok(None);
        };
        Ok(Some(self.finish_op(py, ws, &name, &repo, &new_repo)?))
    }

    /// Fetch `remote`'s bookmarks into jj's view (`jj git fetch`): run a `git fetch` subprocess,
    /// import the fetched remote-tracking refs, and publish one operation — or `None` if nothing
    /// changed. `bookmarks=None` fetches all bookmarks (the CLI default); a non-empty list fetches
    /// the bookmarks matching its entries, using jj's string-pattern vocabulary (`jj git fetch
    /// --branch`): each entry is a **glob by default** (so a literal name matches itself, and
    /// `feature/*` matches the prefix), or carries a `kind:` prefix (`exact:`, `glob:`,
    /// `substring:`, `regex:`, plus their `-i` variants). A leading `~` negates an entry: positive
    /// entries are unioned, then each negated entry is subtracted (set-difference), so
    /// `["glob:feature/*", "~feature/b"]` fetches `feature/*` except `feature/b` — matching
    /// jj-cli's `--branch 'glob:feature/* ~ feature/b'`. A negatives-only list subtracts from
    /// `all()`. Tags are not fetched (jj-lib's own default; jj #7528) and `--all-remotes` is out of
    /// scope. Raises `GitError` on a malformed pattern or a git failure (unknown remote, rejected
    /// update, subprocess error).
    ///
    /// jj 0.38 fetches via a `git` subprocess, so the whole spawn + network I/O runs **off the GIL**.
    /// The `!Send` `GitFetch`/`Transaction` are created **and dropped inside one synchronous closure
    /// on one thread**; the fetcher (which borrows `&mut MutableRepo`) is dropped in an inner scope
    /// before `rebase_descendants()`/`commit` re-borrow the repo. A fresh loader is used so a remote
    /// added through this handle is visible (slice-10 config-snapshot staleness). `import_refs` can
    /// abandon commits, so `rebase_descendants()` runs before commit (landmine #1).
    #[pyo3(signature = (remote, bookmarks=None))]
    fn git_fetch<'py>(
        &self,
        py: Python<'py>,
        remote: &str,
        bookmarks: Option<Vec<String>>,
    ) -> PyResult<Option<Bound<'py, PyDict>>> {
        let mut guard = self.locked()?;
        let ws: &mut Workspace = &mut guard;
        let name = ws.workspace_name().to_owned();
        let loader = Self::fresh_loader(ws)?;
        let settings = ws.repo_loader().settings().clone();
        let remote = remote.to_owned();

        let new_repo = py.allow_threads(move || -> PyResult<Option<Arc<ReadonlyRepo>>> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            // Plain `jj git fetch` import options (as `git_import`): no auto-local-bookmark, abandon
            // unreachable git commits, no per-remote auto-track. `!Sync`, so build it in-closure.
            let options = GitImportOptions {
                auto_local_bookmark: false,
                abandon_unreachable_commits: true,
                remote_auto_track_bookmarks: HashMap::new(),
            };
            let subprocess = GitSubprocessOptions::from_settings(&settings).map_err(map_git_err)?;
            let remote_name: &RemoteName = remote.as_str().as_ref();
            let mut tx = repo.start_transaction();
            {
                let mut fetcher =
                    GitFetch::new(tx.repo_mut(), subprocess, &options).map_err(map_git_err)?;
                // `bookmarks=None` ⇒ all; otherwise jj's string-pattern algebra (glob-by-default,
                // `kind:` prefixes, `~` negation). Tag fetching stays out of scope (jj #7528).
                let bookmark = match &bookmarks {
                    None => StringExpression::all(),
                    Some(specs) => parse_fetch_bookmarks(specs)?,
                };
                let ref_expr = GitFetchRefExpression {
                    bookmark,
                    tag: StringExpression::none(),
                };
                let refspecs =
                    git::expand_fetch_refspecs(remote_name, ref_expr).map_err(map_git_err)?;
                fetcher
                    .fetch(remote_name, refspecs, &mut NullGitCallback, None, None)
                    .map_err(map_git_err)?;
                fetcher.import_refs().map_err(map_git_err)?;
            } // drop the fetcher → release its &mut MutableRepo borrow before rebase/commit
            tx.repo_mut().rebase_descendants().map_err(map_backend_err)?;
            if !tx.repo_mut().has_changes() {
                return Ok(None);
            }
            Ok(Some(
                tx.commit(format!("fetch from git remote '{remote}'"))
                    .map_err(map_backend_err)?,
            ))
        })?;

        let Some(new_repo) = new_repo else {
            return Ok(None);
        };
        let repo = {
            let loader = ws.repo_loader();
            py.allow_threads(|| loader.load_at_head())
                .map_err(map_backend_err)?
        };
        Ok(Some(self.finish_op(py, ws, &name, &repo, &new_repo)?))
    }

    /// Push local `bookmarks` to `remote` (`jj git push --bookmark <…>`): run a `git push`
    /// subprocess and update the remote-tracking bookmarks in the view, publishing one operation —
    /// or `None` if nothing changed. Several bookmarks push in one operation. `allow_new=False` (the
    /// default) refuses to create a bookmark that doesn't yet exist on the remote (mirrors the CLI's
    /// `--allow-new` gate). `delete=True` removes each named bookmark **on the remote**
    /// (`BookmarkPushUpdate { new_target: None }`); it requires a remote-tracking ref but **not** a
    /// local bookmark (you're deleting the remote ref). Raises `GitError` if: `bookmarks` is empty;
    /// a non-delete bookmark is missing/conflicted locally or new without `allow_new`; a delete
    /// target has no remote ref; or the remote rejects the push (the rejected ref names are reported).
    ///
    /// Subprocess + network → **off the GIL**; the `!Send` `Transaction` lives and dies inside the
    /// one closure on one thread. The local + remote-tracking targets are read from the view before
    /// the tx starts. A fresh loader is used so the remote is found (slice-10 staleness). Push moves
    /// only remote-tracking bookmarks (no commit rewrite), so `rebase_descendants` is unnecessary.
    #[pyo3(signature = (remote, bookmarks, allow_new=false, delete=false))]
    fn git_push<'py>(
        &self,
        py: Python<'py>,
        remote: &str,
        bookmarks: Vec<String>,
        allow_new: bool,
        delete: bool,
    ) -> PyResult<Option<Bound<'py, PyDict>>> {
        if bookmarks.is_empty() {
            return Err(map_git_err("no bookmarks to push".to_owned()));
        }
        let mut guard = self.locked()?;
        let ws: &mut Workspace = &mut guard;
        let name = ws.workspace_name().to_owned();
        let loader = Self::fresh_loader(ws)?;
        let settings = ws.repo_loader().settings().clone();
        let remote = remote.to_owned();

        let new_repo = py.allow_threads(move || -> PyResult<Option<Arc<ReadonlyRepo>>> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            let subprocess = GitSubprocessOptions::from_settings(&settings).map_err(map_git_err)?;
            let remote_name: &RemoteName = remote.as_str().as_ref();

            // Read each bookmark's local + remote-tracking targets from the view, then build one
            // `BookmarkPushUpdate` per bookmark. The read borrow ends with `view` before the tx.
            let view = repo.view();
            let mut branch_updates = Vec::with_capacity(bookmarks.len());
            for bookmark in &bookmarks {
                let bookmark_ref: &RefName = bookmark.as_str().as_ref();
                let remote_ref = view.get_remote_bookmark(bookmark_ref.to_remote_symbol(remote_name));
                let old_target = if remote_ref.target.is_absent() {
                    None
                } else if let Some(id) = remote_ref.target.as_normal() {
                    Some(id.clone())
                } else {
                    return Err(map_git_err(format!(
                        "remote bookmark '{bookmark}@{remote}' is conflicted"
                    )));
                };
                let new_target = if delete {
                    // Deleting the *remote* ref: requires a remote-tracking target, not a local one.
                    if old_target.is_none() {
                        return Err(map_git_err(format!(
                            "bookmark '{bookmark}' doesn't exist on remote '{remote}'"
                        )));
                    }
                    None
                } else {
                    let local = view.get_local_bookmark(bookmark_ref);
                    if local.is_absent() {
                        return Err(map_git_err(format!("no local bookmark '{bookmark}'")));
                    }
                    let Some(target) = local.as_normal().cloned() else {
                        return Err(map_git_err(format!(
                            "refusing to push conflicted bookmark '{bookmark}'"
                        )));
                    };
                    // `allow_new` gate: a bookmark with no remote-tracking ref is new on the remote.
                    if old_target.is_none() && !allow_new {
                        return Err(map_git_err(format!(
                            "bookmark '{bookmark}' doesn't exist on remote '{remote}'; pass allow_new=True"
                        )));
                    }
                    Some(target)
                };
                branch_updates.push((
                    RefNameBuf::from(bookmark.as_str()),
                    BookmarkPushUpdate {
                        old_target,
                        new_target,
                    },
                ));
            }

            let targets = GitBranchPushTargets { branch_updates };

            let mut tx = repo.start_transaction();
            let stats =
                git::push_branches(tx.repo_mut(), subprocess, remote_name, &targets, &mut NullGitCallback)
                    .map_err(map_git_err)?;
            if !stats.all_ok() {
                let mut reasons = Vec::new();
                for (ref_name, why) in stats.rejected.iter().chain(stats.remote_rejected.iter()) {
                    let ref_name = ref_name.as_symbol();
                    match why {
                        Some(reason) => reasons.push(format!("{ref_name} ({reason})")),
                        None => reasons.push(ref_name.to_string()),
                    }
                }
                return Err(map_git_err(format!(
                    "push to remote '{remote}' rejected: {}",
                    reasons.join(", ")
                )));
            }
            if !tx.repo_mut().has_changes() {
                return Ok(None);
            }
            Ok(Some(
                tx.commit(format!("push to git remote '{remote}'"))
                    .map_err(map_backend_err)?,
            ))
        })?;

        let Some(new_repo) = new_repo else {
            return Ok(None);
        };
        let repo = {
            let loader = ws.repo_loader();
            py.allow_threads(|| loader.load_at_head())
                .map_err(map_backend_err)?
        };
        Ok(Some(self.finish_op(py, ws, &name, &repo, &new_repo)?))
    }

    /// The name of `remote`'s default branch (what `git remote show` reports as `HEAD`), or `None`
    /// if the remote advertises none. Used by the pure-Python `git_clone` to place the new `@` on
    /// the cloned default branch. Spawns a `git remote show` subprocess (off the GIL) inside a
    /// throwaway transaction that is never committed (no operation published). Raises `GitError` on
    /// an unknown remote or subprocess failure.
    fn git_default_branch(&self, py: Python<'_>, remote: &str) -> PyResult<Option<String>> {
        let guard = self.locked()?;
        let loader = Self::fresh_loader(&guard)?;
        let settings = guard.repo_loader().settings().clone();
        let remote = remote.to_owned();
        py.allow_threads(move || -> PyResult<Option<String>> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            let options = GitImportOptions {
                auto_local_bookmark: false,
                abandon_unreachable_commits: true,
                remote_auto_track_bookmarks: HashMap::new(),
            };
            let subprocess = GitSubprocessOptions::from_settings(&settings).map_err(map_git_err)?;
            let remote_name: &RemoteName = remote.as_str().as_ref();
            let mut tx = repo.start_transaction();
            let fetcher =
                GitFetch::new(tx.repo_mut(), subprocess, &options).map_err(map_git_err)?;
            let default = fetcher.get_default_branch(remote_name).map_err(map_git_err)?;
            Ok(default.map(|n| n.as_str().to_owned()))
        })
    }

    /// List the configured git remotes: each remote's name + its **fetch** URL. Read-only; matches
    /// `jj git remote list`. jj-lib exposes `get_all_remote_names` (names only), so the URL is read
    /// from the git config via `get_git_repo(store).find_remote(name).url(Direction::Fetch)` and
    /// stringified Rust-side — **no `gix` type crosses the FFI**. A remote with no fetch URL ⇒ `None`.
    fn remotes<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let guard = self.locked()?;
        let loader = Self::fresh_loader(&guard)?;
        let rows = py.allow_threads(move || -> PyResult<Vec<RemoteData>> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            let store = repo.store();
            let names = git::get_all_remote_names(store).map_err(map_git_err)?;
            let git_repo = git::get_git_repo(store).map_err(map_git_err)?;
            names
                .iter()
                .map(|n| {
                    let url = git_repo
                        .find_remote(n.as_str())
                        .ok()
                        .and_then(|r| r.url(Direction::Fetch).map(|u| u.to_string()));
                    Ok(RemoteData::new(n.as_str(), url.as_deref()))
                })
                .collect()
        })?;
        rows.iter().map(|r| r.to_dict(py)).collect()
    }

    /// Add a git remote (`jj git remote add`), publishing one operation. `push_url`, `fetch_tags`,
    /// and per-remote auto-track are the CLI's defaults (`None` / `Tags::None` / match-all) — the
    /// refinements are out of scope, not exposed. A duplicate name raises `GitError`.
    ///
    /// `add_remote` is a `&mut MutableRepo` mutation that changes the view, so it runs inside a
    /// transaction publishing exactly one op. The `!Send` `Transaction` stays inside one off-GIL
    /// closure on one thread (as in `forget_workspace`).
    fn add_remote(&self, py: Python<'_>, name: &str, url: &str) -> PyResult<()> {
        let guard = self.locked()?;
        let loader = Self::fresh_loader(&guard)?;
        py.allow_threads(move || -> PyResult<()> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            let mut tx = repo.start_transaction();
            git::add_remote(
                tx.repo_mut(),
                name.as_ref(),
                url,
                None,
                Tags::None,
                &StringExpression::all(),
            )
            .map_err(map_git_err)?;
            tx.commit(format!("add git remote '{name}'"))
                .map_err(map_backend_err)?;
            Ok(())
        })
    }

    /// Remove a git remote (`jj git remote remove`), publishing one operation; also deletes the
    /// remote's git refs from the view. An unknown remote raises `GitError`.
    fn remove_remote(&self, py: Python<'_>, name: &str) -> PyResult<()> {
        let guard = self.locked()?;
        let loader = Self::fresh_loader(&guard)?;
        py.allow_threads(move || -> PyResult<()> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            let mut tx = repo.start_transaction();
            git::remove_remote(tx.repo_mut(), name.as_ref()).map_err(map_git_err)?;
            tx.commit(format!("remove git remote '{name}'"))
                .map_err(map_backend_err)?;
            Ok(())
        })
    }

    /// Rename a git remote (`jj git remote rename`), publishing one operation. An unknown `old`
    /// remote (or a `new` that already exists) raises `GitError`.
    fn rename_remote(&self, py: Python<'_>, old: &str, new: &str) -> PyResult<()> {
        let guard = self.locked()?;
        let loader = Self::fresh_loader(&guard)?;
        py.allow_threads(move || -> PyResult<()> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            let mut tx = repo.start_transaction();
            git::rename_remote(tx.repo_mut(), old.as_ref(), new.as_ref()).map_err(map_git_err)?;
            tx.commit(format!("rename git remote '{old}' to '{new}'"))
                .map_err(map_backend_err)?;
            Ok(())
        })
    }

    /// Change a remote's fetch URL (`jj git remote set-url`). `set_remote_urls` takes `&Store` and
    /// only rewrites git config — it changes no jj view, so it publishes **NO jj operation** (the
    /// asymmetry vs the other CRUD verbs). An unknown remote raises `GitError`.
    fn set_remote_url(&self, py: Python<'_>, name: &str, url: &str) -> PyResult<()> {
        let guard = self.locked()?;
        let loader = Self::fresh_loader(&guard)?;
        py.allow_threads(move || -> PyResult<()> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            git::set_remote_urls(repo.store(), name.as_ref(), Some(url), None)
                .map_err(map_git_err)
        })
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
