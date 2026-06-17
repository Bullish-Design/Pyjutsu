//! The revset pipeline: parse → resolve symbols → evaluate → collect commits.
//!
//! This is jj-lib's hardest read API. The recipe mirrors jj-lib's own parse helpers
//! (revset.rs test module): build a `RevsetParseContext` with this workspace's path converter
//! and name so `@`, `file()`, etc. resolve correctly, then evaluate against the repo and
//! collect the matching commits. All revset reads (`resolve`, `log`, `conflicts`, `diff_stat`)
//! funnel through here.

use std::collections::HashMap;
use std::path::Path;

use pyo3::PyErr;

use jj_lib::backend::CommitId;
use jj_lib::commit::Commit;
use jj_lib::ref_name::WorkspaceName;
use jj_lib::repo::Repo;
use jj_lib::repo_path::RepoPathUiConverter;
use jj_lib::revset::{
    self, Revset, RevsetAliasesMap, RevsetDiagnostics, RevsetExtensions, RevsetIteratorExt as _,
    RevsetParseContext, RevsetWorkspaceContext, SymbolResolver, SymbolResolverExtension,
};

use crate::errors::{map_backend_err, map_revset_err};

/// Parse → resolve symbols → evaluate `revset_str` into an evaluated `Revset` (the id iterator),
/// borrowing `repo`. Shared prefix for [`evaluate`] (which collects commits) and [`evaluate_ids`]
/// (which collects ids), so the two never drift. `workspace_name`/`workspace_root` supply the
/// context for workspace-relative symbols (`@`, `file(...)`).
fn evaluate_revset<'a>(
    repo: &'a dyn Repo,
    revset_str: &str,
    workspace_name: &WorkspaceName,
    workspace_root: &Path,
    user_email: &str,
) -> Result<Box<dyn Revset + 'a>, PyErr> {
    let aliases = RevsetAliasesMap::new();
    let extensions = RevsetExtensions::default();
    // `Fs { cwd, base }` lets `file(<relative>)` resolve against the workspace root, matching
    // how the CLI interprets path arguments from the workspace root.
    let path_converter = RepoPathUiConverter::Fs {
        cwd: workspace_root.to_path_buf(),
        base: workspace_root.to_path_buf(),
    };
    let ws_ctx = RevsetWorkspaceContext {
        path_converter: &path_converter,
        workspace_name,
    };
    let ctx = RevsetParseContext {
        aliases_map: &aliases,
        local_variables: HashMap::new(),
        user_email,
        date_pattern_context: chrono::Local::now().into(),
        default_ignored_remote: Some("git".as_ref()), // jj hides the implicit "git" remote
        use_glob_by_default: false,
        extensions: &extensions,
        workspace: Some(ws_ctx),
    };

    let mut diagnostics = RevsetDiagnostics::new();
    let expr = revset::parse(&mut diagnostics, revset_str, &ctx).map_err(map_revset_err)?;

    let no_extensions: &[Box<dyn SymbolResolverExtension>] = &[];
    let resolver = SymbolResolver::new(repo, no_extensions);
    let resolved = expr
        .resolve_user_expression(repo, &resolver)
        .map_err(map_revset_err)?;
    resolved.evaluate(repo).map_err(map_revset_err)
}

/// Evaluate `revset_str` against `repo` and return the matching commits in revset order.
///
/// Self-contained so the caller can run it inside `Python::allow_threads`.
pub(crate) fn evaluate(
    repo: &dyn Repo,
    revset_str: &str,
    workspace_name: &WorkspaceName,
    workspace_root: &Path,
    user_email: &str,
) -> Result<Vec<Commit>, PyErr> {
    let revset = evaluate_revset(repo, revset_str, workspace_name, workspace_root, user_email)?;
    let mut commits = Vec::new();
    for commit in revset.iter().commits(repo.store()) {
        commits.push(commit.map_err(map_backend_err)?);
    }
    Ok(commits)
}

/// Evaluate `revset_str` and return only the matching **commit ids** in revset order — the cheap,
/// bounded half of [`evaluate`] (no per-commit backend reads). Streaming reads collect ids here
/// eagerly, then build one `CommitData` at a time so the revset/iter (which borrow `repo`) are not
/// held across `__next__`. Self-contained for `Python::allow_threads`.
pub(crate) fn evaluate_ids(
    repo: &dyn Repo,
    revset_str: &str,
    workspace_name: &WorkspaceName,
    workspace_root: &Path,
    user_email: &str,
) -> Result<Vec<CommitId>, PyErr> {
    let revset = evaluate_revset(repo, revset_str, workspace_name, workspace_root, user_email)?;
    let mut ids = Vec::new();
    for id in revset.iter() {
        ids.push(id.map_err(map_revset_err)?);
    }
    Ok(ids)
}
