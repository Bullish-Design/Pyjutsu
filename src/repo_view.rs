//! `PyRepoView` — an immutable repo at one operation; the home of every read (concept §4, M1).
//!
//! Backed by `Arc<ReadonlyRepo>`, which is `Send + Sync`, so reads release the GIL with no
//! `Mutex`. The view also carries the originating workspace's name + root + user email so
//! revset reads (`@`, `author()`, …) resolve in the right context without re-touching the
//! `Workspace` handle. Reads never snapshot (M1): they observe the chosen operation as-is.
//!
//! Read shape: evaluate jj-lib into plain `CommitData` **off the GIL** (`allow_threads`), then
//! convert to dicts after re-acquiring it. The Python layer validates the dicts into models.

use std::path::PathBuf;
use std::sync::Arc;

use futures::StreamExt as _;
use pyo3::PyErr;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use sha1::{Digest as _, Sha1};

use jj_lib::backend::CommitId;
use jj_lib::commit::Commit;
use jj_lib::merge::Merge;
use jj_lib::merged_tree::MergedTree;
use jj_lib::object_id::ObjectId;
use jj_lib::op_walk;
use jj_lib::ref_name::WorkspaceNameBuf;
use jj_lib::repo::{ReadonlyRepo, Repo};
use jj_lib::rewrite::merge_commit_trees;

use crate::convert::{BookmarkData, CommitData, ConflictData, OperationData, merge_tree_id_hex};
use crate::diff::{self, DiffData, FileChangeData};
use crate::diff_stat::{self, DiffStatData};
use crate::errors::{ConflictError, PyjutsuError, RevsetError, map_backend_err};
use crate::revset::{self, RevsetConfig};

/// Opaque handle to a `ReadonlyRepo` at a fixed operation, plus the workspace context reads
/// need. Cheap to clone-share (the repo is `Arc`).
#[pyclass(module = "pyjutsu._pyjutsu")]
pub(crate) struct PyRepoView {
    pub(crate) repo: Arc<ReadonlyRepo>,
    workspace_name: WorkspaceNameBuf,
    workspace_root: PathBuf,
    revset_config: Arc<RevsetConfig>,
}

impl PyRepoView {
    /// Construct a view. Called by `PyWorkspace::head_view`/`at_operation`.
    pub(crate) fn new(
        repo: Arc<ReadonlyRepo>,
        workspace_name: WorkspaceNameBuf,
        workspace_root: PathBuf,
        revset_config: Arc<RevsetConfig>,
    ) -> Self {
        Self {
            repo,
            workspace_name,
            workspace_root,
            revset_config,
        }
    }

    /// Resolve `revset_str` to **exactly one** commit, or raise `RevsetError`. Shared by the
    /// single-commit reads (`diff`/`diff_stat`, and the two ends of their `*_between` overloads).
    /// Call inside `allow_threads` — it touches the backend.
    pub(crate) fn resolve_single(&self, revset_str: &str) -> PyResult<Commit> {
        let commits = revset::evaluate(
            self.repo.as_ref(),
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
        Ok(commits.into_iter().next().expect("len checked == 1"))
    }

    /// Evaluate a revset and build a `CommitData` per match — all off the GIL. `limit` caps the
    /// result before the (backend-touching) `CommitData` build, so it bounds the work too.
    fn eval_to_data(
        &self,
        py: Python<'_>,
        revset_str: &str,
        limit: Option<usize>,
    ) -> PyResult<Vec<CommitData>> {
        py.allow_threads(|| {
            let repo = self.repo.as_ref();
            let mut commits = revset::evaluate(
                repo,
                revset_str,
                &self.workspace_name,
                &self.workspace_root,
                &self.revset_config,
            )?;
            if let Some(limit) = limit {
                commits.truncate(limit);
            }
            commits
                .iter()
                .map(|c| CommitData::build(repo, c))
                .collect::<Result<Vec<_>, PyErr>>()
        })
    }
}

#[pymethods]
impl PyRepoView {
    /// Read `@` — the originating workspace's working-copy commit — as a plain dict. Read-only:
    /// observes the view's operation without snapshotting the on-disk working copy.
    fn working_copy<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let commit_id = self
            .repo
            .view()
            .get_wc_commit_id(&self.workspace_name)
            .cloned()
            .ok_or_else(|| {
                PyjutsuError::new_err(format!(
                    "workspace '{}' has no working-copy commit",
                    self.workspace_name.as_str()
                ))
            })?;
        let data = py.allow_threads(|| {
            let repo = self.repo.as_ref();
            let commit = repo
                .store()
                .get_commit(&commit_id)
                .map_err(map_backend_err)?;
            CommitData::build(repo, &commit)
        })?;
        data.to_dict(py)
    }

