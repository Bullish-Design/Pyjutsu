//! Lightweight jj tag and annotated Git tag helpers.
//!
//! jj-lib gap (checked 0.42.0 and 0.44.0): jj-lib cannot **create** an annotated tag.
//! It only copies an existing annotated tag ref during export (`git.rs:1410`), and
//! `MutableRepo::set_local_tag_target` (0.44 `repo.rs:1850`) writes a *lightweight* tag.
//! Tag object creation therefore goes straight to `gix`. Re-check this on every jj-lib
//! upgrade; drop the workaround as soon as jj-lib exposes an annotated-tag writer.

use std::collections::HashMap;
use std::sync::Arc;

use jj_lib::git::{
    self, GitImportOptions, GitPushOptions, GitPushRefTargets, GitSubprocessOptions,
};
use jj_lib::merge::Diff;
use jj_lib::object_id::ObjectId;
use jj_lib::op_store::RefTarget;
use jj_lib::ref_name::{RefName, RefNameBuf, RemoteName};
use jj_lib::repo::{ReadonlyRepo, Repo};
use jj_lib::workspace::Workspace;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::errors::{RevsetError, map_backend_err, map_git_err};
use crate::workspace::{NullGitCallback, PyWorkspace};

pub(crate) fn create_tag<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
    name: &str,
    target: &str,
    message: Option<&str>,
    force: bool,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    match message {
        Some(message) => create_annotated_tag(workspace, py, name, target, message, force),
        None => create_lightweight_tag(workspace, py, name, target, force),
    }
}

fn create_lightweight_tag<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
    name: &str,
    target: &str,
    force: bool,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let mut guard = workspace.locked()?;
    let ws: &mut Workspace = &mut guard;
    let ws_name = ws.workspace_name().to_owned();
    let ws_root = ws.workspace_root().to_owned();
    let revset_config = workspace.revset_config.clone();
    let repo = {
        let loader = ws.repo_loader();
        py.allow_threads(|| pollster::block_on(loader.load_at_head()))
            .map_err(map_backend_err)?
    };

    let name_owned = name.to_owned();
    let target_owned = target.to_owned();
    let new_repo = py.allow_threads(|| -> PyResult<Option<Arc<ReadonlyRepo>>> {
        let commits = crate::revset::evaluate(
            repo.as_ref(),
            &target_owned,
            &ws_name,
            &ws_root,
            &revset_config,
        )?;
        if commits.len() != 1 {
            return Err(RevsetError::new_err(format!(
                "revset '{target_owned}' resolved to {} revisions, expected exactly 1",
                commits.len()
            )));
        }

        let tag_name: &RefName = name_owned.as_str().as_ref();
        if !force && !repo.view().get_local_tag(tag_name).is_absent() {
            return Err(map_git_err(format!(
                "tag '{name_owned}' already exists; pass force=True to replace it"
            )));
        }

        let mut tx = repo.start_transaction();
        tx.repo_mut()
            .set_local_tag_target(tag_name, RefTarget::normal(commits[0].id().clone()));
        let stats = git::export_refs(tx.repo_mut()).map_err(map_git_err)?;
        if !stats.failed_tags.is_empty() {
            let names = stats
                .failed_tags
                .iter()
                .map(|(symbol, _reason)| symbol.to_string())
                .collect::<Vec<_>>()
                .join(", ");
            return Err(map_git_err(format!("failed to export tag: {names}")));
        }
        pollster::block_on(tx.repo_mut().rebase_descendants()).map_err(map_backend_err)?;
        if !tx.repo_mut().has_changes() {
            return Ok(None);
        }
        Ok(Some(
            pollster::block_on(tx.commit(format!("create tag {name_owned}")))
                .map_err(map_backend_err)?,
        ))
    })?;

    let Some(new_repo) = new_repo else {
        return Ok(None);
    };
    Ok(Some(
        workspace.finish_op(py, ws, &ws_name, &repo, &new_repo)?,
    ))
}

