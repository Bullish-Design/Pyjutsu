//! D3 — the colocated repository's git configuration.
//!
//! Reads are **effective**: they see the merged configuration git itself would use (system,
//! global, then repository-local), because that is what answers "what is `core.hooksPath` here".
//! Writes are **repository-local only** — never the user's global file. The two halves are
//! deliberately asymmetric, and both docstrings say so.
//!
//! Depth: `Repository::config_snapshot` is the shallow read. The write reaches one level down,
//! to `gix::config::File::set_raw_value_filter_by`, because gix's typed `SnapshotMut::set_value`
//! only accepts the statically-known keys in gix's own config tree, and this verb takes any key.
//! Persisting is jj-lib's own `git::save_git_config`, which writes exactly the sections whose
//! metadata matches the file's — that is, the repository-local ones.

use gix::bstr::{BStr, ByteSlice as _};
use jj_lib::git;
use jj_lib::repo::Repo as _;
use pyo3::PyResult;

use crate::errors::{PyjutsuError, map_backend_err, map_git_err};
use crate::workspace::PyWorkspace;

/// A git configuration key split into its three parts, as `git config` names them.
struct Key {
    section: String,
    subsection: Option<String>,
    value_name: String,
}

/// Split `section.key` or `section.subsection.key`. A subsection may itself contain dots
/// (`remote.my.remote.url`), so only the first and last components are fixed — exactly git's rule.
fn parse_key(key: &str) -> PyResult<Key> {
    let (section, rest) = key.split_once('.').ok_or_else(|| {
        PyjutsuError::new_err(format!(
            "git config key '{key}' has no section; use 'section.key' or \
             'section.subsection.key'"
        ))
    })?;
    if section.is_empty() {
        return Err(PyjutsuError::new_err(format!(
            "git config key '{key}' has an empty section name"
        )));
    }
    let (subsection, value_name) = match rest.rsplit_once('.') {
        Some((subsection, value_name)) => (Some(subsection.to_owned()), value_name.to_owned()),
        None => (None, rest.to_owned()),
    };
    if value_name.is_empty() {
        return Err(PyjutsuError::new_err(format!(
            "git config key '{key}' has an empty value name"
        )));
    }
    Ok(Key {
        section: section.to_owned(),
        subsection,
        value_name,
    })
}

/// The **effective** value of `key`, or `None` if no configuration source sets it.
pub(crate) fn get(
    workspace: &PyWorkspace,
    py: pyo3::Python<'_>,
    key: &str,
) -> PyResult<Option<String>> {
    let parsed = parse_key(key)?;
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    py.allow_threads(move || -> PyResult<Option<String>> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let config = git_repo.config_snapshot();
        let value = config.plumbing().raw_value_by(
            parsed.section.as_str(),
            parsed.subsection.as_deref().map(BStr::new),
            parsed.value_name.as_str(),
        );
        Ok(value.ok().map(|v| v.to_str_lossy().into_owned()))
    })
}

/// Set `key` to `value` in the **repository-local** configuration file.
pub(crate) fn set(
    workspace: &PyWorkspace,
    py: pyo3::Python<'_>,
    key: &str,
    value: &str,
) -> PyResult<()> {
    let parsed = parse_key(key)?;
    let value = value.to_owned();
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    py.allow_threads(move || -> PyResult<()> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let mut config = git_repo.config_snapshot().plumbing().clone();
        // The same metadata `save_git_config` filters on, so the write lands in the
        // repository-local file and never in the user's global one.
        let local = config.meta().clone();
        config
            .set_raw_value_filter_by(
                parsed.section.as_str(),
                parsed.subsection.as_deref().map(BStr::new),
                // Passed by value: `ValueName: TryFrom<String>` yields an owned, `'static`
                // name, which is what a `File<'static>` requires.
                parsed.value_name,
                BStr::new(value.as_str()),
                |meta| *meta == local,
            )
            .map_err(map_git_err)?;
        git::save_git_config(&config).map_err(map_git_err)
    })
}

/// Remove `key` from the **repository-local** configuration file. Removing a key that is not set
/// locally is a no-op, matching `git config --unset --local` on an absent key (which exits 5;
/// this verb returns instead of raising, because "already absent" is the caller's goal).
pub(crate) fn unset(workspace: &PyWorkspace, py: pyo3::Python<'_>, key: &str) -> PyResult<()> {
    let parsed = parse_key(key)?;
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    py.allow_threads(move || -> PyResult<()> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let mut config = git_repo.config_snapshot().plumbing().clone();
        let local = config.meta().clone();
        let removed = match config.section_mut_filter(
            parsed.section.as_str(),
            parsed.subsection.as_deref().map(BStr::new),
            |meta| *meta == local,
        ) {
            Ok(Some(mut section)) => section.remove(&parsed.value_name).is_some(),
            // No local section with that name, or none the filter accepts: nothing to unset.
            Ok(None) | Err(_) => false,
        };
        if !removed {
            return Ok(());
        }
        git::save_git_config(&config).map_err(map_git_err)
    })
}
