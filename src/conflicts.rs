//! Conflict content reads: materialize a conflicted path into marked text, parse
//! it back into sides, and resolve it inside a transaction.
//!
//! jj-lib owns the whole conflict path (`conflicts.rs`): `materialize_tree_value`,
//! `materialize_merge_result_to_bytes`, `parse_conflict`, and `update_from_content`
//! (all checked in 0.44.0). This module is a thin binding: read the tree value,
//! hand it to jj-lib, and convert plain text across the FFI.

use pyo3::prelude::*;

use jj_lib::conflict_labels::ConflictLabels;
use jj_lib::conflicts::{
    self, ConflictMarkerStyle, ConflictMaterializeOptions, MaterializedTreeValue,
};
use jj_lib::merged_tree::MergedTree;
use jj_lib::repo::Repo;
use jj_lib::repo_path::{RepoPath, RepoPathBuf};
use jj_lib::store::Store;

use crate::errors::{ConflictError, PyjutsuError, map_backend_err};
use crate::repo_view::PyRepoView;

/// Map the Python marker-style name to jj-lib's enum. Python validates the
/// vocabulary first (`repo_view.py`); this is the native backstop.
pub(crate) fn style_from_str(style: &str) -> PyResult<ConflictMarkerStyle> {
    match style {
        "diff" => Ok(ConflictMarkerStyle::Diff),
        "snapshot" => Ok(ConflictMarkerStyle::Snapshot),
        "git" => Ok(ConflictMarkerStyle::Git),
        other => Err(PyjutsuError::new_err(format!(
            "conflict marker style must be 'diff', 'snapshot', or 'git', got '{other}'"
        ))),
    }
}

/// Materialize the tree value at `path` into text, for `conflict_content`.
///
/// A conflicted file yields jj's marked text in the requested style (matching
/// `jj file show`). A plain file yields its raw content. Symlinks yield their
/// target. Anything else — a directory, a submodule, a non-file conflict, an
/// absent path — is a clear `ConflictError` (or the merge description for an
/// `OtherConflict`, mirroring jj-lib's own working-copy fallback).
pub(crate) async fn materialize_to_string(
    store: &Store,
    path: &RepoPath,
    value: jj_lib::merge::MergedTreeValue,
    labels: &ConflictLabels,
    style: ConflictMarkerStyle,
) -> PyResult<String> {
    let materialized = conflicts::materialize_tree_value(store, path, value, labels)
        .await
        .map_err(map_backend_err)?;
    match materialized {
        MaterializedTreeValue::File(mut file) => {
            let bytes = file.read_all(path).await.map_err(map_backend_err)?;
            Ok(String::from_utf8_lossy(&bytes).into_owned())
        }
        MaterializedTreeValue::FileConflict(file) => {
            let options = ConflictMaterializeOptions {
                marker_style: style,
                marker_len: None,
                merge: store.merge_options().clone(),
            };
            let bytes = conflicts::materialize_merge_result_to_bytes(
                &file.contents,
                &file.labels,
                &options,
            );
            Ok(String::from_utf8_lossy(&bytes).into_owned())
        }
        MaterializedTreeValue::Symlink { target, .. } => Ok(target),
        MaterializedTreeValue::OtherConflict { id, labels } => Ok(id.describe(&labels)),
        _ => Err(ConflictError::new_err(format!(
            "path '{}': not a readable file at this revision",
            path.as_internal_file_string()
        ))),
    }
}