fn create_annotated_tag<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
    name: &str,
    target: &str,
    message: &str,
    force: bool,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let mut guard = workspace.locked()?;
    let ws: &mut Workspace = &mut guard;
    let ws_name = ws.workspace_name().to_owned();
    let ws_root = ws.workspace_root().to_owned();
    let revset_config = workspace.revset_config.clone();
    let user_name = ws.repo_loader().settings().user_name().to_owned();
    let user_email = ws.repo_loader().settings().user_email().to_owned();
    let repo = {
        let loader = ws.repo_loader();
        py.allow_threads(|| pollster::block_on(loader.load_at_head()))
            .map_err(map_backend_err)?
    };

    let name_owned = name.to_owned();
    let target_owned = target.to_owned();
    let message_owned = message.to_owned();

    let new_repo = py.allow_threads(|| -> PyResult<Option<Arc<ReadonlyRepo>>> {
        // Resolve `target` to exactly one commit. Jujutsu commit IDs are Git object IDs.
        let commits = crate::revset::evaluate(
            repo.as_ref(),
            &target_owned,
            &ws_name,
            &ws_root,
            &revset_config,
        )?;
        if commits.len() != 1 {
            return Err(RevsetError::new_err(format!(
                "revset '{target_owned}' resolved to {} revisions, expected exactly 1",
                commits.len()
            )));
        }
        let target_oid = gix::ObjectId::try_from(commits[0].id().as_bytes())
            .map_err(|e| map_git_err(format!("invalid target commit id: {e}")))?;

        // Write the annotated object and ref directly because jj-lib 0.44 cannot create it.
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let time = format!("{} +0000", chrono::Utc::now().timestamp());
        let name_bstr: &gix::bstr::BStr = user_name.as_str().into();
        let email_bstr: &gix::bstr::BStr = user_email.as_str().into();
        let tagger = gix::actor::SignatureRef {
            name: name_bstr,
            email: email_bstr,
            time: &time,
        };
        let constraint = if force {
            gix::refs::transaction::PreviousValue::Any
        } else {
            gix::refs::transaction::PreviousValue::MustNotExist
        };
        git_repo
            .tag(
                &name_owned,
                target_oid,
                gix::objs::Kind::Commit,
                Some(tagger),
                &message_owned,
                constraint,
            )
            .map_err(|e| map_git_err(format!("failed to create tag '{name_owned}': {e}")))?;

        // Import only the new ref. Do not abandon the tag's old target when a force move occurs.
        let options = GitImportOptions {
            abandon_unreachable_commits: false,
            record_synthetic_predecessors: true,
            remote_auto_track_bookmarks: HashMap::new(),
        };
        let mut tx = repo.start_transaction();
        pollster::block_on(git::import_refs(tx.repo_mut(), &options)).map_err(map_git_err)?;
        pollster::block_on(tx.repo_mut().rebase_descendants()).map_err(map_backend_err)?;
        if !tx.repo_mut().has_changes() {
            return Ok(None);
        }
        Ok(Some(
            pollster::block_on(tx.commit(format!("create tag {name_owned}")))
                .map_err(map_backend_err)?,
        ))
    })?;

    let Some(new_repo) = new_repo else {
        return Ok(None);
    };
    Ok(Some(
        workspace.finish_op(py, ws, &ws_name, &repo, &new_repo)?,
    ))
}