    /// Resolve a revset that must name **exactly one** revision → one commit dict. More or
    /// fewer matches is a `RevsetError` (mirrors the CLI's "must resolve to a single revision").
    fn resolve<'py>(&self, py: Python<'py>, revset_str: &str) -> PyResult<Bound<'py, PyDict>> {
        let mut data = self.eval_to_data(py, revset_str, None)?;
        if data.len() != 1 {
            return Err(RevsetError::new_err(format!(
                "revset '{revset_str}' resolved to {} revisions, expected exactly 1",
                data.len()
            )));
        }
        data.pop().expect("len checked == 1").to_dict(py)
    }

    /// Evaluate a revset → a list of commit dicts in revset order (newest first), capped at
    /// `limit` if given.
    #[pyo3(signature = (revset_str, limit=None))]
    fn log<'py>(
        &self,
        py: Python<'py>,
        revset_str: &str,
        limit: Option<usize>,
    ) -> PyResult<Bound<'py, PyList>> {
        let data = self.eval_to_data(py, revset_str, limit)?;
        let dicts: Vec<Bound<'py, PyDict>> = data
            .iter()
            .map(|d| d.to_dict(py))
            .collect::<PyResult<_>>()?;
        PyList::new(py, dicts)
    }

    /// The op log as seen from this view's operation: that operation and its ancestors in
    /// reverse-topological (newest-first) order, capped at `limit`.
    #[pyo3(signature = (limit=None))]
    fn operations<'py>(
        &self,
        py: Python<'py>,
        limit: Option<usize>,
    ) -> PyResult<Bound<'py, PyList>> {
        let data = py.allow_threads(|| -> PyResult<Vec<OperationData>> {
            let head = self.repo.operation().clone();
            // jj-lib 0.42 made `walk_ancestors` stream-based; drive it synchronously off the GIL.
            pollster::block_on(async {
                let mut out = Vec::new();
                let mut ops = std::pin::pin!(op_walk::walk_ancestors(std::slice::from_ref(&head)));
                while let Some(op) = ops.next().await {
                    if limit.is_some_and(|n| out.len() >= n) {
                        break;
                    }
                    out.push(OperationData::build(&op.map_err(map_backend_err)?));
                }
                Ok(out)
            })
        })?;
        let dicts: Vec<Bound<'py, PyDict>> = data
            .iter()
            .map(|d| d.to_dict(py))
            .collect::<PyResult<_>>()?;
        PyList::new(py, dicts)
    }

    /// The id of the operation this view is at (its head operation).
    fn operation_id(&self) -> String {
        self.repo.operation().id().hex()
    }

    /// Walk the evolution of the change named by `change_id_str` (z-k form) → one plain entry
    /// dict per step, newest first, capped at `limit` (matches `jj evolog`). Each entry is
    /// `{commit, operation}`: the commit carries its `predecessor_ids` (the only reads that
    /// populate them — see `src/evolution.rs`), the operation is the one that created or last
    /// rewrote it. An unknown change id yields an empty list.
    #[pyo3(signature = (change_id_str, limit=None))]
    fn evolution<'py>(
        &self,
        py: Python<'py>,
        change_id_str: &str,
        limit: Option<usize>,
    ) -> PyResult<Vec<Bound<'py, PyDict>>> {
        crate::evolution::evolution(self, py, change_id_str, limit)
    }

    /// The shortest unique prefix of `id` (a hex commit id or a z-k change id) within the whole
    /// repository, disambiguated against other ids **and** bookmark/tag names — the same answer
    /// the CLI's `commit_id.shortest()` / `change_id.shortest()` templates give, minus the
    /// `visible()` scoping (see `src/id_prefix.rs` for the C3 decision). An unknown id yields its
    /// full length.
    fn shortest_prefix(&self, py: Python<'_>, id: &str) -> PyResult<String> {
        let id = id.to_owned();
        py.allow_threads(move || crate::id_prefix::shortest_prefix(self.repo.as_ref(), &id))
    }

    /// All bookmarks at this operation: one row per local bookmark (`remote=None`) followed by
    /// one per remote-tracking ref. Local rows come first; within each, jj's name order.
    fn bookmarks<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let data = py.allow_threads(|| {
            let view = self.repo.view();
            let mut rows: Vec<BookmarkData> = view
                .local_bookmarks()
                .map(|(name, target)| BookmarkData::local(name.as_str(), target))
                .collect();
            rows.extend(view.all_remote_bookmarks().map(|(symbol, remote_ref)| {
                BookmarkData::remote(symbol.name.as_str(), symbol.remote.as_str(), remote_ref)
            }));
            rows
        });
        let dicts: Vec<Bound<'py, PyDict>> = data
            .iter()
            .map(|d| d.to_dict(py))
            .collect::<PyResult<_>>()?;
        PyList::new(py, dicts)
    }

    /// The conflicts in the single commit named by `revset_str` — one row per conflicted path,
    /// faithfully N-sided (concept §8.9). `RevsetError` if the revset isn't exactly one commit.
    fn conflicts<'py>(&self, py: Python<'py>, revset_str: &str) -> PyResult<Bound<'py, PyList>> {
        let data = py.allow_threads(|| -> PyResult<Vec<ConflictData>> {
            let repo = self.repo.as_ref();
            let commits = revset::evaluate(
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
            let mut out = Vec::new();
            for (path, value) in commits[0].tree().conflicts() {
                let merge = value.map_err(map_backend_err)?;
                out.push(ConflictData::new(
                    path.as_internal_file_string().to_owned(),
                    merge.num_sides(),
                    merge.removes().count(),
                ));
            }
            Ok(out)
        })?;
        let dicts: Vec<Bound<'py, PyDict>> = data
            .iter()
            .map(|d| d.to_dict(py))
            .collect::<PyResult<_>>()?;
        PyList::new(py, dicts)
    }

    /// Materialize the file at `path` in the single commit named by `revset_str` into marked text
    /// (the content `jj file show` prints, conflict markers included), in the requested
    /// `style` (`"diff"`/`"snapshot"`/`"git"`). A plain (non-conflicted) file yields its raw
    /// content. `RevsetError` unless `revset_str` names exactly one commit; `ConflictError` for a
    /// path that is not a readable file at that revision.
    fn conflict_content<'py>(
        &self,
        py: Python<'py>,
        path: &str,
        revset_str: &str,
        style: &str,
    ) -> PyResult<String> {
        let style = crate::conflicts::style_from_str(style)?;
        let path_buf = jj_lib::repo_path::RepoPathBuf::from_relative_path(path)
            .map_err(|e| PyjutsuError::new_err(format!("invalid path '{path}': {e}")))?;
        py.allow_threads(|| {
            let commit = self.resolve_single(revset_str)?;
            let repo = self.repo.as_ref();
            let store = repo.store();
            let tree = commit.tree();
            let value = pollster::block_on(tree.path_value(&path_buf)).map_err(map_backend_err)?;
            pollster::block_on(crate::conflicts::materialize_to_string(
                store,
                &path_buf,
                value,
                tree.labels(),
                style,
            ))
        })
    }

    /// Parse the conflicted file at `path` in the single commit named by `revset_str` back into
    /// its sides (no markers): one string per merge term, adds first then removes (a regular 3-way
    /// conflict yields `[side_a, base, side_b]`). `RevsetError` unless `revset_str` names exactly
    /// one commit; `ConflictError` if the path is not a conflicted file.
    fn conflict_sides(
        &self,
        py: Python<'_>,
        path: &str,
        revset_str: &str,
    ) -> PyResult<Vec<String>> {
        crate::conflicts::sides(self, py, path, revset_str)
    }

    /// Read the file at `path` in the single commit named by `revset_str` as raw **bytes** (the
    /// caller decodes; binary content round-trips intact). Matches `jj file show -r <rev> <path>`.
    /// `RevsetError` unless `revset_str` names exactly one commit; `ConflictError` for a conflicted
    /// path (read it with `conflict_content` instead); a clear error for a path that is absent or
    /// not a regular file at that revision.
    fn file_content(&self, py: Python<'_>, path: &str, revset_str: &str) -> PyResult<Vec<u8>> {
        let path_buf = jj_lib::repo_path::RepoPathBuf::from_relative_path(path)
            .map_err(|e| PyjutsuError::new_err(format!("invalid path '{path}': {e}")))?;
        py.allow_threads(|| {
            let commit = self.resolve_single(revset_str)?;
            let repo = self.repo.as_ref();
            let store = repo.store();
            let tree = commit.tree();
            let value = pollster::block_on(tree.path_value(&path_buf)).map_err(map_backend_err)?;
            let materialized = pollster::block_on(jj_lib::conflicts::materialize_tree_value(
                store,
                &path_buf,
                value,
                tree.labels(),
            ))
            .map_err(map_backend_err)?;
            match materialized {
                jj_lib::conflicts::MaterializedTreeValue::File(mut file) => {
                    pollster::block_on(file.read_all(&path_buf)).map_err(map_backend_err)
                }
                jj_lib::conflicts::MaterializedTreeValue::Symlink { target, .. } => {
                    Ok(target.into_bytes())
                }
                jj_lib::conflicts::MaterializedTreeValue::FileConflict(_) => {
                    Err(ConflictError::new_err(format!(
                        "path '{path}' is conflicted at this revision; read it with \
                         conflict_content() to see the marked text"
                    )))
                }
                _ => Err(PyjutsuError::new_err(format!(
                    "path '{path}' is not a regular file at this revision"
                ))),
            }
        })
    }

    /// List the files in the single commit named by `revset_str` (repo-relative, sorted). With
    /// `paths` given, only the files matching those fileset expressions are listed — the same
    /// `jj file list -r <rev> <filesets>…` behavior (a bare name is a path prefix, `glob:`/etc.
    /// work, and several patterns union). `RevsetError` unless `revset_str` names exactly one
    /// commit; a malformed fileset is a `PyjutsuError`.
    fn file_list(
        &self,
        py: Python<'_>,
        revset_str: &str,
        paths: Option<Vec<String>>,
    ) -> PyResult<Vec<String>> {
        let workspace_root = self.workspace_root.clone();
        py.allow_threads(|| {
            let commit = self.resolve_single(revset_str)?;
            let tree = commit.tree();
            // The same fileset parsing the snapshot and `fix` use (`src/fileset.rs`), evaluated
            // against repo-relative tree paths. No patterns ⇒ every file, like bare `jj file list`.
            let matcher = crate::fileset::union_matcher(
                paths.as_deref().unwrap_or_default(),
                workspace_root,
                crate::fileset::EmptyPatterns::MatchEverything,
            )?;
            let mut out: Vec<String> = tree
                .entries_matching(&*matcher)
                .map(|(path, _value)| path.as_internal_file_string().to_owned())
                .collect();
            out.sort();
            Ok(out)
        })
    }

    /// Diff stat (per-file + total line counts) of the single commit named by `revset_str`
    /// against its parent(s). `RevsetError` if the revset isn't exactly one commit.
    fn diff_stat<'py>(&self, py: Python<'py>, revset_str: &str) -> PyResult<Bound<'py, PyDict>> {
        let data = py.allow_threads(|| -> PyResult<DiffStatData> {
            let commit = self.resolve_single(revset_str)?;
            diff_stat::compute(self.repo.as_ref(), &commit)
        })?;
        diff_stat_to_dict(py, &data)
    }

    /// Diff stat **between two revisions** — `from_str`'s whole tree against `to_str`'s (concept
    /// §12), not a commit vs its parents. `RevsetError` unless each side names exactly one commit.
    fn diff_stat_between<'py>(
        &self,
        py: Python<'py>,
        from_str: &str,
        to_str: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let data = py.allow_threads(|| -> PyResult<DiffStatData> {
            let from_ = self.resolve_single(from_str)?;
            let to = self.resolve_single(to_str)?;
            diff_stat::compute_between(self.repo.as_ref(), &from_, &to)
        })?;
        diff_stat_to_dict(py, &data)
    }

    /// A lazy iterator over a revset's commits: evaluate to ids eagerly (cheap, off the GIL),
    /// then build one `CommitData` per `__next__`. For huge histories the caller can stream and
    /// discard rather than materialize the whole `log` list. `limit` truncates the id list.
    #[pyo3(signature = (revset_str, limit=None))]
    fn log_stream(
        &self,
        py: Python<'_>,
        revset_str: &str,
        limit: Option<usize>,
    ) -> PyResult<PyCommitStream> {
        let ids = py.allow_threads(|| -> PyResult<Vec<CommitId>> {
            let mut ids = revset::evaluate_ids(
                self.repo.as_ref(),
                revset_str,
                &self.workspace_name,
                &self.workspace_root,
                &self.revset_config,
            )?;
            if let Some(n) = limit {
                ids.truncate(n);
            }
            Ok(ids)
        })?;
        Ok(PyCommitStream {
            repo: self.repo.clone(),
            ids,
            pos: 0,
        })
    }

    /// Name-status diff (changed paths + how each changed) of the single commit named by
    /// `revset_str` against its parent(s). `RevsetError` if the revset isn't exactly one commit.
    fn diff<'py>(&self, py: Python<'py>, revset_str: &str) -> PyResult<Bound<'py, PyDict>> {
        let data = py.allow_threads(|| -> PyResult<DiffData> {
            let commit = self.resolve_single(revset_str)?;
            diff::compute(self.repo.as_ref(), &commit)
        })?;
        diff_to_dict(py, &data)
    }

    /// Name-status diff **between two revisions** — `from_str`'s whole tree against `to_str`'s
    /// (concept §12), not a commit vs its parents. `RevsetError` unless each side names exactly
    /// one commit.
    fn diff_between<'py>(
        &self,
        py: Python<'py>,
        from_str: &str,
        to_str: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let data = py.allow_threads(|| -> PyResult<DiffData> {
            let from_ = self.resolve_single(from_str)?;
            let to = self.resolve_single(to_str)?;
            diff::compute_between(self.repo.as_ref(), &from_, &to)
        })?;
        diff_to_dict(py, &data)
    }

    /// Whether `ancestor` is an ancestor of `descendant` in the commit DAG. A commit is its own
    /// ancestor (so `is_ancestor(x, x)` is `True`), matching `git merge-base --is-ancestor`. Each
    /// side must name exactly one commit, else `RevsetError` (project 13 §P4).
    fn is_ancestor(&self, py: Python<'_>, ancestor: &str, descendant: &str) -> PyResult<bool> {
        py.allow_threads(|| {
            let a = self.resolve_single(ancestor)?;
            let d = self.resolve_single(descendant)?;
            pollster::block_on(self.repo.index().is_ancestor(a.id(), d.id()))
                .map_err(|e| PyjutsuError::new_err(e.to_string()))
        })
    }

    /// A content identity for the change the single commit named by `revset_str` introduces against
    /// its parent(s): a stable hash of the *diff* (changed paths + added/removed line contents, with
    /// line numbers excluded). Two commits that make the same change — e.g. before and after a
    /// rebase/squash that re-hashes the commit id — share a `patch_id`, even though their commit ids
    /// differ (project 13 §P4). Not byte-compatible with `git patch-id`; it is pyjutsu's own stable
    /// diff digest. `RevsetError` unless `revset_str` names exactly one commit.
    fn patch_id(&self, py: Python<'_>, revset_str: &str) -> PyResult<String> {
        py.allow_threads(|| {
            let commit = self.resolve_single(revset_str)?;
            let data = diff::compute(self.repo.as_ref(), &commit)?;
            Ok(patch_id_hex(&data))
        })
    }

    /// 3-way merge the trees at `a` and `b` → `{tree_id, has_conflict}`. A pure read (no operation
    /// published). With `base=None` the merge base is auto-computed (jj's `merge_commit_trees`); with
    /// an explicit `base` a fixed 3-way merge is done at the tree layer. Each argument must name
    /// exactly one revision, else `RevsetError`. Mirrors `merge_commit_trees` in `diff`/`new`.
    #[pyo3(signature = (a, b, base=None))]
    fn try_merge<'py>(
        &self,
        py: Python<'py>,
        a: &str,
        b: &str,
        base: Option<&str>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let (tree_id, has_conflict) = py.allow_threads(|| -> PyResult<(String, bool)> {
            let repo = self.repo.as_ref();
            let a_commit = self.resolve_single(a)?;
            let b_commit = self.resolve_single(b)?;
            let merged: MergedTree = pollster::block_on(async {
                match base {
                    // Auto merge-base: `merge_commit_trees` computes the base internally (jj's own
                    // 3-way, matching `git merge-tree --write-tree`).
                    None => merge_commit_trees(repo, &[a_commit.clone(), b_commit.clone()])
                        .await
                        .map_err(map_backend_err),
                    // Fixed base: a tree-layer merge `[a, -base, b]` (adds a & b, removes base).
                    Some(base_str) => {
                        let base_commit = self.resolve_single(base_str)?;
                        let terms = Merge::from_vec(vec![
                            (a_commit.tree(), String::new()),
                            (base_commit.tree(), String::new()),
                            (b_commit.tree(), String::new()),
                        ]);
                        MergedTree::merge(terms).await.map_err(map_backend_err)
                    }
                }
            })?;
            Ok((merge_tree_id_hex(merged.tree_ids()), merged.has_conflict()))
        })?;
        let dict = PyDict::new(py);
        dict.set_item("tree_id", tree_id)?;
        dict.set_item("has_conflict", has_conflict)?;
        Ok(dict)
    }
}

