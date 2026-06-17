//! `diff` — name-status (and, slice 2, content hunks) for a commit vs its parent(s).
//!
//! Read-only sibling to `diff_stat` (concept §5 `diff`, §12 "full diffs/hunks"): same framing
//! — resolve `revset` to one commit, diff its tree against the merged parent tree (empty tree
//! for a root commit) — but instead of line *counts* it reports *which* paths changed and *how*
//! (added/modified/removed/type_changed). Slice 2 enriches each changed text file with content
//! hunks; both reads share `diff_stat::read_text`'s text/binary discipline so they never
//! disagree about a file's diffability. No commit id moves — this is a pure read off the GIL.

use futures::StreamExt as _;
use pyo3::PyErr;

use jj_lib::backend::TreeValue;
use jj_lib::commit::Commit;
use jj_lib::matchers::EverythingMatcher;
use jj_lib::merge::MergedTreeValue;
use jj_lib::repo::Repo;
use jj_lib::rewrite::merge_commit_trees;

use crate::errors::map_backend_err;

/// One changed path and how it changed.
pub(crate) struct FileChangeData {
    pub path: String,
    /// "added" | "modified" | "removed" | "type_changed".
    pub kind: &'static str,
}

/// A commit's whole name-status: one row per changed path.
pub(crate) struct DiffData {
    pub files: Vec<FileChangeData>,
}

/// Compute the name-status of `commit` against its merged-parent tree (empty tree for a root
/// commit). Synchronous wrapper over jj-lib's async tree diff; call off the GIL.
pub(crate) fn compute(repo: &dyn Repo, commit: &Commit) -> Result<DiffData, PyErr> {
    pollster::block_on(async {
        let parents: Vec<Commit> = commit
            .parents()
            .collect::<Result<_, _>>()
            .map_err(map_backend_err)?;
        // merge_commit_trees over zero parents yields the empty tree (root commit's "before").
        let from_tree = merge_commit_trees(repo, &parents)
            .await
            .map_err(map_backend_err)?;
        let to_tree = commit.tree();

        let mut stream = from_tree.diff_stream(&to_tree, &EverythingMatcher);
        let mut files = Vec::new();
        while let Some(entry) = stream.next().await {
            let diff = entry.values.map_err(map_backend_err)?;
            files.push(FileChangeData {
                path: entry.path.as_internal_file_string().to_owned(),
                kind: classify_change(&diff.before, &diff.after),
            });
        }
        Ok(DiffData { files })
    })
}

/// Derive the change kind from a path's before/after tree values. The diff stream only yields
/// real changes, so the unchanged case never reaches here. A side that is a tree conflict
/// (`as_resolved() == None`) is reported as `modified` — a conflicted path *is* a change vs the
/// parent; finer conflict detail is the separate `conflicts()` read.
fn classify_change(before: &MergedTreeValue, after: &MergedTreeValue) -> &'static str {
    match (before.as_resolved(), after.as_resolved()) {
        (Some(None), Some(Some(_))) => "added",
        (Some(Some(_)), Some(None)) => "removed",
        (Some(Some(a)), Some(Some(b))) => {
            if same_variant(a, b) {
                "modified"
            } else {
                "type_changed"
            }
        }
        _ => "modified",
    }
}

/// Whether two tree values are the same kind of entry (file↔file, symlink↔symlink, …). A
/// differing variant (e.g. file↔symlink, file↔submodule) is a type change. An
/// executable-bit-only change stays file↔file ⇒ `modified` (jj renders it `M`).
fn same_variant(a: &TreeValue, b: &TreeValue) -> bool {
    matches!(
        (a, b),
        (TreeValue::File { .. }, TreeValue::File { .. })
            | (TreeValue::Symlink(_), TreeValue::Symlink(_))
            | (TreeValue::Tree(_), TreeValue::Tree(_))
            | (TreeValue::GitSubmodule(_), TreeValue::GitSubmodule(_))
    )
}