pub(crate) fn push_tag<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
    name: &str,
    remote: &str,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let mut guard = workspace.locked()?;
    let ws: &mut Workspace = &mut guard;
    let ws_name = ws.workspace_name().to_owned();
    let loader = PyWorkspace::fresh_loader(ws)?;
    let settings = ws.repo_loader().settings().clone();
    let remote = remote.to_owned();
    let name_owned = name.to_owned();

    let new_repo = py.allow_threads(move || -> PyResult<Option<Arc<ReadonlyRepo>>> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let subprocess = GitSubprocessOptions::from_settings(&settings).map_err(map_git_err)?;
        let remote_name: &RemoteName = remote.as_str().as_ref();
        let tag_ref: &RefName = name_owned.as_str().as_ref();

        let view = repo.view();
        let Some(new_target) = view.get_local_tag(tag_ref).as_normal().cloned() else {
            return Err(map_git_err(format!("no local tag '{name_owned}'")));
        };
        let remote_ref = view.get_remote_tag(tag_ref.to_remote_symbol(remote_name));
        let old_target = if remote_ref.target.is_absent() {
            None
        } else if let Some(id) = remote_ref.target.as_normal() {
            Some(id.clone())
        } else {
            return Err(map_git_err(format!(
                "remote tag '{name_owned}@{remote}' is conflicted"
            )));
        };
        if old_target.as_ref() == Some(&new_target) {
            return Ok(None);
        }

        let targets = GitPushRefTargets {
            bookmarks: vec![],
            tags: vec![(
                RefNameBuf::from(name_owned.as_str()),
                Diff {
                    before: old_target,
                    after: Some(new_target),
                },
            )],
        };
        let mut tx = repo.start_transaction();
        let stats = git::push_refs(
            tx.repo_mut(),
            subprocess,
            remote_name,
            &targets,
            &mut NullGitCallback,
            &GitPushOptions::default(),
        )
        .map_err(map_git_err)?;
        if !stats.all_ok() {
            let mut reasons = Vec::new();
            for (ref_name, why) in stats.rejected.iter().chain(stats.remote_rejected.iter()) {
                let ref_name = ref_name.as_symbol();
                match why {
                    Some(reason) => reasons.push(format!("{ref_name} ({reason})")),
                    None => reasons.push(ref_name.to_string()),
                }
            }
            return Err(map_git_err(format!(
                "push to remote '{remote}' rejected: {}",
                reasons.join(", ")
            )));
        }
        if !tx.repo_mut().has_changes() {
            return Ok(None);
        }
        Ok(Some(
            pollster::block_on(
                tx.commit(format!("push tag {name_owned} to git remote '{remote}'")),
            )
            .map_err(map_backend_err)?,
        ))
    })?;

    let Some(new_repo) = new_repo else {
        return Ok(None);
    };
    let repo = {
        let loader = ws.repo_loader();
        py.allow_threads(|| pollster::block_on(loader.load_at_head()))
            .map_err(map_backend_err)?
    };
    Ok(Some(
        workspace.finish_op(py, ws, &ws_name, &repo, &new_repo)?,
    ))
}

/// One tag row read from the on-disk git refs, for `ws.git.tag(name)` /
/// `ws.git.tags()`. Plain data: no `gix` type crosses the FFI.
pub(crate) struct GitTagData {
    name: String,
    /// The commit the tag points at (fully peeled). For an annotated tag this
    /// is the tag object's target commit; for a lightweight tag the direct
    /// target.
    target: String,
    annotated: bool,
    message: Option<String>,
    tagger_name: Option<String>,
    tagger_email: Option<String>,
    tagger_timestamp_ms: Option<i64>,
    tagger_tz_offset_minutes: Option<i32>,
}