/// Hash a name-status diff into a stable hex digest for `patch_id`. Files are sorted by path so the
/// digest is order-independent; each contributes its path, kind, and rename/copy source, then — for
/// text files — its added/removed line contents **without** line numbers, so the digest tracks
/// *what* changed, not where (a rebase that only shifts line positions keeps the same id). Binary or
/// typeless changes contribute path + kind only (their bytes aren't line-diffable).
///
/// The digest is **always SHA-1**, in every repository, including a SHA-256 one. A patch id is a
/// pyjutsu content digest, not a Git object id: nothing resolves it against the object database, so
/// a stable width is the useful contract. Do not "fix" this to follow the repository object hash —
/// that would make the same change produce different ids in different repos.
///
/// jj-lib gap: patch ids are a pyjutsu concept. jj-lib has no equivalent. Pyjutsu computes this
/// content digest directly with the `sha1` crate.
fn patch_id_hex(data: &DiffData) -> String {
    let mut files: Vec<&FileChangeData> = data.files.iter().collect();
    files.sort_by(|a, b| a.path.cmp(&b.path));
    let mut hasher = Sha1::new();
    for f in files {
        hasher.update(f.path.as_bytes());
        hasher.update(b"\0");
        hasher.update(f.kind.as_bytes());
        hasher.update(b"\0");
        if let Some(source) = &f.source {
            hasher.update(source.as_bytes());
        }
        hasher.update(b"\0");
        for hunk in &f.hunks {
            for line in &hunk.lines {
                hasher.update(line.kind.as_bytes());
                hasher.update(b" ");
                hasher.update(line.content.as_bytes());
                hasher.update(b"\n");
            }
        }
        hasher.update(b"\0\0");
    }
    format!("{:x}", hasher.finalize())
}

