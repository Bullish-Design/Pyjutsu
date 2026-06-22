//! Intermediate conversion: `jj-lib` values → plain Python data (dicts/lists of primitives).
//!
//! No `jj-lib` type crosses the FFI (concept §4). Each read computes these plain Rust structs
//! **off the GIL** (they touch the backend: trees for `is_empty`, the view for bookmarks), then
//! converts them to dicts after re-acquiring the GIL. The Python layer validates the dicts into
//! Pydantic models — the drift tripwire (`extra="forbid"`).

use std::path::Path;

use pyo3::PyErr;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use jj_lib::backend::{Signature, Timestamp};
use jj_lib::commit::Commit;
use jj_lib::object_id::ObjectId;
use jj_lib::op_store::{RefTarget, RemoteRef};
use jj_lib::operation::Operation;
use jj_lib::repo::Repo;

use crate::errors::map_backend_err;

/// Plain author/committer signature. Timestamps are carried as raw ms + minutes so the Python
/// layer builds the tz-aware `datetime` (keeping policy out of Rust).
pub(crate) struct SignatureData {
    name: String,
    email: String,
    timestamp_ms: i64,
    tz_offset_minutes: i32,
}

impl SignatureData {
    fn from_jj(sig: &Signature) -> Self {
        Self {
            name: sig.name.clone(),
            email: sig.email.clone(),
            timestamp_ms: sig.timestamp.timestamp.0,
            tz_offset_minutes: sig.timestamp.tz_offset,
        }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("name", &self.name)?;
        dict.set_item("email", &self.email)?;
        dict.set_item("timestamp_ms", self.timestamp_ms)?;
        dict.set_item("tz_offset_minutes", self.tz_offset_minutes)?;
        Ok(dict)
    }
}

/// Set `{<prefix>_ms, <prefix>_tz_offset_minutes}` on `dict` from a jj `Timestamp`. The Python
/// model assembles the tz-aware datetime, keeping time policy out of Rust.
fn set_timestamp(dict: &Bound<'_, PyDict>, prefix: &str, ts: &Timestamp) -> PyResult<()> {
    dict.set_item(format!("{prefix}_ms"), ts.timestamp.0)?;
    dict.set_item(format!("{prefix}_tz_offset_minutes"), ts.tz_offset)?;
    Ok(())
}

/// Plain, fully-resolved commit row. Everything a `Commit` Pydantic model needs, computed once.
pub(crate) struct CommitData {
    change_id: String,
    commit_id: String,
    description: String,
    author: SignatureData,
    committer: SignatureData,
    parent_ids: Vec<String>,
    is_empty: bool,
    has_conflict: bool,
    bookmarks: Vec<String>,
}

impl CommitData {
    /// Build a row from a commit. Touches the backend (`is_empty`) and the view (local
    /// bookmarks pointing at the commit), so call this **off the GIL**.
    pub(crate) fn build(repo: &dyn Repo, commit: &Commit) -> Result<Self, PyErr> {
        // change_id uses jj's canonical "reverse hex" (z-k digits) — the letter form `jj` shows
        // and users type; commit_id is plain hex (git-style), as `jj` displays it.
        // Sort the names explicitly so the `Commit.bookmarks` "sorted" contract holds at the FFI
        // boundary, independent of jj-lib's view iteration order.
        let mut bookmarks: Vec<String> = repo
            .view()
            .local_bookmarks_for_commit(commit.id())
            .map(|(name, _target)| name.as_str().to_owned())
            .collect();
        bookmarks.sort();
        Ok(Self {
            change_id: commit.change_id().reverse_hex(),
            commit_id: commit.id().hex(),
            description: commit.description().to_owned(),
            author: SignatureData::from_jj(commit.author()),
            committer: SignatureData::from_jj(commit.committer()),
            parent_ids: commit.parent_ids().iter().map(ObjectId::hex).collect(),
            is_empty: pollster::block_on(commit.is_empty(repo)).map_err(map_backend_err)?,
            has_conflict: commit.has_conflict(),
            bookmarks,
        })
    }

    pub(crate) fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("change_id", &self.change_id)?;
        dict.set_item("commit_id", &self.commit_id)?;
        dict.set_item("description", &self.description)?;
        dict.set_item("author", self.author.to_dict(py)?)?;
        dict.set_item("committer", self.committer.to_dict(py)?)?;
        dict.set_item("parent_ids", self.parent_ids.clone())?;
        dict.set_item("is_empty", self.is_empty)?;
        dict.set_item("has_conflict", self.has_conflict)?;
        dict.set_item("bookmarks", self.bookmarks.clone())?;
        Ok(dict)
    }
}

/// Plain op-log row. Times are carried as raw ms + minutes (like signatures) for the Python
/// model to assemble. `is_snapshot` flags pure working-copy-snapshot operations.
pub(crate) struct OperationData {
    id: String,
    parent_ids: Vec<String>,
    description: String,
    hostname: String,
    username: String,
    is_snapshot: bool,
    tags: Vec<(String, String)>,
    start: Timestamp,
    end: Timestamp,
}