/// Parse a conflicted file's marked text back into its sides (no markers).
///
/// Round-trips through jj-lib exactly as `update_from_content` does: materialize
/// with an explicit marker length, then parse with the same length. Returns one
/// string per merge term in jj's conflict term order — each add with its
/// preceding base, starting with the first add — so a regular 3-way conflict
/// yields `[side_a, base, side_b]` (matching `Conflict.num_sides`/`num_bases`).
/// A path that is not a conflicted file is a `ConflictError`.
pub(crate) fn sides(
    view: &PyRepoView,
    py: Python<'_>,
    path: &str,
    revset_str: &str,
) -> PyResult<Vec<String>> {
    let path_buf = RepoPathBuf::from_relative_path(path)
        .map_err(|e| PyjutsuError::new_err(format!("invalid path '{path}': {e}")))?;
    py.allow_threads(|| {
        let commit = view.resolve_single(revset_str)?;
        let repo = view.repo.as_ref();
        let store = repo.store();
        let tree = commit.tree();
        let value = pollster::block_on(tree.path_value(&path_buf)).map_err(map_backend_err)?;
        let materialized = pollster::block_on(conflicts::materialize_tree_value(
            store,
            &path_buf,
            value,
            tree.labels(),
        ))
        .map_err(map_backend_err)?;
        let MaterializedTreeValue::FileConflict(file) = materialized else {
            return Err(ConflictError::new_err(format!(
                "path '{path}' is not a conflicted file; nothing to parse"
            )));
        };
        let marker_len = conflicts::choose_materialized_conflict_marker_len(&file.contents);
        let options = ConflictMaterializeOptions {
            marker_style: ConflictMarkerStyle::Diff,
            marker_len: Some(marker_len),
            merge: store.merge_options().clone(),
        };
        let bytes =
            conflicts::materialize_merge_result_to_bytes(&file.contents, &file.labels, &options);
        // One buffer per merge term (adds then removes) — the same rebuild
        // `update_from_content` performs, so the sides reassemble the file.
        let num_terms = file.contents.iter().count();
        let mut sides: Vec<Vec<u8>> = vec![Vec::new(); num_terms];
        match conflicts::parse_conflict(&bytes, file.contents.num_sides(), marker_len) {
            Some(hunks) => {
                for hunk in hunks {
                    if let Some(resolved) = hunk.as_resolved() {
                        for side in &mut sides {
                            side.extend_from_slice(resolved);
                        }
                    } else {
                        for (side, term) in sides.iter_mut().zip(hunk.iter()) {
                            side.extend_from_slice(term);
                        }
                    }
                }
            }
            // No markers survived materialization (the merge resolved on its
            // own) — fall back to the raw terms.
            None => {
                for (side, term) in sides.iter_mut().zip(file.contents.iter()) {
                    side.extend_from_slice(term);
                }
            }
        }
        Ok(sides
            .into_iter()
            .map(|side| String::from_utf8_lossy(&side).into_owned())
            .collect())
    })
}

/// The resolved tree for `update_from_content`: the working-copy commit's tree
/// with `path`'s conflict replaced by the caller's content. `value` is the tree
/// value **before** materialization (kept for its executable-bit/copy-id shape);
/// the new file ids come from jj-lib's `update_from_content`.
pub(crate) async fn resolved_tree(
    tree: MergedTree,
    path: &RepoPath,
    value: jj_lib::merge::MergedTreeValue,
    content: &[u8],
) -> PyResult<MergedTree> {
    let store = tree.store();
    let materialized = conflicts::materialize_tree_value(store, path, value.clone(), tree.labels())
        .await
        .map_err(map_backend_err)?;
    let MaterializedTreeValue::FileConflict(file) = materialized else {
        return Err(ConflictError::new_err(format!(
            "path '{}' is not a conflicted file; nothing to resolve",
            path.as_internal_file_string()
        )));
    };
    let marker_len = conflicts::choose_materialized_conflict_marker_len(&file.contents);
    let new_file_ids =
        conflicts::update_from_content(&file.unsimplified_ids, store, path, content, marker_len)
            .await
            .map_err(map_backend_err)?;
    // Mirror jj-lib's working-copy snapshot (`local_working_copy.rs`): a fully resolved result
    // replaces the whole merge with one normal file value (preserving the resolved executable
    // bit and copy id); a still-conflicted result keeps the merge shape via `with_new_file_ids`.
    // A `FileConflict` is all-file/absent terms, so the executable/copy-id merges are present.
    let new_value = match new_file_ids.into_resolved() {
        Ok(Some(file_id)) => {
            let copy_id = value
                .to_copy_id_merge()
                .expect("file conflict has a copy-id merge")
                .resolve_trivial(jj_lib::merge::SameChange::Accept)
                .cloned()
                .flatten()
                .unwrap_or_else(jj_lib::backend::CopyId::placeholder);
            // `Merge::normal` wraps its argument in `Some`, so pass the bare file value.
            jj_lib::merge::Merge::normal(jj_lib::backend::TreeValue::File {
                id: file_id,
                executable: conflicts::resolve_file_executable(
                    &value
                        .to_executable_merge()
                        .expect("file conflict has an executable-bit merge"),
                )
                .unwrap_or(false),
                copy_id,
            })
        }
        // The resolution deleted the file (resolved to absent).
        Ok(None) => jj_lib::merge::Merge::resolved(None),
        Err(ids) => value.with_new_file_ids(&ids),
    };
    let mut builder = jj_lib::merged_tree_builder::MergedTreeBuilder::new(tree);
    builder.set_or_remove(path.to_owned(), new_value);
    pollster::block_on(builder.write_tree()).map_err(map_backend_err)
}
