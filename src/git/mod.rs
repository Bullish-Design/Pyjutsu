//! The git half of a colocated repository: annotated tags, config, HEAD, worktrees,
//! objects, submodules, reflog, and index reads.
//!
//! Native layout rule (concept §4): `#[pymethods]` blocks stay flat on
//! `PyWorkspace`; Python owns the `ws.git.*` namespace (see `python/pyjutsu/git.py`).
//! This module holds the implementations those flat methods delegate to.
//!
//! Depth rule (COLOCATED_GIT_SURFACE.md §4): prefer shallow, stable `gix::Repository`
//! methods. Anything reaching under `gix::refs::file::transaction` is a port hazard
//! with its own test; today that is exactly `apply_head_ref_packed`
//! (`src/workspace.rs`).

pub(crate) mod config;
pub(crate) mod head;
pub(crate) mod objects;
pub(crate) mod reflog;
pub(crate) mod submodules;
pub(crate) mod tags;
pub(crate) mod worktrees;
