//! C7 — `jj fix`: run the tools jj's own `fix.tools` configuration names over a revset.
//!
//! jj-lib owns the graph half (`fix::fix_files` walks the descendants, deduplicates file
//! content, rewrites the commits, and can never create a conflict). It does **not** own the
//! tool half.
//!
//! jj-lib gap (checked 0.44.0): no tool runner for `fix.tools`. jj-lib exposes the `FileFixer`
//! trait, `ParallelFileFixer`, and `compute_changed_ranges`, but the configuration schema and
//! the subprocess invocation belong to jj-**cli**, which is not published to crates.io. That
//! policy is vendored here, like `src/config/revsets.toml` and `git_object_hash`, and belongs
//! on the per-upgrade re-verification list. Two vendored items:
//!
//! - the `fix.tools` schema (`command`, `patterns`, `enabled`, `line-range-arg`,
//!   `run-tool-if-zero-line-ranges`), read from `jj help -k config`, chapter "Code formatting
//!   and other file content transformations", on the pinned 0.44.0 binary;
//! - the `revsets.fix` default, [`DEFAULT_FIX_REVSET`].

use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use jj_lib::backend::FileId;
use jj_lib::config::ConfigGetResultExt as _;
use jj_lib::fix::{FileToFix, FixError, ParallelFileFixer, RegionsToFormat};
use jj_lib::matchers::Matcher;
use jj_lib::repo_path::RepoPath;
use jj_lib::settings::UserSettings;
use jj_lib::store::Store;
use pyo3::PyResult;

use crate::errors::{PyjutsuError, to_py_err};
use crate::fileset::{EmptyPatterns, union_matcher};

/// jj-cli's default for `revsets.fix`, vendored because Pyjutsu does not link jj-cli.
///
/// **Re-verification list entry.** Confirm against the pinned binary at every jj upgrade:
/// `jj config list --include-defaults revsets`.
pub(crate) const DEFAULT_FIX_REVSET: &str = "reachable(@, mutable())";

/// One enabled entry of jj's `fix.tools` table, with its patterns already parsed.
struct CompiledTool {
    name: String,
    command: Vec<String>,
    matcher: Box<dyn Matcher>,
    line_range_arg: Option<String>,
    run_tool_if_zero_line_ranges: bool,
}

/// Read `revsets.fix`, falling back to jj-cli's own default.
pub(crate) fn configured_fix_revset(settings: &UserSettings) -> PyResult<String> {
    match settings.get_string("revsets.fix").optional() {
        Ok(Some(value)) => Ok(value),
        Ok(None) => Ok(DEFAULT_FIX_REVSET.to_owned()),
        Err(err) => Err(to_py_err(err)),
    }
}

/// Load the enabled `fix.tools` entries, optionally restricted to `selected` names.
///
/// Tools run in **name order**. jj's documentation fixes which files a tool sees but not the
/// order two tools that match the same file run in; sorting by name makes Pyjutsu's answer
/// deterministic instead of dependent on configuration-layer merge order.
fn load_tools(
    settings: &UserSettings,
    workspace_root: &Path,
    selected: Option<&[String]>,
) -> PyResult<Vec<CompiledTool>> {
    let config = settings.config();
    let mut names: Vec<String> = config
        .table_keys(["fix", "tools"])
        .map(str::to_owned)
        .collect();
    names.sort();
    names.dedup();
    if let Some(selected) = selected {
        for want in selected {
            if !names.contains(want) {
                return Err(PyjutsuError::new_err(format!(
                    "no such fix tool '{want}'; fix.tools defines {names:?}"
                )));
            }
        }
        names.retain(|name| selected.contains(name));
    }

    let mut tools = Vec::new();
    for name in names {
        let enabled: bool = config
            .get(["fix", "tools", &name, "enabled"])
            .optional()
            .map_err(to_py_err)?
            .unwrap_or(true);
        if !enabled {
            continue;
        }
        let command: Vec<String> = config
            .get(["fix", "tools", &name, "command"])
            .map_err(to_py_err)?;
        if command.is_empty() {
            return Err(PyjutsuError::new_err(format!(
                "fix.tools.{name}.command is empty; it must name an executable"
            )));
        }
        let patterns: Vec<String> = config
            .get(["fix", "tools", &name, "patterns"])
            .optional()
            .map_err(to_py_err)?
            .unwrap_or_default();
        let line_range_arg: Option<String> = config
            .get(["fix", "tools", &name, "line-range-arg"])
            .optional()
            .map_err(to_py_err)?;
        let run_tool_if_zero_line_ranges: bool = config
            .get(["fix", "tools", &name, "run-tool-if-zero-line-ranges"])
            .optional()
            .map_err(to_py_err)?
            .unwrap_or(false);
        tools.push(CompiledTool {
            name,
            command,
            // An entry with no `patterns` affects no file — jj's documented rule.
            matcher: union_matcher(
                &patterns,
                workspace_root.to_owned(),
                EmptyPatterns::MatchNothing,
            )?,
            line_range_arg,
            run_tool_if_zero_line_ranges,
        });
    }
    Ok(tools)
}

/// Substitute jj's two documented variables into one command argument.
fn substitute(arg: &str, workspace_root: &Path, repo_path: &RepoPath) -> String {
    arg.replace("$root", &workspace_root.to_string_lossy())
        .replace("$path", repo_path.as_internal_file_string())
}