/// Build the `diff_stat` result dict (`files: [{path, insertions, deletions}], total_*`) from
/// computed data. Shared by `diff_stat` and `diff_stat_between`.
fn diff_stat_to_dict<'py>(py: Python<'py>, data: &DiffStatData) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    let files: Vec<Bound<'py, PyDict>> = data
        .files
        .iter()
        .map(|f| {
            let file = PyDict::new(py);
            file.set_item("path", &f.path)?;
            file.set_item("insertions", f.insertions)?;
            file.set_item("deletions", f.deletions)?;
            Ok(file)
        })
        .collect::<PyResult<_>>()?;
    dict.set_item("files", files)?;
    dict.set_item("total_insertions", data.total_insertions)?;
    dict.set_item("total_deletions", data.total_deletions)?;
    Ok(dict)
}

/// Build the `diff` result dict (`files: [{path, kind, binary, source, hunks: [...]}]`) from
/// computed data. Shared by `diff` and `diff_between`.
fn diff_to_dict<'py>(py: Python<'py>, data: &DiffData) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    let files: Vec<Bound<'py, PyDict>> = data
        .files
        .iter()
        .map(|f| {
            let file = PyDict::new(py);
            file.set_item("path", &f.path)?;
            file.set_item("kind", f.kind)?;
            file.set_item("binary", f.binary)?;
            file.set_item("source", f.source.as_deref())?;
            let hunks: Vec<Bound<'py, PyDict>> = f
                .hunks
                .iter()
                .map(|h| {
                    let hunk = PyDict::new(py);
                    hunk.set_item("old_start", h.old_start)?;
                    hunk.set_item("old_lines", h.old_lines)?;
                    hunk.set_item("new_start", h.new_start)?;
                    hunk.set_item("new_lines", h.new_lines)?;
                    let lines: Vec<Bound<'py, PyDict>> = h
                        .lines
                        .iter()
                        .map(|l| {
                            let line = PyDict::new(py);
                            line.set_item("kind", l.kind)?;
                            line.set_item("content", &l.content)?;
                            Ok(line)
                        })
                        .collect::<PyResult<_>>()?;
                    hunk.set_item("lines", lines)?;
                    Ok(hunk)
                })
                .collect::<PyResult<_>>()?;
            file.set_item("hunks", hunks)?;
            Ok(file)
        })
        .collect::<PyResult<_>>()?;
    dict.set_item("files", files)?;
    Ok(dict)
}

/// A one-shot iterator yielding a revset's commits as plain dicts, one per `__next__`. Holds the
/// `Arc<ReadonlyRepo>` (`Send + Sync`) plus the pre-evaluated id list and a cursor — it owns ids,
/// not the revset/iter (which borrow the repo), so there are no self-referential lifetimes. The
/// expensive `CommitData::build` (commit object, signatures, bookmarks) is deferred to each step.
#[pyclass(module = "pyjutsu._pyjutsu")]
pub(crate) struct PyCommitStream {
    repo: Arc<ReadonlyRepo>,
    ids: Vec<CommitId>,
    pos: usize,
}

#[pymethods]
impl PyCommitStream {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    /// Build and return the next commit dict, or `None` (→ `StopIteration`) when exhausted.
    fn __next__<'py>(&mut self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
        if self.pos >= self.ids.len() {
            return Ok(None);
        }
        let id = self.ids[self.pos].clone();
        self.pos += 1;
        let data = py.allow_threads(|| {
            let repo = self.repo.as_ref();
            let commit = repo.store().get_commit(&id).map_err(map_backend_err)?;
            CommitData::build(repo, &commit)
        })?;
        Ok(Some(data.to_dict(py)?))
    }
}
