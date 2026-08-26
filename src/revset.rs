//! The revset pipeline: parse → resolve symbols → evaluate → collect commits.
//!
//! This is jj-lib's hardest read API. The recipe mirrors jj-lib's own parse helpers
//! (revset.rs test module): build a `RevsetParseContext` with this workspace's path converter
//! and name so `@`, `file()`, etc. resolve correctly, then evaluate against the repo and
//! collect the matching commits. All revset reads (`resolve`, `log`, `conflicts`, `diff_stat`)
//! funnel through here.

use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, OnceLock};

use pyo3::PyErr;

use futures::TryStreamExt as _;

use jj_lib::backend::CommitId;
use jj_lib::commit::Commit;
use jj_lib::fileset::FilesetAliasesMap;
use jj_lib::ref_name::WorkspaceName;
use jj_lib::repo::Repo;
use jj_lib::repo_path::RepoPathUiConverter;
use jj_lib::revset::{
    self, Revset, RevsetAliasesMap, RevsetDiagnostics, RevsetExtensions, RevsetParseContext,
    RevsetStreamExt as _, RevsetWorkspaceContext, SymbolResolver, SymbolResolverExtension,
    UserRevsetExpression,
};
use jj_lib::settings::UserSettings;

use crate::errors::{map_backend_err, map_revset_err};

/// Parsed settings that affect every revset in one loaded workspace.
///
/// `Workspace::load()` creates this once from the resolved jj settings. Views and transactions
/// clone its `Arc`, so each parse sees the same aliases and author email without
/// reparsing configuration on every read.
pub(crate) struct RevsetConfig {
    aliases: RevsetAliasesMap,
    user_email: String,
    immutable_expression: OnceLock<Result<Arc<UserRevsetExpression>, String>>,
}

impl RevsetConfig {
    /// Build the revset context values from resolved settings.
    ///
    /// jj-lib exposes the parser-backed alias map, but not jj-cli's small config-table loader.
    /// Invalid declarations therefore become warnings and do not prevent a workspace from loading.
    pub(crate) fn from_settings(settings: &UserSettings) -> (Self, Vec<String>) {
        let config = settings.config();
        let mut aliases = RevsetAliasesMap::new();
        let mut warnings = Vec::new();
        for declaration in config.table_keys("revset-aliases") {
            let value = match config.get::<String>(["revset-aliases", declaration]) {
                Ok(value) => value,
                Err(err) => {
                    warnings.push(format!(
                        "ignored revset alias '{declaration}': {err}; fix revset-aliases.{declaration}"
                    ));
                    continue;
                }
            };
            if let Err(err) = aliases.insert(declaration, value, None) {
                warnings.push(format!(
                    "ignored revset alias '{declaration}': {err}; fix revset-aliases.{declaration}"
                ));
            }
        }
        (
            Self {
                aliases,
                user_email: settings.user_email().to_owned(),
                immutable_expression: OnceLock::new(),
            },
            warnings,
        )
    }

    /// Parse `immutable_heads().ancestors()` once for this transaction's workspace context.
    pub(crate) fn immutable_expression(
        &self,
        workspace_name: &WorkspaceName,
        workspace_root: &Path,
    ) -> Result<Arc<UserRevsetExpression>, PyErr> {
        self.immutable_expression
            .get_or_init(|| {
                parse_user_expression(self, "immutable_heads()", workspace_name, workspace_root)
                    .map(|heads| heads.ancestors())
                    .map_err(|err| err.to_string())
            })
            .as_ref()
            .map(Arc::clone)
            .map_err(map_revset_err)
    }
}

fn parse_user_expression(
    revset_config: &RevsetConfig,
    revset_str: &str,
    workspace_name: &WorkspaceName,
    workspace_root: &Path,
) -> Result<Arc<UserRevsetExpression>, PyErr> {
    let fileset_aliases = FilesetAliasesMap::new();
    let extensions = RevsetExtensions::default();
    let path_converter = RepoPathUiConverter::Fs {
        cwd: workspace_root.to_path_buf(),
        base: workspace_root.to_path_buf(),
    };
    let ws_ctx = RevsetWorkspaceContext {
        path_converter: &path_converter,
        workspace_name,
    };
    let ctx = RevsetParseContext {
        aliases_map: &revset_config.aliases,
        local_variables: HashMap::new(),
        user_email: &revset_config.user_email,
        date_pattern_context: chrono::Local::now().into(),
        default_ignored_remote: Some("git".as_ref()),
        fileset_aliases_map: &fileset_aliases,
        extensions: &extensions,
        workspace: Some(ws_ctx),
    };
    let mut diagnostics = RevsetDiagnostics::new();
    revset::parse(&mut diagnostics, revset_str, &ctx).map_err(map_revset_err)
}

/// Parse → resolve symbols → evaluate `revset_str` into an evaluated `Revset` (the id iterator),
/// borrowing `repo`. Shared prefix for [`evaluate`] (which collects commits) and [`evaluate_ids`]
/// (which collects ids), so the two never drift. `workspace_name`/`workspace_root` supply the
/// context for workspace-relative symbols (`@`, `file(...)`).
fn evaluate_revset<'a>(
    repo: &'a dyn Repo,
    revset_str: &str,
    workspace_name: &WorkspaceName,
    workspace_root: &Path,
    revset_config: &RevsetConfig,
) -> Result<Box<dyn Revset + 'a>, PyErr> {
    let expr = parse_user_expression(revset_config, revset_str, workspace_name, workspace_root)?;

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
    revset_config: &RevsetConfig,
) -> Result<Vec<Commit>, PyErr> {
    let revset = evaluate_revset(
        repo,
        revset_str,
        workspace_name,
        workspace_root,
        revset_config,
    )?;
    // jj-lib 0.42 made revset evaluation stream-based: `stream()` yields commit ids and the
    // `RevsetStreamExt::commits` adaptor resolves them to `Commit`s. Drive it synchronously off
    // the GIL (the caller already wraps us in `allow_threads`).
    let commits: Vec<Commit> =
        pollster::block_on(revset.stream().commits(repo.store()).try_collect())
            .map_err(map_backend_err)?;
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
    revset_config: &RevsetConfig,
) -> Result<Vec<CommitId>, PyErr> {
    let revset = evaluate_revset(
        repo,
        revset_str,
        workspace_name,
        workspace_root,
        revset_config,
    )?;
    // Parse/resolve errors already surfaced in `evaluate_revset` (mapped to `RevsetError`); a
    // failure *streaming* the evaluated set is a backend/store read, so classify it like
    // `evaluate`'s per-commit error (`map_backend_err`) — the two paths must agree.
    let ids: Vec<CommitId> =
        pollster::block_on(revset.stream().try_collect()).map_err(map_backend_err)?;
    Ok(ids)
}