/// Render `line-range-arg` once per changed range (1-based, inclusive).
fn line_range_args(template: &str, regions: &RegionsToFormat) -> Vec<String> {
    let RegionsToFormat::LineRanges(ranges) = regions;
    ranges
        .iter()
        .map(|range| {
            template
                .replace("$first", &range.first.to_string())
                .replace("$last", &range.last.to_string())
        })
        .collect()
}

/// Pipe `content` through one tool. `None` means "the tool declined to change anything":
/// either it failed, or its output equals its input. A failing tool never rewrites a file —
/// jj's rule is "only rewritten if the subprocess produces a successful exit code".
fn run_tool(
    tool: &CompiledTool,
    workspace_root: &Path,
    repo_path: &RepoPath,
    content: &[u8],
    extra_args: &[String],
) -> Result<Option<Vec<u8>>, FixError> {
    let args: Vec<String> = tool
        .command
        .iter()
        .map(|arg| substitute(arg, workspace_root, repo_path))
        .chain(extra_args.iter().cloned())
        .collect();
    let mut child = Command::new(&args[0])
        .args(&args[1..])
        .current_dir(workspace_root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(FixError::Io)?;
    child
        .stdin
        .take()
        .expect("stdin was piped")
        .write_all(content)
        .map_err(FixError::Io)?;
    let output = child.wait_with_output().map_err(FixError::Io)?;
    if !output.status.success() {
        return Ok(None);
    }
    Ok(Some(output.stdout))
}

/// Read one file's whole content out of the store.
fn read_blob(store: &Store, path: &RepoPath, id: &FileId) -> Result<Vec<u8>, FixError> {
    use futures::AsyncReadExt as _;
    pollster::block_on(async {
        let mut reader = store.read_file(path, id).await?;
        let mut buf = Vec::new();
        reader.read_to_end(&mut buf).await.map_err(FixError::Io)?;
        Ok(buf)
    })
}

/// Build the `FileFixer` jj-lib drives: for each file, run every matching tool in name order,
/// chaining each tool's output into the next, and return the new `FileId` when the content moved.
fn build_fixer<'a>(
    tools: &'a [CompiledTool],
    workspace_root: &'a Path,
    all_lines: bool,
) -> ParallelFileFixer<
    impl Fn(&Store, &FileToFix) -> Result<Option<FileId>, FixError> + Sync + Send + use<'a>,
> {
    ParallelFileFixer::new(move |store: &Store, file_to_fix: &FileToFix| {
        let path = &file_to_fix.repo_path;
        let matching: Vec<&CompiledTool> =
            tools.iter().filter(|t| t.matcher.matches(path)).collect();
        if matching.is_empty() {
            return Ok(None);
        }
        let original = read_blob(store, path, &file_to_fix.file_id)?;
        // The changed line ranges are computed once, against the base content jj-lib resolved
        // for this file (absent for a newly added file — then every line is "changed").
        let base = match &file_to_fix.base_file_id {
            Some(base_id) => read_blob(store, path, base_id)?,
            None => Vec::new(),
        };
        let regions = jj_lib::fix::compute_changed_ranges(&base, &original);
        let RegionsToFormat::LineRanges(ranges) = &regions;
        let zero_ranges = ranges.is_empty();

        let mut content = original.clone();
        for tool in matching {
            if zero_ranges && !all_lines && !tool.run_tool_if_zero_line_ranges {
                continue;
            }
            let extra_args = match (&tool.line_range_arg, all_lines) {
                (Some(template), false) => line_range_args(template, &regions),
                _ => Vec::new(),
            };
            if let Some(output) = run_tool(tool, workspace_root, path, &content, &extra_args)? {
                content = output;
            }
        }
        if content == original {
            return Ok(None);
        }
        let new_id = pollster::block_on(store.write_file(path, &mut content.as_slice()))?;
        Ok(Some(new_id))
    })
}

/// Everything `Transaction.fix` needs that is not the jj-lib call itself.
pub(crate) struct FixPlan {
    tools: Vec<CompiledTool>,
    workspace_root: PathBuf,
    /// The fileset restricting which paths are considered at all (`jj fix [FILESETS]`).
    pub(crate) matcher: Box<dyn Matcher>,
    pub(crate) all_lines: bool,
}

impl FixPlan {
    /// Compile the configured tools and the path fileset. Errors if `fix.tools` is empty or
    /// names a tool that is not configured — a silent no-op would look like a working fix.
    pub(crate) fn build(
        settings: &UserSettings,
        workspace_root: PathBuf,
        tools: Option<&[String]>,
        paths: Option<&[String]>,
        all_lines: bool,
    ) -> PyResult<Self> {
        let compiled = load_tools(settings, &workspace_root, tools)?;
        if compiled.is_empty() {
            return Err(PyjutsuError::new_err(
                "no enabled fix tools; configure jj's `fix.tools` table (see `jj help -k config`)",
            ));
        }
        let matcher = union_matcher(
            paths.unwrap_or_default(),
            workspace_root.clone(),
            EmptyPatterns::MatchEverything,
        )?;
        Ok(Self {
            tools: compiled,
            workspace_root,
            matcher,
            all_lines,
        })
    }

    /// The names of the tools that will run, in the order they run.
    pub(crate) fn tool_names(&self) -> Vec<String> {
        self.tools.iter().map(|t| t.name.clone()).collect()
    }

    /// The `FileFixer` to hand to `jj_lib::fix::fix_files`.
    pub(crate) fn fixer(
        &self,
    ) -> ParallelFileFixer<
        impl Fn(&Store, &FileToFix) -> Result<Option<FileId>, FixError> + Sync + Send + use<'_>,
    > {
        build_fixer(&self.tools, &self.workspace_root, self.all_lines)
    }
}
