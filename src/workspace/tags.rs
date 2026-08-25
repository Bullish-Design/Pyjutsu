//! Annotated Git tag creation and push helpers.

use std::collections::HashMap;
use std::sync::Arc;

use jj_lib::git::{
    self, GitImportOptions, GitPushOptions, GitPushRefTargets, GitSubprocessOptions,
};
use jj_lib::merge::Diff;
use jj_lib::object_id::ObjectId;
use jj_lib::ref_name::{RefName, RefNameBuf, RemoteName};
use jj_lib::repo::{ReadonlyRepo, Repo};
use jj_lib::workspace::Workspace;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use super::{NullGitCallback, PyWorkspace};
use crate::errors::{RevsetError, map_backend_err, map_git_err};

pub(super) fn create_tag<'py>(
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

        // Write the annotated object and ref directly because jj-lib 0.42 cannot create it.
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

pub(super) fn push_tag<'py>(
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
