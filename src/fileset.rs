//! Fileset parsing shared by every verb that takes `jj`-style path patterns.
//!
//! jj-lib owns the parser; jj-cli owns the small context policy around it. Pyjutsu reproduces
//! that policy once, here, so `RepoView.file_list`, `Transaction.fix`, and the snapshot's
//! `snapshot.auto-track` all interpret a pattern the same way the CLI does: a bare name is a
//! path prefix, `glob:`/`file:`/`root:` prefixes work, and several patterns union.

use std::path::PathBuf;

use jj_lib::fileset::{self, FilesetAliasesMap, FilesetDiagnostics, FilesetParseContext};
use jj_lib::matchers::{EverythingMatcher, Matcher, NothingMatcher, UnionMatcher};
use jj_lib::repo_path::RepoPathUiConverter;
use pyo3::PyResult;

use crate::errors::to_py_err;

/// The union of `patterns`, parsed against `workspace_root`.
///
/// `empty` decides what "no patterns" means, because the two callers disagree: `file_list` treats
/// an empty list as "every file" (the CLI's bare `jj file list`), while a `fix.tools` entry with
/// no `patterns` affects nothing (jj's documented rule). A malformed pattern is a `PyjutsuError`.
pub(crate) fn union_matcher(
    patterns: &[String],
    workspace_root: PathBuf,
    empty: EmptyPatterns,
) -> PyResult<Box<dyn Matcher>> {
    if patterns.is_empty() {
        return Ok(match empty {
            EmptyPatterns::MatchEverything => Box::new(EverythingMatcher),
            EmptyPatterns::MatchNothing => Box::new(NothingMatcher),
        });
    }
    // jj-lib 0.44 wraps the path converter in a `FilesetParseContext` (with an aliases map).
    let path_converter = RepoPathUiConverter::Fs {
        cwd: workspace_root.clone(),
        base: workspace_root,
    };
    let aliases_map = FilesetAliasesMap::new();
    let ctx = FilesetParseContext {
        aliases_map: &aliases_map,
        path_converter: &path_converter,
    };
    let mut matchers: Vec<Box<dyn Matcher>> = Vec::with_capacity(patterns.len());
    for expr in patterns {
        let mut diagnostics = FilesetDiagnostics::new();
        matchers.push(
            fileset::parse(&mut diagnostics, expr, &ctx)
                .map_err(to_py_err)?
                .to_matcher(),
        );
    }
    let mut iter = matchers.into_iter();
    let first = iter.next().expect("patterns is non-empty");
    Ok(iter.fold(first, |acc, m| Box::new(UnionMatcher::new(acc, m))))
}

/// What an empty pattern list means to the calling verb.
#[derive(Clone, Copy)]
pub(crate) enum EmptyPatterns {
    /// No patterns ⇒ every file (`jj file list` with no arguments).
    MatchEverything,
    /// No patterns ⇒ no file (a `fix.tools` entry with an empty `patterns`).
    MatchNothing,
}