impl OperationData {
    /// Build a row from an operation. Reads only already-loaded metadata (no backend I/O), but
    /// callers still build these inside the off-GIL op-log walk.
    pub(crate) fn build(op: &Operation) -> Self {
        let meta = op.metadata();
        Self {
            id: op.id().hex(),
            parent_ids: op.parent_ids().iter().map(ObjectId::hex).collect(),
            description: meta.description.clone(),
            hostname: meta.hostname.clone(),
            username: meta.username.clone(),
            is_snapshot: meta.is_snapshot,
            // jj-lib 0.42 renamed `OperationMetadata::tags` to `attributes` (same string→string
            // map); we still surface it to Python under the `tags` key.
            tags: meta
                .attributes
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
            start: meta.time.start,
            end: meta.time.end,
        }
    }

    pub(crate) fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("id", &self.id)?;
        dict.set_item("parent_ids", self.parent_ids.clone())?;
        dict.set_item("description", &self.description)?;
        dict.set_item("hostname", &self.hostname)?;
        dict.set_item("username", &self.username)?;
        dict.set_item("is_snapshot", self.is_snapshot)?;
        let tags = PyDict::new(py);
        for (k, v) in &self.tags {
            tags.set_item(k, v)?;
        }
        dict.set_item("tags", tags)?;
        set_timestamp(&dict, "start", &self.start)?;
        set_timestamp(&dict, "end", &self.end)?;
        Ok(dict)
    }
}

/// Plain bookmark row: one per local bookmark (`remote = None`) and one per remote ref. A
/// bookmark whose target has more than one added id is conflicted (jj keeps both sides); the
/// `target_ids` carry every side, so callers see conflicts faithfully (concept §8.9).
pub(crate) struct BookmarkData {
    name: String,
    remote: Option<String>,
    target_ids: Vec<String>,
    tracked: bool,
}

impl BookmarkData {
    /// A local bookmark row. `tracked` is `false`: tracking is a remote-ref property, so jj
    /// reports local bookmarks as untracked (matches `jj bookmark list`'s `tracked` keyword).
    pub(crate) fn local(name: &str, target: &RefTarget) -> Self {
        Self {
            name: name.to_owned(),
            remote: None,
            target_ids: target.added_ids().map(ObjectId::hex).collect(),
            tracked: false,
        }
    }

    /// A remote-tracking bookmark row. `tracked` reflects whether jj merges it into the local.
    pub(crate) fn remote(name: &str, remote: &str, remote_ref: &RemoteRef) -> Self {
        Self {
            name: name.to_owned(),
            remote: Some(remote.to_owned()),
            target_ids: remote_ref.target.added_ids().map(ObjectId::hex).collect(),
            tracked: remote_ref.is_tracked(),
        }
    }

    pub(crate) fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("name", &self.name)?;
        dict.set_item("remote", self.remote.as_deref())?;
        dict.set_item("target_ids", self.target_ids.clone())?;
        dict.set_item("tracked", self.tracked)?;
        Ok(dict)
    }
}

/// Plain workspace row: one per workspace tracked in the repo view (concept §124). `path` is the
/// on-disk working-copy root recorded in the workspace store; it is `None` if the store has no
/// entry for the name (e.g. a workspace whose `.jj` was removed out-of-band). `wc_commit_id` is the
/// workspace's `@` (working-copy commit).
pub(crate) struct WorkspaceInfoData {
    name: String,
    path: Option<String>,
    wc_commit_id: String,
}

impl WorkspaceInfoData {
    pub(crate) fn new(name: &str, path: Option<&Path>, wc_commit_id: &str) -> Self {
        Self {
            name: name.to_owned(),
            // Lossy is fine here: the Python layer treats this as a display/`os.PathLike` string.
            path: path.map(|p| p.to_string_lossy().into_owned()),
            wc_commit_id: wc_commit_id.to_owned(),
        }
    }

    pub(crate) fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("name", &self.name)?;
        dict.set_item("path", self.path.as_deref())?;
        dict.set_item("wc_commit_id", &self.wc_commit_id)?;
        Ok(dict)
    }
}

/// Plain git-remote row: one configured remote's name + its **fetch** URL (concept §134). `url` is
/// stringified Rust-side from `gix::Url` so no `gix` type crosses the FFI; it is `None` if the remote
/// has no fetch URL configured.
pub(crate) struct RemoteData {
    name: String,
    url: Option<String>,
}

impl RemoteData {
    pub(crate) fn new(name: &str, url: Option<&str>) -> Self {
        Self {
            name: name.to_owned(),
            url: url.map(str::to_owned),
        }
    }

    pub(crate) fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("name", &self.name)?;
        dict.set_item("url", self.url.as_deref())?;
        Ok(dict)
    }
}

/// Plain conflict row: one per conflicted path in a commit's tree. jj conflicts are N-sided
/// (concept §8.9) — `num_sides` is the number of positive terms, `num_bases` the negative
/// (removed) terms. A regular 3-way merge conflict is `num_sides=2, num_bases=1`.
pub(crate) struct ConflictData {
    path: String,
    num_sides: usize,
    num_bases: usize,
}

impl ConflictData {
    pub(crate) fn new(path: String, num_sides: usize, num_bases: usize) -> Self {
        Self {
            path,
            num_sides,
            num_bases,
        }
    }

    pub(crate) fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("path", &self.path)?;
        dict.set_item("num_sides", self.num_sides)?;
        dict.set_item("num_bases", self.num_bases)?;
        Ok(dict)
    }
}
