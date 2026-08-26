//! `PyTransaction` — the opaque handle holding one in-flight `jj_lib::Transaction` (concept §4, M2).
//!
//! Unlike every other handle, this one is **`unsendable`**: `jj_lib::Transaction` owns a
//! `MutableRepo`, which holds a `Box<dyn MutableIndex>`, and `MutableIndex: Any` carries **no**
//! `Send` bound (verified in jj-lib 0.44, `index.rs:186`). So the transaction is pinned to the
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
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use futures::TryStreamExt as _;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use jj_lib::backend::{CommitId, TreeValue};
use jj_lib::commit::Commit;
use jj_lib::dag_walk;
use jj_lib::diff::{ContentDiff, DiffHunkKind};
use jj_lib::matchers::{EverythingMatcher, FilesMatcher};
use jj_lib::merge::{Merge, MergedTreeValue};
use jj_lib::merged_tree::MergedTree;
use jj_lib::merged_tree_builder::MergedTreeBuilder;
use jj_lib::object_id::ObjectId;
use jj_lib::op_store::RefTarget;
use jj_lib::ref_name::{RefName, RemoteName, WorkspaceNameBuf};
use jj_lib::repo::Repo;
use jj_lib::repo_path::{RepoPath, RepoPathBuf};
use jj_lib::rewrite::{
    self, CommitWithSelection, MoveCommitsLocation, MoveCommitsTarget, RebaseOptions,
    merge_commit_trees, move_commits, restore_tree, squash_commits,
};
use jj_lib::settings::UserSettings;
use jj_lib::store::Store;
use jj_lib::transaction::Transaction;

use crate::convert::{BookmarkData, CommitData};
use crate::diff;
use crate::diff_stat::read_text;
use crate::errors::{
    ConflictError, ImmutableCommitError, PyjutsuError, RevsetError, StaleWorkingCopyError,
    map_backend_err, map_edit_err,
};
use crate::revset::{self, RevsetConfig};
use crate::workspace::PyWorkspace;

/// One file's split selection: whole-file (`None`) or a set of 0-based hunk indices into that
/// file's `RepoView.diff()` output (`Some`). This is the S3-A selection vocabulary — the indices
/// reference the very hunks `diff()` emitted for the *same* commit, so they are stable and need no
/// patch-header parsing. Crosses the FFI as a `dict[str, list[int] | None]`.
type FileSelection = Option<Vec<usize>>;

#[pyclass(unsendable, module = "pyjutsu._pyjutsu")]
pub(crate) struct PyTransaction {
    /// The native transaction, taken out (left `None`) by whichever of `commit`/`rollback` fires
    /// first; subsequent calls then raise instead of double-consuming.
    tx: RefCell<Option<Transaction>>,
    /// The owning workspace's single-transaction guard, released when this tx is consumed/dropped.
    tx_open: Arc<AtomicBool>,
    /// Back-reference to the owning workspace, used by `commit` to drive the on-disk checkout when
    /// the transaction moves `@`. `Py<PyWorkspace>` is `Send`; the workspace only holds an
    /// `AtomicBool` + a `Mutex<Workspace>`, so there is no reference cycle to worry about.
    workspace: Py<PyWorkspace>,
    /// Revset-resolution context (mirrors `PyRepoView`): the workspace's name, root, and cached
    /// settings so `@`, `file()`, `mine()`, and aliases resolve the same way reads do — but here
    /// against the open `MutableRepo`, which sees this transaction's in-flight rewrites.
    workspace_name: WorkspaceNameBuf,
    workspace_root: PathBuf,
    revset_config: Arc<RevsetConfig>,
    /// The resolved jj settings, captured when the transaction opened. `fix` reads jj's own
    /// `fix.tools` and `revsets.fix` from here rather than inventing a second configuration.
    settings: Arc<UserSettings>,
    ignore_immutable: bool,
    /// `@`'s commit id when the transaction began. `commit` compares the post-commit `@` against
    /// this to decide whether the on-disk working copy needs a checkout.
    starting_wc_commit: Option<CommitId>,
}

