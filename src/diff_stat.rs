//! `diff_stat` — per-file insertion/deletion line counts for a commit vs its parent(s).
//!
//! jj-lib has no `DiffStat` type (the CLI computes it), so we do too (concept §5, guide §6):
//! diff the commit's tree against its merged-parent tree, read each changed file's before/after
//! bytes, and count lines in the differing hunks of a line-level diff. Text files only — symlinks,
//! submodules, conflicts, and binary (NUL-containing) files are listed with zero line counts,
//! matching how `jj diff --stat` leaves them out of the +/- totals.

use futures::AsyncReadExt as _;
use futures::StreamExt as _;
use pyo3::PyErr;

use jj_lib::backend::TreeValue;
use jj_lib::commit::Commit;
use jj_lib::diff::{ContentDiff, DiffHunkKind};
use jj_lib::matchers::EverythingMatcher;
use jj_lib::merge::MergedTreeValue;
use jj_lib::repo::Repo;
use jj_lib::repo_path::RepoPath;
use jj_lib::rewrite::merge_commit_trees;
use jj_lib::store::Store;

use crate::errors::map_backend_err;

/// Per-file line counts.
pub(crate) struct FileStatData {
    pub path: String,
    pub insertions: usize,
    pub deletions: usize,
}

/// A commit's whole diff stat: per-file rows plus summed totals.
pub(crate) struct DiffStatData {
    pub files: Vec<FileStatData>,
    pub total_insertions: usize,
    pub total_deletions: usize,
}

/// Compute the diff stat of `commit` against its merged-parent tree (empty tree for a root
/// commit). Synchronous wrapper over jj-lib's async tree diff; call off the GIL.
pub(crate) fn compute(repo: &dyn Repo, commit: &Commit) -> Result<DiffStatData, PyErr> {
    pollster::block_on(async {
        let store = repo.store();
        // jj-lib 0.42 made `Commit::parents` an async fn returning all parents at once.
        let parents: Vec<Commit> = commit.parents().await.map_err(map_backend_err)?;
        // merge_commit_trees over zero parents yields the empty tree (root commit's "before").
        let from_tree = merge_commit_trees(repo, &parents)
            .await
            .map_err(map_backend_err)?;
        let to_tree = commit.tree();

        let mut stream = from_tree.diff_stream(&to_tree, &EverythingMatcher);
        let mut files = Vec::new();
        let mut total_insertions = 0;
        let mut total_deletions = 0;
        while let Some(entry) = stream.next().await {
            let diff = entry.values.map_err(map_backend_err)?;
            let before = read_text(store, &entry.path, &diff.before).await?;
            let after = read_text(store, &entry.path, &diff.after).await?;
            let (insertions, deletions) = match (before, after) {
                (Some(b), Some(a)) => count_line_changes(&b, &a),
                // Non-text change (symlink/submodule/conflict/binary): list it, count nothing.
                _ => (0, 0),
            };
            total_insertions += insertions;
            total_deletions += deletions;
            files.push(FileStatData {
                path: entry.path.as_internal_file_string().to_owned(),
                insertions,
                deletions,
            });
        }
        Ok(DiffStatData {
            files,
            total_insertions,
            total_deletions,
        })
    })
}

/// Read a tree value's bytes if it is a resolved text file. `Some(bytes)` for a present file
/// (or empty for an absent side); `None` for anything not line-diffable (symlink, submodule,
/// tree, conflict, or binary content with a NUL byte).
pub(crate) async fn read_text(
    store: &Store,
    path: &RepoPath,
    value: &MergedTreeValue,
) -> Result<Option<Vec<u8>>, PyErr> {
    match value.as_resolved() {
        Some(None) => Ok(Some(Vec::new())), // absent side of an add/delete
        Some(Some(TreeValue::File { id, .. })) => {
            let mut reader = store.read_file(path, id).await.map_err(map_backend_err)?;
            let mut buf = Vec::new();
            reader.read_to_end(&mut buf).await.map_err(map_backend_err)?;
            Ok(if buf.contains(&0) { None } else { Some(buf) })
        }
        _ => Ok(None),
    }
}

/// Count inserted/removed lines between two text blobs using a line-level diff: for each
/// differing hunk, the lines on the "after" side are insertions and on the "before" side
/// deletions (matching `jj diff --stat`).
fn count_line_changes(before: &[u8], after: &[u8]) -> (usize, usize) {
    let inputs = [before, after];
    let diff = ContentDiff::by_line(inputs);
    let mut insertions = 0;
    let mut deletions = 0;
    for hunk in diff.hunks() {
        if hunk.kind == DiffHunkKind::Different {
            deletions += count_lines(hunk.contents[0]);
            insertions += count_lines(hunk.contents[1]);
        }
    }
    (insertions, deletions)
}

/// Number of lines in a blob (a trailing line without a newline still counts).
fn count_lines(bytes: &[u8]) -> usize {
    if bytes.is_empty() {
        0
    } else {
        bytes.split_inclusive(|&b| b == b'\n').count()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn count_lines_handles_trailing_newline() {
        assert_eq!(count_lines(b""), 0);
        assert_eq!(count_lines(b"one\n"), 1);
        assert_eq!(count_lines(b"one\ntwo\n"), 2);
        assert_eq!(count_lines(b"no trailing newline"), 1);
        assert_eq!(count_lines(b"a\nb"), 2);
    }

    #[test]
    fn count_line_changes_added_file() {
        assert_eq!(count_line_changes(b"", b"a\nb\nc\n"), (3, 0));
    }

    #[test]
    fn count_line_changes_deleted_file() {
        assert_eq!(count_line_changes(b"a\nb\n", b""), (0, 2));
    }

    #[test]
    fn count_line_changes_modify_and_append() {
        // l2 -> CHANGED (one replaced) plus an appended l4: +2 insertions, -1 deletion.
        let before = b"l1\nl2\nl3\n";
        let after = b"l1\nCHANGED\nl3\nl4\n";
        assert_eq!(count_line_changes(before, after), (2, 1));
    }

    #[test]
    fn count_line_changes_identical_is_zero() {
        assert_eq!(count_line_changes(b"same\n", b"same\n"), (0, 0));
    }
}
