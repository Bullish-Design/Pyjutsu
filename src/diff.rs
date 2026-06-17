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
use jj_lib::diff::{ContentDiff, DiffHunkKind};
use jj_lib::matchers::EverythingMatcher;
use jj_lib::merge::MergedTreeValue;
use jj_lib::repo::Repo;
use jj_lib::rewrite::merge_commit_trees;

use crate::diff_stat::read_text;
use crate::errors::map_backend_err;

/// One line within a [`HunkData`]. `kind` is "removed" (present on the before side only) or
/// "added" (after side only). Content is lossy-utf8 decoded and keeps its trailing newline.
pub(crate) struct HunkLineData {
    pub kind: &'static str,
    pub content: String,
}

/// One unified-diff hunk: a contiguous changed span with its 1-based old/new line ranges. The
/// binding groups one hunk per changed span with no surrounding context (see `content_hunks`).
pub(crate) struct HunkData {
    pub old_start: usize,
    pub old_lines: usize,
    pub new_start: usize,
    pub new_lines: usize,
    pub lines: Vec<HunkLineData>,
}

/// One changed path, how it changed, and (for text files) its content hunks.
pub(crate) struct FileChangeData {
    pub path: String,
    /// "added" | "modified" | "removed" | "type_changed".
    pub kind: &'static str,
    /// True for a non-line-diffable file (binary, symlink, submodule, or conflict): no hunks.
    pub binary: bool,
    pub hunks: Vec<HunkData>,
}

/// A commit's whole name-status: one row per changed path.
pub(crate) struct DiffData {
    pub files: Vec<FileChangeData>,
}

/// Compute the name-status of `commit` against its merged-parent tree (empty tree for a root
/// commit). Synchronous wrapper over jj-lib's async tree diff; call off the GIL.
pub(crate) fn compute(repo: &dyn Repo, commit: &Commit) -> Result<DiffData, PyErr> {
    pollster::block_on(async {
        let store = repo.store();
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
            // Reuse diff_stat's text/binary discipline so the two reads never disagree about a
            // file's diffability: `Some(bytes)` ⇒ resolved text, `None` ⇒ non-line-diffable.
            let before = read_text(store, &entry.path, &diff.before).await?;
            let after = read_text(store, &entry.path, &diff.after).await?;
            let (binary, hunks) = match (before, after) {
                (Some(b), Some(a)) => (false, content_hunks(&b, &a)),
                _ => (true, Vec::new()),
            };
            files.push(FileChangeData {
                path: entry.path.as_internal_file_string().to_owned(),
                kind: classify_change(&diff.before, &diff.after),
                binary,
                hunks,
            });
        }
        Ok(DiffData { files })
    })
}

/// Build content hunks from a line-level diff of two text blobs. One hunk per changed span with
/// **no surrounding context** (`old_start`/`new_start` track the 1-based line cursors; context
/// lines are never emitted). This sidesteps git-style context-windowing while staying a faithful
/// structured diff: per-file added/removed line multisets match `jj diff --git`. Header-exact
/// `@@` grouping (3-line context) is intentionally not implemented (flagged, not faked).
fn content_hunks(before: &[u8], after: &[u8]) -> Vec<HunkData> {
    let inputs = [before, after];
    let diff = ContentDiff::by_line(inputs);
    let mut hunks = Vec::new();
    let mut old_start = 1usize;
    let mut new_start = 1usize;
    for hunk in diff.hunks() {
        match hunk.kind {
            // A matched span advances both cursors by its line count; emit nothing.
            DiffHunkKind::Matching => {
                let n = split_lines(hunk.contents[0]).count();
                old_start += n;
                new_start += n;
            }
            // A changed span: contents[0] is the before (removed) side, contents[1] the after
            // (added) side. Emit removed lines then added lines, and advance each cursor.
            DiffHunkKind::Different => {
                let removed: Vec<&[u8]> = split_lines(hunk.contents[0]).collect();
                let added: Vec<&[u8]> = split_lines(hunk.contents[1]).collect();
                let mut lines = Vec::with_capacity(removed.len() + added.len());
                for line in &removed {
                    lines.push(HunkLineData {
                        kind: "removed",
                        content: String::from_utf8_lossy(line).into_owned(),
                    });
                }
                for line in &added {
                    lines.push(HunkLineData {
                        kind: "added",
                        content: String::from_utf8_lossy(line).into_owned(),
                    });
                }
                hunks.push(HunkData {
                    old_start,
                    old_lines: removed.len(),
                    new_start,
                    new_lines: added.len(),
                    lines,
                });
                old_start += removed.len();
                new_start += added.len();
            }
        }
    }
    hunks
}

/// Split a blob into lines, each keeping its trailing newline (a final line without a newline
/// still counts). Empty input yields no lines.
fn split_lines(bytes: &[u8]) -> impl Iterator<Item = &[u8]> {
    bytes.split_inclusive(|&b| b == b'\n')
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