impl GitTagData {
    /// Build a row from one `refs/tags/*` reference. The reference's direct
    /// target decides the kind: a tag object ⇒ annotated (decode message +
    /// tagger), anything else ⇒ lightweight (no message, no tagger).
    fn build(git_repo: &gix::Repository, git_ref: &mut gix::Reference<'_>) -> PyResult<Self> {
        let direct_id = match git_ref.target() {
            gix::refs::TargetRef::Object(id) => id.to_owned(),
            _ => {
                return Err(map_git_err(format!(
                    "tag ref '{}' is symbolic; expected a direct object target",
                    git_ref.name()
                )));
            }
        };
        // Fully-peeled target: the commit (or other object) the tag chain ends at.
        let target = git_ref
            .peel_to_id()
            .map_err(map_git_err)?
            .detach()
            .to_hex()
            .to_string();
        let full_name = git_ref.name().as_bstr();
        let name = String::from_utf8_lossy(
            full_name
                .strip_prefix(b"refs/tags/")
                .unwrap_or(full_name.as_ref()),
        )
        .into_owned();
        match git_repo.find_tag(direct_id.to_owned()) {
            // The direct target is a tag object → annotated tag.
            Ok(tag) => {
                let tag_ref = tag.decode().map_err(map_git_err)?;
                // Git stores the tag message with a trailing newline; surface the clean message.
                let message = Some(
                    String::from_utf8_lossy(tag_ref.message)
                        .trim_end_matches(['\n', '\r'])
                        .to_owned(),
                );
                let mut tagger_name = None;
                let mut tagger_email = None;
                let mut tagger_timestamp_ms = None;
                let mut tagger_tz_offset_minutes = None;
                if let Some(tagger) = tag.tagger().map_err(map_git_err)? {
                    let time = tagger.time().map_err(map_git_err)?;
                    tagger_name = Some(String::from_utf8_lossy(tagger.name).into_owned());
                    tagger_email = Some(String::from_utf8_lossy(tagger.email).into_owned());
                    tagger_timestamp_ms = Some(time.seconds * 1000);
                    tagger_tz_offset_minutes = Some(time.offset / 60);
                }
                Ok(Self {
                    name,
                    target,
                    annotated: true,
                    message,
                    tagger_name,
                    tagger_email,
                    tagger_timestamp_ms,
                    tagger_tz_offset_minutes,
                })
            }
            // Not a tag object → lightweight tag (the direct target is the commit).
            Err(_) => Ok(Self {
                name,
                target,
                annotated: false,
                message: None,
                tagger_name: None,
                tagger_email: None,
                tagger_timestamp_ms: None,
                tagger_tz_offset_minutes: None,
            }),
        }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("name", &self.name)?;
        dict.set_item("target", &self.target)?;
        dict.set_item("annotated", self.annotated)?;
        dict.set_item("message", self.message.as_deref())?;
        if let Some(name) = &self.tagger_name {
            let tagger = PyDict::new(py);
            tagger.set_item("name", name)?;
            tagger.set_item("email", self.tagger_email.as_deref())?;
            tagger.set_item("timestamp_ms", self.tagger_timestamp_ms)?;
            tagger.set_item("tz_offset_minutes", self.tagger_tz_offset_minutes)?;
            dict.set_item("tagger", tagger)?;
        } else {
            dict.set_item("tagger", None::<&str>)?;
        }
        Ok(dict)
    }
}

/// Read one tag by name from the on-disk git refs → its plain row, or `None`
/// if no such ref exists. Reads `refs/tags/<name>` directly; the tag need not
/// be imported into jj's view.
pub(crate) fn read_tag<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
    name: &str,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    let full_name: gix::refs::FullName = format!("refs/tags/{name}")
        .try_into()
        .map_err(|e| map_git_err(format!("bad tag name '{name}': {e}")))?;
    let row = py.allow_threads(move || -> PyResult<Option<GitTagData>> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let mut git_ref = match git_repo.find_reference(&full_name) {
            Ok(git_ref) => git_ref,
            Err(gix::reference::find::existing::Error::NotFound { .. }) => return Ok(None),
            Err(err) => return Err(map_git_err(err.to_string())),
        };
        Ok(Some(GitTagData::build(&git_repo, &mut git_ref)?))
    })?;
    row.as_ref().map(|r| r.to_dict(py)).transpose()
}

/// List every tag in the on-disk git refs (``refs/tags/*``) → one plain row
/// each, sorted by tag name. Reads the refs directly, like `git for-each-ref
/// refs/tags`.
pub(crate) fn read_tags<'py>(
    workspace: &PyWorkspace,
    py: Python<'py>,
) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let guard = workspace.locked()?;
    let loader = PyWorkspace::fresh_loader(&guard)?;
    let rows = py.allow_threads(|| -> PyResult<Vec<GitTagData>> {
        let repo = pollster::block_on(loader.load_at_head()).map_err(map_backend_err)?;
        let git_repo = git::get_git_repo(repo.store()).map_err(map_git_err)?;
        let mut out = Vec::new();
        for git_ref in git_repo
            .references()
            .map_err(map_git_err)?
            .prefixed("refs/tags/")
            .map_err(map_git_err)?
        {
            let mut git_ref = git_ref.map_err(map_git_err)?;
            out.push(GitTagData::build(&git_repo, &mut git_ref)?);
        }
        out.sort_by(|a, b| a.name.cmp(&b.name));
        Ok(out)
    })?;
    rows.iter().map(|r| r.to_dict(py)).collect()
}
