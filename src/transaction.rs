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

use jj_lib::backend::CommitId;
use jj_lib::commit::Commit;
use jj_lib::matchers::{EverythingMatcher, FilesMatcher};
use jj_lib::object_id::ObjectId;
use jj_lib::op_store::RefTarget;
use jj_lib::ref_name::{RefName, RemoteName, WorkspaceNameBuf};
use jj_lib::repo::Repo;
use jj_lib::repo_path::RepoPathBuf;
use jj_lib::rewrite::{
    CommitWithSelection, MoveCommitsLocation, MoveCommitsTarget, RebaseOptions, merge_commit_trees,
    move_commits, restore_tree, squash_commits,
};
use jj_lib::transaction::Transaction;

use crate::convert::{BookmarkData, CommitData};
use crate::errors::{
    ImmutableCommitError, PyjutsuError, RevsetError, StaleWorkingCopyError, map_backend_err,
    map_edit_err,
};
use crate::revset;
use crate::workspace::PyWorkspace;

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
    /// Revset-resolution context (mirrors `PyRepoView`): the workspace's name + root + author
    /// email, so `@`, `file()`, `mine()`, … resolve the same way reads do — but here against the
    /// open `MutableRepo`, which sees this transaction's in-flight rewrites.
    workspace_name: WorkspaceNameBuf,
    workspace_root: PathBuf,
    user_email: String,
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
        user_email: String,
        starting_wc_commit: Option<CommitId>,
    ) -> Self {
        Self {
            tx: RefCell::new(Some(tx)),
            tx_open,
            workspace,
            workspace_name,
            workspace_root,
            user_email,
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
            &self.user_email,
        )?;
        Ok(roots.iter().map(|c| c.id().clone()).collect())
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
    /// `record_abandoned_commit` `assert_ne!`s on the root commit (it would **panic**, surfacing as
    /// a generic `PanicException` through PyO3), so we guard the root explicitly and raise
    /// `ImmutableCommitError`. Only the root is enforced — jj's configurable `immutable_heads()`
    /// set is CLI workflow policy, which the thin layer deliberately does not replicate.
    fn abandon(&self, revset_str: &str) -> PyResult<()> {
        let mut guard = self.tx.borrow_mut();
        let tx = guard
            .as_mut()
            .ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
        // On the GIL — `MutableRepo` is `!Send` (see module docs).
        let repo = tx.repo_mut();
        let target = self.resolve_single(&*repo, revset_str)?;
        if target.id() == repo.store().root_commit_id() {
            return Err(ImmutableCommitError::new_err("cannot abandon the root commit"));
        }
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
    /// idempotently). Rewriting the **root** panics in `record_rewritten_commit`, so we guard it and
    /// raise `ImmutableCommitError`, like `abandon`. An unknown `mode` is a `PyjutsuError`.
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
        if target.id() == repo.store().root_commit_id() {
            return Err(ImmutableCommitError::new_err("cannot rebase the root commit"));
        }
        let new_parent_ids = onto
            .iter()
            .map(|r| Ok(self.resolve_single(&*repo, r)?.id().clone()))
            .collect::<PyResult<Vec<CommitId>>>()?;
        let target = match mode {
            "source" => MoveCommitsTarget::Roots(vec![target.id().clone()]),
            "revision" => MoveCommitsTarget::Commits(vec![target.id().clone()]),
            "branch" => MoveCommitsTarget::Roots(self.branch_roots(&*repo, &target, &new_parent_ids)?),
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
        let root_id = repo.store().root_commit_id().clone();
        if src.id() == &root_id || dst.id() == &root_id {
            return Err(ImmutableCommitError::new_err("cannot squash the root commit"));
        }
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
        if target.id() == repo.store().root_commit_id() {
            return Err(ImmutableCommitError::new_err("cannot restore the root commit"));
        }
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
        repo.track_remote_bookmark(symbol).map_err(map_backend_err)?;
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

        let new_wc_commit = new_repo.view().get_wc_commit_id(&self.workspace_name).cloned();
        if new_wc_commit != self.starting_wc_commit
            && let Some(new_id) = new_wc_commit
        {
            let new_commit = new_repo.store().get_commit(&new_id).map_err(map_backend_err)?;
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