impl PyTransaction {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        tx: Transaction,
        tx_open: Arc<AtomicBool>,
        workspace: Py<PyWorkspace>,
        workspace_name: WorkspaceNameBuf,
        workspace_root: PathBuf,
        revset_config: Arc<RevsetConfig>,
        settings: Arc<UserSettings>,
        ignore_immutable: bool,
        starting_wc_commit: Option<CommitId>,
    ) -> Self {
        Self {
            tx: RefCell::new(Some(tx)),
            tx_open,
            workspace,
            workspace_name,
            workspace_root,
            revset_config,
            settings,
            ignore_immutable,
            starting_wc_commit,
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
            &self.revset_config,
        )?;
        if commits.len() != 1 {
            return Err(RevsetError::new_err(format!(
                "revset '{revset_str}' resolved to {} revisions, expected exactly 1",
                commits.len()
            )));
        }
        Ok(commits.pop().expect("len checked == 1"))
    }

    /// Resolve a revset to **all** its matching commits, in revset order (the multi-revision
    /// variant of [`resolve_single`](Self::resolve_single); the `duplicate` target surface).
    fn resolve_many(&self, repo: &dyn Repo, revset_str: &str) -> PyResult<Vec<Commit>> {
        revset::evaluate(
            repo,
            revset_str,
            &self.workspace_name,
            &self.workspace_root,
            &self.revset_config,
        )
    }

    /// The roots of the branch carried by `jj rebase -b <target> -d <onto…>`: the commits reachable
    /// from `target` but not from any destination — `roots((<onto…>)..<target>)`. Built from commit
    /// hexes (not the user's revset strings) so operator precedence is unambiguous, then evaluated
    /// against the open `MutableRepo` through the same revset pipeline reads use. An empty result
    /// (the target is already an ancestor of the destinations) yields an empty `Roots`, which
    /// `move_commits` treats as a no-op.
    fn branch_roots(
        &self,
        repo: &dyn Repo,
        target: &Commit,
        new_parent_ids: &[CommitId],
    ) -> PyResult<Vec<CommitId>> {
        let dests = new_parent_ids
            .iter()
            .map(|id| id.hex())
            .collect::<Vec<_>>()
            .join("|");
        let expr = format!("roots(({})..{})", dests, target.id().hex());
        let roots = revset::evaluate(
            repo,
            &expr,
            &self.workspace_name,
            &self.workspace_root,
            &self.revset_config,
        )?;
        Ok(roots.iter().map(|c| c.id().clone()).collect())
    }

    /// Refuse a root or a configured immutable commit before a rewrite.
    fn check_rewritable(&self, repo: &dyn Repo, commits: &[Commit]) -> PyResult<()> {
        let root_id = repo.store().root_commit_id();
        if commits.iter().any(|commit| commit.id() == root_id) {
            return Err(ImmutableCommitError::new_err(
                "cannot rewrite the root commit",
            ));
        }
        if self.ignore_immutable {
            return Ok(());
        }
        let immutable = self
            .revset_config
            .immutable_expression(&self.workspace_name, &self.workspace_root)?;
        let no_extensions: &[Box<dyn jj_lib::revset::SymbolResolverExtension>] = &[];
        let resolver = jj_lib::revset::SymbolResolver::new(repo, no_extensions);
        let resolved = immutable
            .resolve_user_expression(repo, &resolver)
            .map_err(crate::errors::map_revset_err)?;
        let immutable_ids: HashSet<CommitId> = pollster::block_on(
            resolved
                .evaluate(repo)
                .map_err(crate::errors::map_revset_err)?
                .stream()
                .try_collect(),
        )
        .map_err(map_backend_err)?;
        if let Some(commit) = commits
            .iter()
            .find(|commit| immutable_ids.contains(commit.id()))
        {
            return Err(ImmutableCommitError::new_err(format!(
                "commit {} is immutable; change revset-aliases.immutable_heads() or use \
                 transaction(ignore_immutable=True)",
                commit.id().hex()
            )));
        }
        Ok(())
    }

    fn check_rewritable_revset(&self, repo: &dyn Repo, revset_str: &str) -> PyResult<()> {
        let commits = revset::evaluate(
            repo,
            revset_str,
            &self.workspace_name,
            &self.workspace_root,
            &self.revset_config,
        )?;
        self.check_rewritable(repo, &commits)
    }

    fn check_rewritable_descendants(&self, repo: &dyn Repo, roots: &[CommitId]) -> PyResult<()> {
        let mut commits = Vec::new();
        for root in roots {
            commits.extend(revset::evaluate(
                repo,
                &format!("{}::", root.hex()),
                &self.workspace_name,
                &self.workspace_root,
                &self.revset_config,
            )?);
        }
        self.check_rewritable(repo, &commits)
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
        self.check_rewritable(&*repo, std::slice::from_ref(&commit))?;
        let new_commit = pollster::block_on(
            repo.rewrite_commit(&commit)
                .set_description(message)
                .write(),
        )
        .map_err(map_backend_err)?;
        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;
        let data = CommitData::build(&*repo, &new_commit)?;
        data.to_dict(py)
    }

    /// Create a new commit on top of `parents` (each a single-revision revset) and point `@` at
    /// it, returning the new commit as a plain dict. With no parents, the new commit is a child of
    /// the current `@` (the common `jj new`). The new commit's tree is the merge of its parents'
    /// trees, so a multi-parent `new` is a merge.
    ///
    /// `edit` may abandon the old `@` if it was discardable, registering a rewrite, so we run
    /// `rebase_descendants()` before reading the result back (and `commit` re-runs it safely).
    /// The on-disk working copy is updated by `commit`'s checkout, since `@` moved.
    #[pyo3(name = "new", signature = (parents=None))]
    fn py_new<'py>(
        &self,
        py: Python<'py>,
        parents: Option<Vec<String>>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs).
        let repo = tx.repo_mut();

        let revsets = parents.unwrap_or_else(|| vec!["@".to_owned()]);
        let parent_commits: Vec<Commit> = revsets
            .iter()
            .map(|r| self.resolve_single(&*repo, r))
            .collect::<PyResult<_>>()?;
        let name = self.workspace_name.clone();

        let new_commit = if let [parent] = parent_commits.as_slice() {
            // Single parent: `check_out` is exactly `new_commit(vec![p], p.tree()).write()` + edit.
            pollster::block_on(repo.check_out(name, parent)).map_err(map_backend_err)?
        } else {
            let tree = pollster::block_on(merge_commit_trees(&*repo, &parent_commits))
                .map_err(map_backend_err)?;
            let parent_ids = parent_commits.iter().map(|c| c.id().clone()).collect();
            let new = pollster::block_on(repo.new_commit(parent_ids, tree).write())
                .map_err(map_backend_err)?;
            pollster::block_on(repo.edit(name, &new)).map_err(map_backend_err)?;
            new
        };
        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;
        let data = CommitData::build(&*repo, &new_commit)?;
        data.to_dict(py)
    }

    /// Point `@` at the existing commit named by `revset_str` (no new commit is written; contrast
    /// `new`), returning that commit as a plain dict. `MutableRepo::edit` may abandon the old `@`
    /// if it was discardable (registering a rewrite), so we `rebase_descendants()` before reading
    /// the result back; the target's own commit id is unchanged, but bookmarks around it may move.
    /// Editing the **root** returns `EditCommitError::RewriteRootCommit` → `ImmutableCommitError`.
    /// `@` moves to the target, so `commit`'s checkout updates the on-disk working copy.
    fn edit<'py>(&self, py: Python<'py>, revset_str: &str) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs).
        let repo = tx.repo_mut();
        let target = self.resolve_single(&*repo, revset_str)?;
        self.check_rewritable(&*repo, std::slice::from_ref(&target))?;
        pollster::block_on(repo.edit(self.workspace_name.clone(), &target))
            .map_err(map_edit_err)?;
        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;
        let target = self.resolve_single(&*repo, revset_str)?; // re-read post-rebase
        let data = CommitData::build(&*repo, &target)?;
        data.to_dict(py)
    }

    /// Abandon the commit named by `revset_str`; its children are rebased onto its parent(s).
    /// Returns nothing (the commit is gone). Abandoning `@` advances `@` to a fresh empty commit
    /// on top of the old parents, so `commit`'s checkout fires.
    ///
    /// `record_abandoned_commit` `assert_ne!`s on the root commit. `check_rewritable` prevents
    /// that panic and also enforces the configured immutable set.
    fn abandon(&self, revset_str: &str) -> PyResult<()> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs).
        let repo = tx.repo_mut();
        let target = self.resolve_single(&*repo, revset_str)?;
        self.check_rewritable(&*repo, std::slice::from_ref(&target))?;
        repo.record_abandoned_commit(&target);
        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;
        Ok(())
    }

    /// Rebase `commit` onto the new parents `onto` (each a single-revision revset), returning the
    /// rebased `commit` as a plain dict. `mode` selects which commits move, matching jj's flags:
    ///
    /// - `"source"` (default, `jj rebase -s`): `commit` **and all its descendants**
    ///   (`MoveCommitsTarget::Roots([commit])`).
    /// - `"revision"` (`jj rebase -r`): **only** `commit` (`MoveCommitsTarget::Commits([commit])`);
    ///   its children reattach to `commit`'s old parents.
    /// - `"branch"` (`jj rebase -b`): the whole branch — the roots of `onto..commit`
    ///   (`roots((<onto…>)..<commit>)`, the commits reachable from `commit` but not from any
    ///   destination) plus their descendants.
    ///
    /// In every mode the change id is preserved and the commit id changes; if `@` (or an ancestor of
    /// `@`) moves, `commit`'s checkout updates the on-disk working copy. `move_commits` records
    /// rewrites, so we `rebase_descendants()` before reading the result back (and `commit` re-runs it
    /// idempotently). Configured immutable revisions — and the root commit unconditionally — are
    /// rejected before any rewrite. An unknown `mode` is a `PyjutsuError`.
    #[pyo3(signature = (commit, onto, mode="source"))]
    fn rebase<'py>(
        &self,
        py: Python<'py>,
        commit: &str,
        onto: Vec<String>,
        mode: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs); in-memory graph work.
        let repo = tx.repo_mut();
        let target = self.resolve_single(&*repo, commit)?;
        let new_parent_ids = onto
            .iter()
            .map(|r| Ok(self.resolve_single(&*repo, r)?.id().clone()))
            .collect::<PyResult<Vec<CommitId>>>()?;
        let target = match mode {
            "source" => {
                self.check_rewritable_revset(&*repo, &format!("{}::", target.id().hex()))?;
                MoveCommitsTarget::Roots(vec![target.id().clone()])
            }
            "revision" => {
                self.check_rewritable(&*repo, std::slice::from_ref(&target))?;
                MoveCommitsTarget::Commits(vec![target.id().clone()])
            }
            "branch" => {
                let roots = self.branch_roots(&*repo, &target, &new_parent_ids)?;
                self.check_rewritable_descendants(&*repo, &roots)?;
                MoveCommitsTarget::Roots(roots)
            }
            other => {
                return Err(PyjutsuError::new_err(format!(
                    "rebase mode must be 'source', 'revision', or 'branch', got '{other}'"
                )));
            }
        };
        let loc = MoveCommitsLocation {
            new_parent_ids,
            new_child_ids: vec![],
            target,
        };
        pollster::block_on(move_commits(repo, &loc, &RebaseOptions::default()))
            .map_err(map_backend_err)?;
        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;
        let rebased = self.resolve_single(&*repo, commit)?; // re-read post-rebase (id changed)
        let data = CommitData::build(&*repo, &rebased)?;
        data.to_dict(py)
    }

    /// Squash `source`'s changes into `into` (single-revision revsets), returning the squashed
    /// `into` as a plain dict. Matches `jj squash --from <source> --into <into>`: `source` is
    /// abandoned when fully squashed and its descendants rebase onto its parent(s). With `message`
    /// the squashed commit takes it (newline-normalized in Python); without, `into`'s description is
    /// kept. A squash that *produces* a conflict is allowed (jj records it N-sided) — only the root
    /// guard and `source == into` are refused.
    ///
    /// `squash_commits` hands back a `CommitBuilder` that **holds the `&mut repo` borrow**, so we
    /// must `write()` it (releasing the borrow) before any further `repo.*` — in particular before
    /// `rebase_descendants()`. `Ok(None)` means nothing was selected to squash. Description-combining
    /// (jj's default when both sides describe) is the documented out-of-scope refinement: we keep
    /// `into`'s description unless `message` is given.
    #[pyo3(signature = (source, into, message=None))]
    fn squash<'py>(
        &self,
        py: Python<'py>,
        source: &str,
        into: &str,
        message: Option<&str>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs).
        let repo = tx.repo_mut();
        let src = self.resolve_single(&*repo, source)?;
        let dst = self.resolve_single(&*repo, into)?;
        self.check_rewritable(&*repo, &[src.clone(), dst.clone()])?;
        if src.id() == dst.id() {
            return Err(PyjutsuError::new_err("cannot squash a commit into itself"));
        }
        // Whole-commit selection: the entire source tree is moved (partial/interactive selection is
        // the out-of-scope refinement). `parent_tree` is computed before `src` is moved in.
        let sel = CommitWithSelection {
            selected_tree: src.tree(),
            parent_tree: pollster::block_on(src.parent_tree(&*repo)).map_err(map_backend_err)?,
            commit: src,
        };
        let squashed = pollster::block_on(squash_commits(
            repo,
            &[sel],
            &dst,
            /* keep_emptied = */ false,
        ))
        .map_err(map_backend_err)?
        .ok_or_else(|| PyjutsuError::new_err("nothing to squash"))?;
        let mut builder = squashed.commit_builder; // holds &mut repo until write()
        if let Some(msg) = message {
            builder = builder.set_description(msg);
        }
        pollster::block_on(builder.write()).map_err(map_backend_err)?; // releases the borrow
        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;
        let result = self.resolve_single(&*repo, into)?; // re-read the squashed `into`
        let data = CommitData::build(&*repo, &result)?;
        data.to_dict(py)
    }

    /// Replace `commit`'s content — or just `paths` — with `from_`'s (single-revision revsets),
    /// returning the rewritten `commit` as a plain dict. Matches `jj restore --from <from_> --into
    /// <commit> [paths…]`. The change id is preserved; the commit id changes. A restore that
    /// produces a conflict is allowed (jj records it). Rewriting the **root** panics, so we guard it.
    ///
    /// `restore_tree` is **async** (wrap in `pollster::block_on`, like `merge_commit_trees`). Its
    /// orientation is "matched paths come from `source`", so to restore `commit` *from* `from_` we
    /// pass `source = from_.tree()`, `destination = commit.tree()`: matched paths take `from_`'s
    /// content, the rest stay as `commit` had them. `paths=None` uses `EverythingMatcher` (whole
    /// tree → `from_`'s); otherwise a `FilesMatcher` scopes the restore to the given repo-paths.
    #[pyo3(signature = (commit, from_, paths=None))]
    fn restore<'py>(
        &self,
        py: Python<'py>,
        commit: &str,
        from_: &str,
        paths: Option<Vec<String>>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs).
        let repo = tx.repo_mut();
        let target = self.resolve_single(&*repo, commit)?;
        self.check_rewritable(&*repo, std::slice::from_ref(&target))?;
        let from = self.resolve_single(&*repo, from_)?;
        let from_tree = from.tree();
        let target_tree = target.tree();
        let new_tree = match &paths {
            None => pollster::block_on(restore_tree(
                &from_tree,
                &target_tree,
                "from".into(),
                "into".into(),
                &EverythingMatcher,
            )),
            Some(ps) => {
                // Repo-relative paths from the caller (user input, like a revset) → map parse
                // errors to `PyjutsuError`. The matcher's tree outlives the `restore_tree` call.
                let repo_paths = ps
                    .iter()
                    .map(|p| {
                        RepoPathBuf::from_relative_path(p)
                            .map_err(|e| PyjutsuError::new_err(e.to_string()))
                    })
                    .collect::<PyResult<Vec<RepoPathBuf>>>()?;
                let matcher = FilesMatcher::new(&repo_paths);
                pollster::block_on(restore_tree(
                    &from_tree,
                    &target_tree,
                    "from".into(),
                    "into".into(),
                    &matcher,
                ))
            }
        }
        .map_err(map_backend_err)?;
        pollster::block_on(repo.rewrite_commit(&target).set_tree(new_tree).write())
            .map_err(map_backend_err)?;
        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;
        let restored = self.resolve_single(&*repo, commit)?; // re-read post-rewrite (id changed)
        let data = CommitData::build(&*repo, &restored)?;
        data.to_dict(py)
    }

    /// Duplicate the commits named by `revsets` → the new commits as plain dicts, in the same
    /// (children-first) order. Matches `jj duplicate -r <revsets> [--onto <onto>]`: each
    /// duplicated commit copies its original's content and description with a **new** change id,
    /// and the originals stay in place. With `onto=None` (the default) the duplicated commits sit
    /// on their original parents (or on the other duplicated commits, preserving internal
    /// structure). With `onto` (one or more single-revision revsets) the roots of the duplicated
    /// set sit on those commits instead (a merge when several).
    ///
    /// Targets are ordered reverse-topologically (children before parents) exactly as jj-lib
    /// requires, then `rewrite::duplicate_commits(_onto_parents)` runs. Configured immutable
    /// revisions — and the root — are rejected before any write. Must be called inside the
    /// transaction's `with` block.
    #[pyo3(signature = (revsets, onto=None))]
    fn duplicate<'py>(
        &self,
        py: Python<'py>,
        revsets: Vec<String>,
        onto: Option<Vec<String>>,
    ) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs); in-memory graph work.
        let repo = tx.repo_mut();
        // Collect the target commits (dedup by id, first occurrence wins).
        let mut targets: Vec<Commit> = Vec::new();
        let mut seen: HashSet<CommitId> = HashSet::new();
        for expression in &revsets {
            for commit in self.resolve_many(&*repo, expression)? {
                if seen.insert(commit.id().clone()) {
                    targets.push(commit);
                }
            }
        }
        if targets.is_empty() {
            return Err(RevsetError::new_err(
                "duplicate needs at least one revision",
            ));
        }
        self.check_rewritable(&*repo, &targets)?;
        // Reverse topological order (children before parents) — jj-lib's documented requirement.
        // The walk must stay inside the target set: `topo_order_reverse` would otherwise pull in
        // every ancestor of the targets (and duplicate them all).
        let target_set: HashSet<CommitId> = targets.iter().map(|c| c.id().clone()).collect();
        let ordered = dag_walk::topo_order_reverse_ok(
            targets.into_iter().map(Ok::<Commit, PyErr>),
            |c: &Commit| c.id().clone(),
            |c: &Commit| match pollster::block_on(c.parents()) {
                Ok(parents) => parents
                    .into_iter()
                    .filter(|parent| target_set.contains(parent.id()))
                    .map(Ok::<Commit, PyErr>)
                    .collect::<Vec<_>>(),
                Err(e) => vec![Err(map_backend_err(e))],
            },
            |c: Commit| -> PyErr {
                PyjutsuError::new_err(format!(
                    "cycle in duplicate targets around {}",
                    c.id().hex()
                ))
            },
        )?;
        let ordered_ids: Vec<CommitId> = ordered.iter().map(|c| c.id().clone()).collect();

        let stats = match onto {
            None => pollster::block_on(rewrite::duplicate_commits_onto_parents(
                repo,
                &ordered_ids,
                &HashMap::new(),
            ))
            .map_err(map_backend_err)?,
            Some(onto_revsets) => {
                let mut onto_commits: Vec<Commit> = Vec::new();
                let mut seen_onto: HashSet<CommitId> = HashSet::new();
                for expression in &onto_revsets {
                    let commit = self.resolve_single(&*repo, expression)?;
                    if seen_onto.insert(commit.id().clone()) {
                        onto_commits.push(commit);
                    }
                }
                let onto_ids: Vec<CommitId> = onto_commits.iter().map(|c| c.id().clone()).collect();
                pollster::block_on(rewrite::duplicate_commits(
                    repo,
                    &ordered_ids,
                    &HashMap::new(),
                    &onto_ids,
                    &[],
                ))
                .map_err(map_backend_err)?
            }
        };
        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;
        // Return the duplicated commits in the same children-first order as the targets.
        let mut out = Vec::new();
        for original_id in ordered_ids {
            let duplicated = stats.duplicated_commits.get(&original_id).ok_or_else(|| {
                PyjutsuError::new_err(format!(
                    "duplicate produced no commit for {}",
                    original_id.hex()
                ))
            })?;
            out.push(CommitData::build(&*repo, duplicated)?);
        }
        out.iter().map(|d| d.to_dict(py)).collect()
    }

    /// Absorb the changes of the single commit named by `source` into the ancestors that
    /// introduced those lines (matches `jj absorb --from <source> [--into <revset>]`): each hunk
    /// moves to the closest mutable ancestor that last modified its lines, and hunks with no
    /// unique ancestor stay behind in `source`. Returns a plain dict:
    /// `{rewritten_source, rewritten_destinations, num_rebased, skipped_paths}`. `source`
    /// defaults to `@`; `into` defaults to `mutable()`, and only ancestors of `source` are
    /// considered (jj's own rule). The source is abandoned when it becomes empty and has no
    /// description.
    ///
    /// jj-lib owns the whole algorithm (`absorb.rs`): `AbsorbSource::from_commit` →
    /// `split_hunks_to_trees` (with the destination revset and an everything matcher — no
    /// fileset scoping in this first release) → `absorb_hunks`. Runs on the GIL
    /// (`MutableRepo` is `!Send`). Raises `RevsetError` unless `source` names exactly one
    /// revision.
    #[pyo3(signature = (source="@", into=None))]
    fn absorb<'py>(
        &self,
        py: Python<'py>,
        source: &str,
        into: Option<&str>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs); in-memory graph work.
        let repo = tx.repo_mut();
        let source_commit = self.resolve_single(&*repo, source)?;
        self.check_rewritable(&*repo, std::slice::from_ref(&source_commit))?;
        let absorb_source = pollster::block_on(jj_lib::absorb::AbsorbSource::from_commit(
            &*repo,
            source_commit,
        ))
        .map_err(map_backend_err)?;
        let destinations = crate::revset::resolve_expression(
            &*repo,
            into.unwrap_or("mutable()"),
            &self.workspace_name,
            &self.workspace_root,
            &self.revset_config,
        )?;
        let matcher = jj_lib::matchers::EverythingMatcher;
        let selected = pollster::block_on(jj_lib::absorb::split_hunks_to_trees(
            &*repo,
            &absorb_source,
            &destinations,
            &matcher,
        ))
        .map_err(crate::errors::to_py_err)?;
        let stats = pollster::block_on(jj_lib::absorb::absorb_hunks(
            repo,
            &absorb_source,
            selected.target_commits,
        ))
        .map_err(map_backend_err)?;
        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;

        let dict = PyDict::new(py);
        match &stats.rewritten_source {
            Some(commit) => dict.set_item(
                "rewritten_source",
                CommitData::build(&*repo, commit)?.to_dict(py)?,
            )?,
            None => dict.set_item("rewritten_source", None::<&str>)?,
        }
        let destinations_dicts: Vec<Bound<'py, PyDict>> = stats
            .rewritten_destinations
            .iter()
            .map(|c| CommitData::build(&*repo, c).and_then(|d| d.to_dict(py)))
            .collect::<PyResult<_>>()?;
        dict.set_item("rewritten_destinations", destinations_dicts)?;
        dict.set_item("num_rebased", stats.num_rebased)?;
        let skipped: Vec<(String, String)> = selected
            .skipped_paths
            .into_iter()
            .map(|(path, reason)| (path.as_internal_file_string().to_owned(), reason))
            .collect();
        dict.set_item("skipped_paths", skipped)?;
        Ok(dict)
    }

    /// Run jj's configured `fix.tools` over `revsets` and their descendants (`jj fix`), returning
    /// a plain dict `{rewrites, num_checked_commits, num_fixed_commits, tools}`. `rewrites` maps
    /// old commit id → new commit id.
    ///
    /// `revsets` empty ⇒ the `revsets.fix` setting, or jj-cli's `reachable(@, mutable())` default.
    /// `tools` restricts the run to those `fix.tools` entries by name (an unknown name is an
    /// error, so a typo cannot silently become a no-op). `paths` restricts which files are
    /// considered, like `jj fix [FILESETS]`. `include_unchanged_files` and `all_lines` mirror the
    /// CLI flags of the same names. Every root revision passes the immutable/root guard.
    ///
    /// jj-lib owns the graph half (`fix::fix_files`): it walks the descendants, deduplicates file
    /// content across commits, and rewrites — this can never create a conflict. `src/fix.rs`
    /// owns the tool half, which jj-cli would otherwise own. Runs on the GIL (`MutableRepo` is
    /// `!Send`); the tools themselves run on rayon threads inside `ParallelFileFixer`.
    #[pyo3(signature = (revsets, tools=None, paths=None, include_unchanged_files=false, all_lines=false))]
    fn fix<'py>(
        &self,
        py: Python<'py>,
        revsets: Vec<String>,
        tools: Option<Vec<String>>,
        paths: Option<Vec<String>>,
        include_unchanged_files: bool,
        all_lines: bool,
    ) -> PyResult<Bound<'py, PyDict>> {
        let plan = crate::fix::FixPlan::build(
            &self.settings,
            self.workspace_root.clone(),
            tools.as_deref(),
            paths.as_deref(),
            all_lines,
        )?;
        let default_revset;
        let revsets: &[String] = if revsets.is_empty() {
            default_revset = [crate::fix::configured_fix_revset(&self.settings)?];
            &default_revset
        } else {
            &revsets
        };

        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs).
        let repo = tx.repo_mut();
        let mut roots: Vec<CommitId> = Vec::new();
        let mut seen: HashSet<CommitId> = HashSet::new();
        for revset_str in revsets {
            for commit in self.resolve_many(&*repo, revset_str)? {
                if seen.insert(commit.id().clone()) {
                    roots.push(commit.id().clone());
                }
            }
        }
        if roots.is_empty() {
            return Err(RevsetError::new_err(format!(
                "revset {revsets:?} matched no revisions to fix"
            )));
        }
        // `fix_files` rewrites the roots *and* their descendants, so the guard covers both.
        self.check_rewritable_descendants(&*repo, &roots)?;

        let mut fixer = plan.fixer();
        let summary = pollster::block_on(jj_lib::fix::fix_files(
            roots,
            &*plan.matcher,
            include_unchanged_files,
            repo,
            &mut fixer,
        ))
        .map_err(crate::errors::to_py_err)?;
        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;

        let dict = PyDict::new(py);
        let rewrites = PyDict::new(py);
        for (old, new) in &summary.rewrites {
            rewrites.set_item(old.hex(), new.hex())?;
        }
        dict.set_item("rewrites", rewrites)?;
        dict.set_item("num_checked_commits", summary.num_checked_commits)?;
        dict.set_item("num_fixed_commits", summary.num_fixed_commits)?;
        dict.set_item("tools", plan.tool_names())?;
        Ok(dict)
    }

    /// The paths changed by the single revision named by `revset_str` (diffed against its
    /// merged-parent tree, like the read surface's `diff`), evaluated against the **open**
    /// `MutableRepo` — so it sees this transaction's in-flight rewrites, which the read surface
    /// (op-log based) cannot. This is the "what is this commit introducing" list a pre-commit
    /// hook filters on; the hook wiring passes it as the `paths=` payload. Runs on the GIL
    /// (`MutableRepo` is `!Send`, see module docs); the diff itself is in-memory tree walking +
    /// object reads, reusing `crate::diff::compute` so it can never disagree with `diff()` about
    /// a path.
    fn changed_paths(&self, revset_str: &str) -> PyResult<Vec<String>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs).
        let repo = tx.repo_mut();
        let commit = self.resolve_single(&*repo, revset_str)?;
        let data = diff::compute(&*repo, &commit)?;
        Ok(data.files.into_iter().map(|file| file.path).collect())
    }

    /// Resolve the conflict at `path` in `@` with the caller's `content`, returning the rewritten
    /// `@` as a plain dict. Matches `jj resolve` on the working-copy commit: jj-lib's
    /// `update_from_content` parses `content` (conflict markers still present are honored) and
    /// writes the new file ids into `@`'s tree, preserving the executable-bit/copy-id shape.
    /// The change id is preserved; the commit id changes, and the working copy is checked out to
    /// the new `@` when the transaction commits.
    ///
    /// `content` is decoded as UTF-8 on the Python side; non-UTF-8 file content is out of scope
    /// for this first release. Raises `ConflictError` if `path` is not a conflicted file at `@`,
    /// or `RevsetError`/`ImmutableCommitError` per the usual single-revision/immutability rules.
    fn resolve_conflict<'py>(
        &self,
        py: Python<'py>,
        path: &str,
        content: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs); in-memory graph work.
        let repo = tx.repo_mut();
        let path_buf = RepoPathBuf::from_relative_path(path)
            .map_err(|e| PyjutsuError::new_err(format!("invalid path '{path}': {e}")))?;
        let wc_id = repo
            .view()
            .get_wc_commit_id(&self.workspace_name)
            .cloned()
            .ok_or_else(|| {
                ConflictError::new_err("workspace has no working-copy commit to resolve")
            })?;
        let wc_commit = repo.store().get_commit(&wc_id).map_err(map_backend_err)?;
        self.check_rewritable(&*repo, std::slice::from_ref(&wc_commit))?;
        let tree = wc_commit.tree();
        let value = pollster::block_on(tree.path_value(&path_buf)).map_err(map_backend_err)?;
        let new_tree = pollster::block_on(crate::conflicts::resolved_tree(
            tree,
            &path_buf,
            value,
            content.as_bytes(),
        ))?;
        let new_commit =
            pollster::block_on(repo.rewrite_commit(&wc_commit).set_tree(new_tree).write())
                .map_err(map_backend_err)?;
        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;
        let data = CommitData::build(&*repo, &new_commit)?;
        data.to_dict(py)
    }

    /// Build the **partial tree** for a hunk-level selection of `commit`'s diff and return its
    /// (resolved) tree id as hex — the S2 primitive that `split` composes on. `selection` maps each
    /// changed path to either `None` (the whole file) or a list of 0-based hunk indices into that
    /// file's `diff(commit)` output. The returned tree is `commit`'s parent tree with the selected
    /// changes applied (parent + selected hunks), assembled off jj-lib's `MergedTreeBuilder` exactly
    /// like `split`'s selected side — but here just materialized as a tree, not written into a commit.
    ///
    /// Unlike `split`, this does **not** validate the selection is a proper subset (an empty
    /// selection yields the parent tree id, a full one the commit tree id) — it is the low-level
    /// "selection → tree" building block; `split` layers the empty/full guards on top. Runs on the
    /// GIL (`MutableRepo` is `!Send`, see module docs); the tree build itself is in-memory + object
    /// writes.
    fn select_tree(
        &self,
        commit: &str,
        selection: HashMap<String, FileSelection>,
    ) -> PyResult<String> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs).
        let repo = tx.repo_mut();
        let target = self.resolve_single(&*repo, commit)?;
        let parent_tree =
            pollster::block_on(target.parent_tree(&*repo)).map_err(map_backend_err)?;
        let commit_tree = target.tree();
        let selected_tree = pollster::block_on(build_split_side(
            &*repo,
            &parent_tree,
            &commit_tree,
            &selection,
            /* for_remainder = */ false,
        ))?;
        match selected_tree.tree_ids().as_resolved() {
            Some(id) => Ok(id.hex()),
            None => Err(PyjutsuError::new_err(
                "selected tree is conflicted; a hunk selection over resolved paths should not \
                 produce a conflict — report this",
            )),
        }
    }

    /// Split `commit` into **two commits** by a partial (hunk-level) selection of its diff, returning
    /// `(first, second)` as plain dicts. `first` holds only the **selected** change; `second` holds
    /// the **remainder**; together they reproduce `commit`'s tree. `selection` maps each changed path
    /// to `None` (whole file) or a list of 0-based hunk indices into that file's `diff(commit)` output
    /// (S3-A). This is the sub-file mutation primitive that `restore`'s whole-file `FilesMatcher`
    /// cannot express; a whole-file selection reproduces the path-scoped `restore` carve, so `split`
    /// subsumes it.
    ///
    /// `mode` picks the topology:
    /// - `"siblings"` (default): `first` is a **new** commit (fresh change id, no descendants) holding
    ///   the selected change; `second` is `commit` **rewritten in place** to the remainder — it keeps
    ///   its change id, bookmarks, descendants, and (if it was `@`) the working-copy pointer. Both are
    ///   children of `commit`'s original parent(s). This is what a "carve one lane into two siblings"
    ///   consumer (gitman) wants.
    /// - `"stacked"` (jj's own `jj split` default): `first` (selected) is a new child of the original
    ///   parent(s); `second` is `commit` reparented onto `first` with its tree unchanged, so its diff
    ///   vs `first` is exactly the remainder. `second` keeps its change id + descendants + `@`.
    ///
    /// The empty/full guards lean on jj-lib's `CommitWithSelection::is_empty_selection`/
    /// `is_full_selection`: an empty carve (nothing selected) and a full carve (everything selected —
    /// the second commit would be empty) are both refused with typed errors. Splitting the **root**
    /// raises `ImmutableCommitError`. Partial (hunk) selection is supported for plain modified/added
    /// text files; binary, symlink, conflicted, and removed files must be selected whole-file (`None`)
    /// — a hunk list on such a path raises a typed error. For a path `diff()` reports as
    /// `renamed`/`copied` (its `source` is set), pass whole-file `None`: the indices are only aligned
    /// with a plain same-path diff. Runs on the GIL (`MutableRepo` is `!Send`).
    #[pyo3(signature = (commit, selection, mode="siblings"))]
    fn split<'py>(
        &self,
        py: Python<'py>,
        commit: &str,
        selection: HashMap<String, FileSelection>,
        mode: &str,
    ) -> PyResult<(Bound<'py, PyDict>, Bound<'py, PyDict>)> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs); in-memory graph work + object
        // writes (the partial-file blobs and the two commit trees).
        let repo = tx.repo_mut();
        let target = self.resolve_single(&*repo, commit)?;
        self.check_rewritable(&*repo, std::slice::from_ref(&target))?;
        if selection.is_empty() {
            return Err(PyjutsuError::new_err(
                "split selection is empty; select at least one path (or hunk) to carve off",
            ));
        }
        let parent_ids: Vec<CommitId> = target.parent_ids().to_vec();
        let parent_tree =
            pollster::block_on(target.parent_tree(&*repo)).map_err(map_backend_err)?;
        let commit_tree = target.tree();

        // Build the selected side first and validate it is a *proper* subset (non-empty, non-full)
        // via jj-lib's own helpers on `CommitWithSelection`.
        let selected_tree = pollster::block_on(build_split_side(
            &*repo,
            &parent_tree,
            &commit_tree,
            &selection,
            /* for_remainder = */ false,
        ))?;
        let sel = CommitWithSelection {
            commit: target.clone(),
            selected_tree: selected_tree.clone(),
            parent_tree: parent_tree.clone(),
        };
        if sel.is_empty_selection() {
            return Err(PyjutsuError::new_err(
                "empty selection: the chosen hunks carve off no change; nothing would move to the \
                 first commit",
            ));
        }
        if sel.is_full_selection() {
            return Err(PyjutsuError::new_err(
                "full selection: the chosen hunks are the commit's entire change, so the second \
                 commit would be empty; that is a no-op, not a split",
            ));
        }

        let (first, second) = match mode {
            "siblings" => {
                let remainder_tree = pollster::block_on(build_split_side(
                    &*repo,
                    &parent_tree,
                    &commit_tree,
                    &selection,
                    /* for_remainder = */ true,
                ))?;
                // `first`: new sibling holding only the selected change (fresh change id).
                let first =
                    pollster::block_on(repo.new_commit(parent_ids.clone(), selected_tree).write())
                        .map_err(map_backend_err)?;
                // `second`: the original commit rewritten to the remainder — keeps its change id,
                // bookmarks, descendants, and `@`. Root of this rewrite, so `rebase_descendants`
                // below fixes up its descendants but not `second` itself.
                let second = pollster::block_on(
                    repo.rewrite_commit(&target)
                        .set_tree(remainder_tree)
                        .write(),
                )
                .map_err(map_backend_err)?;
                (first, second)
            }
            "stacked" => {
                // `first`: new commit holding the selected change, child of the original parent(s).
                let first =
                    pollster::block_on(repo.new_commit(parent_ids.clone(), selected_tree).write())
                        .map_err(map_backend_err)?;
                // `second`: the original commit reparented onto `first`, tree unchanged — its diff vs
                // `first` is exactly the remainder. Keeps its change id + descendants + `@`.
                let second = pollster::block_on(
                    repo.rewrite_commit(&target)
                        .set_parents(vec![first.id().clone()])
                        .write(),
                )
                .map_err(map_backend_err)?;
                (first, second)
            }
            other => {
                return Err(PyjutsuError::new_err(format!(
                    "split mode must be 'siblings' or 'stacked', got '{other}'"
                )));
            }
        };

        pollster::block_on(repo.rebase_descendants()).map_err(map_backend_err)?;
        let first_data = CommitData::build(&*repo, &first)?;
        let second_data = CommitData::build(&*repo, &second)?;
        Ok((first_data.to_dict(py)?, second_data.to_dict(py)?))
    }

    /// Create a **new** local bookmark `name` at the single revision named by `commit`, returning
    /// the new bookmark as a plain dict. Errors with `PyjutsuError` if a local bookmark of that
    /// name already exists (matches `jj bookmark create`, which refuses to clobber).
    ///
    /// Bookmark writes rewrite no commit and never move `@`, so there is no `rebase_descendants()`
    /// and no checkout: the view mutation is read straight back (§1.2). `set_local_bookmark_target`
    /// adds the target as a head, so pointing at a non-head commit is fine.
    fn create_bookmark<'py>(
        &self,
        py: Python<'py>,
        name: &str,
        commit: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs); pure in-memory view mutation.
        let repo = tx.repo_mut();
        let target = self.resolve_single(&*repo, commit)?;
        let ref_name = RefName::new(name);
        if !repo.get_local_bookmark(ref_name).is_absent() {
            return Err(PyjutsuError::new_err(format!(
                "bookmark '{name}' already exists"
            )));
        }
        repo.set_local_bookmark_target(ref_name, RefTarget::normal(target.id().clone()));
        let new_target = repo.get_local_bookmark(ref_name);
        BookmarkData::local(name, &new_target).to_dict(py)
    }

    /// Point local bookmark `name` at the single revision named by `commit`, creating it if absent
    /// (create-or-move; matches `jj bookmark set`). Identical to `create_bookmark` without the
    /// existence guard. Returns the bookmark as a plain dict.
    fn set_bookmark<'py>(
        &self,
        py: Python<'py>,
        name: &str,
        commit: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs); pure in-memory view mutation.
        let repo = tx.repo_mut();
        let target = self.resolve_single(&*repo, commit)?;
        let ref_name = RefName::new(name);
        repo.set_local_bookmark_target(ref_name, RefTarget::normal(target.id().clone()));
        let new_target = repo.get_local_bookmark(ref_name);
        BookmarkData::local(name, &new_target).to_dict(py)
    }

    /// Delete local bookmark `name` by setting its target absent (matches `jj bookmark delete`).
    /// Returns nothing. Errors with `PyjutsuError` if no such local bookmark exists, so a typo
    /// doesn't silently no-op.
    fn delete_bookmark(&self, name: &str) -> PyResult<()> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs); pure in-memory view mutation.
        let repo = tx.repo_mut();
        let ref_name = RefName::new(name);
        if repo.get_local_bookmark(ref_name).is_absent() {
            return Err(PyjutsuError::new_err(format!("no such bookmark '{name}'")));
        }
        repo.set_local_bookmark_target(ref_name, RefTarget::absent());
        Ok(())
    }

    /// Start tracking the remote-tracking bookmark `name@remote` (matches `jj bookmark track`),
    /// merging it into the local bookmark and flipping the remote ref's state to tracked. Returns
    /// the **remote** bookmark row. Errors with `PyjutsuError` if no such remote bookmark exists.
    fn track_bookmark<'py>(
        &self,
        py: Python<'py>,
        name: &str,
        remote: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs); pure in-memory view mutation.
        let repo = tx.repo_mut();
        let symbol = RefName::new(name).to_remote_symbol(RemoteName::new(remote));
        if repo.get_remote_bookmark(symbol).target.is_absent() {
            return Err(PyjutsuError::new_err(format!(
                "no such remote bookmark '{name}@{remote}'"
            )));
        }
        pollster::block_on(repo.track_remote_bookmark(symbol)).map_err(map_backend_err)?;
        let remote_ref = repo.get_remote_bookmark(symbol);
        BookmarkData::remote(name, remote, &remote_ref).to_dict(py)
    }

    /// Stop tracking the remote-tracking bookmark `name@remote` (matches `jj bookmark untrack`),
    /// flipping the remote ref's state to untracked. Returns the **remote** bookmark row. Errors
    /// with `PyjutsuError` if no such remote bookmark exists. `untrack_remote_bookmark` returns
    /// `()`, so there is no fallible jj-lib call to map here.
    fn untrack_bookmark<'py>(
        &self,
        py: Python<'py>,
        name: &str,
        remote: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs); pure in-memory view mutation.
        let repo = tx.repo_mut();
        let symbol = RefName::new(name).to_remote_symbol(RemoteName::new(remote));
        if repo.get_remote_bookmark(symbol).target.is_absent() {
            return Err(PyjutsuError::new_err(format!(
                "no such remote bookmark '{name}@{remote}'"
            )));
        }
        repo.untrack_remote_bookmark(symbol);
        let remote_ref = repo.get_remote_bookmark(symbol);
        BookmarkData::remote(name, remote, &remote_ref).to_dict(py)
    }

    /// Commit the transaction with `description`, publishing exactly one operation, and return
    /// the new head operation id. Centralizes `rebase_descendants()` so every rewriting mutation
    /// is safe against `Transaction::commit`'s `!has_rewrites()` assert (landmine #1: a violation
    /// aborts the process); for a non-rewriting tx it is a harmless no-op. Raises if already closed.
    ///
    /// If the transaction moved `@`, the on-disk working copy is checked out to the new `@`
    /// **after** the operation is published (off the GIL, on the `Send` `Workspace`), so a later
    /// `jj` command on the same repo sees a working copy in lockstep with the repo head. This is
    /// the shared piece every `@`-rewriting mutation (`new`, `describe` of `@`, edit, abandon, …)
    /// relies on. A failure in that post-publish checkout is surfaced as `StaleWorkingCopyError`
    /// (the operation is already in the log; the caller reconciles with `update_stale`) rather than
    /// a generic error.
    fn commit(&self, py: Python<'_>, description: String) -> PyResult<String> {
        let mut tx = self.take()?;
        // The native transaction is now consumed (the cell is `None`). Release the workspace's
        // single-tx slot *immediately*, before any fallible work below: once `take` has emptied the
        // cell, `Drop` can no longer release the slot (it guards on `is_some()`), so a failure in
        // `rebase_descendants`/`commit` would otherwise wedge the workspace permanently. Mirrors
        // `rollback`, which releases right after `take`.
        self.release_slot();
        // NOTE: on the GIL — `Transaction` is `!Send` (see module docs), so it cannot be moved
        // into `allow_threads`. The op-store write here is light; heavy I/O is off-GIL elsewhere.
        pollster::block_on(tx.repo_mut().rebase_descendants()).map_err(map_backend_err)?;
        let new_repo = pollster::block_on(tx.commit(description)).map_err(map_backend_err)?;
        // From here the operation is PUBLISHED — it is in the op log regardless of what follows.
        let op_hex = new_repo.operation().id().hex();

        let new_wc_commit = new_repo
            .view()
            .get_wc_commit_id(&self.workspace_name)
            .cloned();
        if new_wc_commit != self.starting_wc_commit
            && let Some(new_id) = new_wc_commit
        {
            let new_commit = new_repo
                .store()
                .get_commit(&new_id)
                .map_err(map_backend_err)?;
            let op_id = new_repo.operation().id().clone();
            // The op already landed; a checkout failure means the on-disk WC is now stale, not that
            // the commit failed. Surface it as `StaleWorkingCopyError` carrying the published op id
            // so the caller can `update_stale` rather than mistaking a landed op for a failed one.
            self.workspace
                .bind(py)
                .borrow()
                .checkout_wc(py, op_id, &new_commit)
                .map_err(|e| {
                    StaleWorkingCopyError::new_err(format!(
                        "operation {op_hex} was published but the working copy could not be \
                         checked out ({e}); run update_stale to reconcile"
                    ))
                })?;
        }
        Ok(op_hex)
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

/// Assemble one side of a `split` (or the `select_tree` result) as a `MergedTree`.
///
/// With `for_remainder = false` this builds the **selected** side: base = `parent_tree`, and each
/// listed path is overridden with *(parent content + the selected hunks)*. With `for_remainder =
/// true` it builds the **remainder** side: base = `commit_tree`, and each listed path is overridden
/// with *(parent content + the **un**selected hunks)* — the commit's content with the selected hunks
/// reverted. A path **not** listed keeps the base's value, so a wholly-unselected changed path lands
/// on the parent value in the selected tree and the commit value in the remainder — i.e. entirely on
/// the remainder side. The two sides therefore reassemble the original commit.
///
/// Only resolved changed paths may be listed. A whole-file selection (`None`) copies the merged tree
/// value verbatim (so binary/symlink/etc. whole-file moves need no hunk assembly); a hunk list is
/// materialized by `partial_file_value`.
async fn build_split_side(
    repo: &dyn Repo,
    parent_tree: &MergedTree,
    commit_tree: &MergedTree,
    selection: &HashMap<String, FileSelection>,
    for_remainder: bool,
) -> PyResult<MergedTree> {
    let store = repo.store();
    let base = if for_remainder {
        commit_tree.clone()
    } else {
        parent_tree.clone()
    };
    let mut builder = MergedTreeBuilder::new(base);
    for (path_str, sel) in selection {
        let path = RepoPathBuf::from_relative_path(path_str)
            .map_err(|e| PyjutsuError::new_err(format!("invalid path '{path_str}': {e}")))?;
        let before_value = parent_tree
            .path_value(&path)
            .await
            .map_err(map_backend_err)?;
        let after_value = commit_tree
            .path_value(&path)
            .await
            .map_err(map_backend_err)?;
        if before_value == after_value {
            return Err(PyjutsuError::new_err(format!(
                "path '{path_str}' is not changed in the commit; a split selection may only list \
                 changed paths"
            )));
        }
        match sel {
            // Whole-file: the file's entire change goes to the selected side. The selected tree takes
            // the commit's value, the remainder reverts to the parent's (an absent value removes the
            // path — e.g. a wholly-selected added file is not in the remainder).
            None => {
                let value = if for_remainder {
                    before_value
                } else {
                    after_value
                };
                builder.set_or_remove(path, value);
            }
            Some(indices) => {
                let file = partial_file_value(
                    store,
                    &path,
                    path_str,
                    &before_value,
                    &after_value,
                    indices,
                    for_remainder,
                )
                .await?;
                builder.set_or_remove(path, Merge::normal(file));
            }
        }
    }
    builder.write_tree().await.map_err(map_backend_err)
}

/// Materialize the partial content of one file for a hunk-level split side, returning its
/// `TreeValue::File`. Requires a resolved **text file present in the commit** (an "after" side to
/// carve and a file identity — executable bit + copy id — to preserve); binary, symlink, conflicted,
/// and removed files raise a typed error so the caller must select them whole-file instead.
async fn partial_file_value(
    store: &Store,
    path: &RepoPath,
    path_str: &str,
    before_value: &MergedTreeValue,
    after_value: &MergedTreeValue,
    indices: &[usize],
    for_remainder: bool,
) -> PyResult<TreeValue> {
    let (executable, copy_id) = match after_value.as_resolved() {
        Some(Some(TreeValue::File {
            executable,
            copy_id,
            ..
        })) => (*executable, copy_id.clone()),
        _ => {
            return Err(PyjutsuError::new_err(format!(
                "path '{path_str}': hunk-level selection requires a regular file present in the \
                 commit; select binary, symlink, conflicted, or removed files whole-file (None)"
            )));
        }
    };
    let before_bytes = read_text(store, path, before_value).await?.ok_or_else(|| {
        PyjutsuError::new_err(format!(
            "path '{path_str}': hunk-level selection requires text on the parent side (it is binary \
             or not a regular file); select it whole-file (None)"
        ))
    })?;
    let after_bytes = read_text(store, path, after_value).await?.ok_or_else(|| {
        PyjutsuError::new_err(format!(
            "path '{path_str}': hunk-level selection requires text content (the file is binary); \
             select it whole-file (None)"
        ))
    })?;
    let content = assemble_selected_content(
        &before_bytes,
        &after_bytes,
        indices,
        for_remainder,
        path_str,
    )?;
    let id = store
        .write_file(path, &mut content.as_slice())
        .await
        .map_err(map_backend_err)?;
    Ok(TreeValue::File {
        id,
        executable,
        copy_id,
    })
}

/// Reconstruct a file's content for one split side from a line-level diff of its parent/commit bytes.
///
/// Walks `ContentDiff::by_line`'s hunks in order (the same decomposition `diff()` emits): a matching
/// span is copied through; the k-th *changed* span (0-based — the same index a consumer sees in
/// `diff(commit)`'s hunk list) contributes its **after** side when it is selected for this side, else
/// its **before** side. For the selected tree that means "after for selected hunks"; for the
/// remainder it is the complement ("after for *un*selected hunks"), so the two sides partition the
/// change. An index that names no changed span is a typed error rather than a silent no-op.
fn assemble_selected_content(
    before: &[u8],
    after: &[u8],
    indices: &[usize],
    for_remainder: bool,
    path_str: &str,
) -> PyResult<Vec<u8>> {
    let selected: HashSet<usize> = indices.iter().copied().collect();
    let inputs = [before, after];
    let diff = ContentDiff::by_line(inputs);
    let mut out = Vec::new();
    let mut k = 0usize; // index over changed spans, matching `diff()`'s hunk order
    for hunk in diff.hunks() {
        match hunk.kind {
            DiffHunkKind::Matching => out.extend_from_slice(hunk.contents[0]),
            DiffHunkKind::Different => {
                // Selected side keeps the after content for selected hunks; the remainder is the
                // complement. `!=` is XOR over the two bools.
                let take_after = selected.contains(&k) != for_remainder;
                out.extend_from_slice(hunk.contents[usize::from(take_after)]);
                k += 1;
            }
        }
    }
    if let Some(&bad) = indices.iter().find(|&&i| i >= k) {
        return Err(PyjutsuError::new_err(format!(
            "path '{path_str}': hunk index {bad} out of range (the file has {k} hunk(s) in this \
             commit's diff)"
        )));
    }
    Ok(out)
}
