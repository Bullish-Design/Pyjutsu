//! Shortest unique id prefixes (lane C3).
//!
//! Open-decision resolution: disambiguate across the **whole repository**.
//! `IdPrefixContext::new` without `disambiguate_within` — no revset scope, no
//! vendored `revsets.short-prefixes` configuration key, always correct. jj-cli
//! scopes prefix disambiguation to `visible()` by default (its
//! `revsets.short-prefixes`, which Pyjutsu does not vendor); this binding
//! deliberately does not, because whole-repo disambiguation can never return a
//! prefix that resolves ambiguously. The cost is speed on very large repos
//! (the whole index is consulted rather than a narrow revset), which is the
//! accepted trade for the first release.

use pyo3::PyResult;

use jj_lib::backend::{ChangeId, CommitId};
use jj_lib::id_prefix::IdPrefixContext;
use jj_lib::object_id::ObjectId;
use jj_lib::repo::Repo;
use jj_lib::revset::RevsetExtensions;

use crate::errors::PyjutsuError;

/// The shortest prefix of `commit_id` (hex) that still resolves uniquely
/// within the whole repository, including against bookmark/tag names (the
/// same disambiguation the CLI's `commit_id.shortest()` applies).
pub(crate) fn shortest_commit_prefix(repo: &dyn Repo, commit_id: &CommitId) -> PyResult<String> {
    let context = IdPrefixContext::new(std::sync::Arc::new(RevsetExtensions::default()));
    let index = context
        .populate(repo)
        .map_err(|e| PyjutsuError::new_err(e.to_string()))?;
    let len = index
        .shortest_commit_prefix_len(repo, commit_id)
        .map_err(|e| PyjutsuError::new_err(e.to_string()))?;
    Ok(commit_id.hex()[..len].to_owned())
}

/// The shortest prefix of `change_id` (z-k form) that still resolves uniquely
/// within the whole repository, including against bookmark/tag names. The
/// z-k form and the raw hex have the same length, so a hex-computed prefix
/// length slices the z-k string directly.
pub(crate) fn shortest_change_prefix(repo: &dyn Repo, change_id: &ChangeId) -> PyResult<String> {
    let context = IdPrefixContext::new(std::sync::Arc::new(RevsetExtensions::default()));
    let index = context
        .populate(repo)
        .map_err(|e| PyjutsuError::new_err(e.to_string()))?;
    let len = index
        .shortest_change_prefix_len(repo, change_id)
        .map_err(|e| PyjutsuError::new_err(e.to_string()))?;
    Ok(change_id.reverse_hex()[..len].to_owned())
}

/// Dispatch `shortest_prefix(id)`: a hex string is a commit id, a z-k letter
/// string is a change id (the two alphabets are disjoint — hex is `0-9a-f`,
/// jj's z-k form is `k-z`). An unknown id yields a prefix that never matches
/// any id (jj's own contract), so it never resolves to a different commit.
pub(crate) fn shortest_prefix(repo: &dyn Repo, id: &str) -> PyResult<String> {
    let is_hex = !id.is_empty() && id.bytes().all(|b| b.is_ascii_hexdigit());
    let is_z_k = !id.is_empty() && id.bytes().all(|b| b.is_ascii_lowercase());
    if is_hex {
        let commit_id = CommitId::try_from_hex(id)
            .ok_or_else(|| PyjutsuError::new_err(format!("invalid hex commit id '{id}'")))?;
        shortest_commit_prefix(repo, &commit_id)
    } else if is_z_k {
        let change_id = ChangeId::try_from_reverse_hex(id)
            .ok_or_else(|| PyjutsuError::new_err(format!("invalid change id '{id}'")))?;
        shortest_change_prefix(repo, &change_id)
    } else {
        Err(PyjutsuError::new_err(
            "id must be a hex commit id or a z-k change id",
        ))
    }
}
