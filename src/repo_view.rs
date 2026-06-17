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

use pyo3::PyErr;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use jj_lib::backend::CommitId;
use jj_lib::object_id::ObjectId;
use jj_lib::op_walk;
use jj_lib::ref_name::WorkspaceNameBuf;
use jj_lib::repo::{ReadonlyRepo, Repo};

use crate::convert::{BookmarkData, CommitData, ConflictData, OperationData};
use crate::diff::{self, DiffData};
use crate::diff_stat::{self, DiffStatData};
use crate::errors::{PyjutsuError, RevsetError, map_backend_err};
use crate::revset;

/// Opaque handle to a `ReadonlyRepo` at a fixed operation, plus the workspace context reads
/// need. Cheap to clone-share (the repo is `Arc`).
#[pyclass(module = "pyjutsu._pyjutsu")]
pub(crate) struct PyRepoView {
    repo: Arc<ReadonlyRepo>,
    workspace_name: WorkspaceNameBuf,
    workspace_root: PathBuf,
    user_email: String,
}

impl PyRepoView {
    /// Construct a view. Called by `PyWorkspace::head_view`/`at_operation`.
    pub(crate) fn new(
        repo: Arc<ReadonlyRepo>,
        workspace_name: WorkspaceNameBuf,
        workspace_root: PathBuf,
        user_email: String,
    ) -> Self {
        Self {
            repo,
            workspace_name,
            workspace_root,
            user_email,
        }
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
                &self.user_email,
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
            let commit = repo.store().get_commit(&commit_id).map_err(map_backend_err)?;
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
        let dicts: Vec<Bound<'py, PyDict>> =
            data.iter().map(|d| d.to_dict(py)).collect::<PyResult<_>>()?;
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
            let mut out = Vec::new();
            for op in op_walk::walk_ancestors(std::slice::from_ref(&head)) {
                if limit.is_some_and(|n| out.len() >= n) {
                    break;
                }
                out.push(OperationData::build(&op.map_err(map_backend_err)?));
            }
            Ok(out)
        })?;
        let dicts: Vec<Bound<'py, PyDict>> =
            data.iter().map(|d| d.to_dict(py)).collect::<PyResult<_>>()?;
        PyList::new(py, dicts)
    }

    /// The id of the operation this view is at (its head operation).
    fn operation_id(&self) -> String {
        self.repo.operation().id().hex()
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
        let dicts: Vec<Bound<'py, PyDict>> =
            data.iter().map(|d| d.to_dict(py)).collect::<PyResult<_>>()?;
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
                &self.user_email,
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
        let dicts: Vec<Bound<'py, PyDict>> =
            data.iter().map(|d| d.to_dict(py)).collect::<PyResult<_>>()?;
        PyList::new(py, dicts)
    }

    /// Diff stat (per-file + total line counts) of the single commit named by `revset_str`
    /// against its parent(s). `RevsetError` if the revset isn't exactly one commit.
    fn diff_stat<'py>(&self, py: Python<'py>, revset_str: &str) -> PyResult<Bound<'py, PyDict>> {
        let data = py.allow_threads(|| -> PyResult<DiffStatData> {
            let repo = self.repo.as_ref();
            let commits = revset::evaluate(
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
            diff_stat::compute(repo, &commits[0])
        })?;

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
                &self.user_email,
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
            let repo = self.repo.as_ref();
            let commits = revset::evaluate(
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
            diff::compute(repo, &commits[0])
        })?;

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
