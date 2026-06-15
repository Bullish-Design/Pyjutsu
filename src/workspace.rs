//! `PyWorkspace` — opaque, `Send` handle to one jj workspace (one working-copy path).
//!
//! `jj_lib::Workspace` is `Send` but not `Sync`, so it's held behind a `Mutex` (concept §8.4).
//! M1 only reads, so the workspace's job here is path/identity + producing `PyRepoView`s
//! (head or historical). The repo behind it is `Arc`-loaded via its `RepoLoader`.

use std::path::PathBuf;
use std::sync::Mutex;

use pyo3::prelude::*;

use jj_lib::config::StackedConfig;
use jj_lib::object_id::ObjectId;
use jj_lib::op_walk;
use jj_lib::repo::StoreFactories;
use jj_lib::settings::UserSettings;
use jj_lib::workspace::{Workspace, default_working_copy_factories};

use crate::errors::{PyjutsuError, map_backend_err, map_workspace_err, to_py_err};
use crate::repo_view::PyRepoView;

#[pyclass(module = "pyjutsu._pyjutsu")]
pub(crate) struct PyWorkspace {
    inner: Mutex<Workspace>,
    /// The authoring email from settings — carried into the revset context of any view this
    /// workspace produces (so `author()`/`mine()` resolve consistently).
    user_email: String,
}

impl PyWorkspace {
    fn locked(&self) -> PyResult<std::sync::MutexGuard<'_, Workspace>> {
        self.inner
            .lock()
            .map_err(|_| PyjutsuError::new_err("workspace lock poisoned"))
    }
}

#[pymethods]
impl PyWorkspace {
    /// Load the workspace whose working copy is rooted at `path`.
    #[staticmethod]
    fn load(path: PathBuf) -> PyResult<Self> {
        // Built-in default config is enough to *read* a repo (no user name/email needed until
        // we author commits, in M2). UserSettings carries it through the RepoLoader.
        let settings =
            UserSettings::from_config(StackedConfig::with_defaults()).map_err(map_workspace_err)?;
        let user_email = settings.user_email().to_owned();
        let store_factories = StoreFactories::default();
        let working_copy_factories = default_working_copy_factories();
        let inner = Workspace::load(&settings, &path, &store_factories, &working_copy_factories)
            .map_err(map_workspace_err)?;
        Ok(Self {
            inner: Mutex::new(inner),
            user_email,
        })
    }

    /// This workspace's name/id (e.g. "default").
    fn name(&self) -> PyResult<String> {
        Ok(self.locked()?.workspace_name().as_str().to_owned())
    }

    /// The filesystem root of this workspace's working copy.
    fn workspace_root(&self) -> PyResult<PathBuf> {
        Ok(self.locked()?.workspace_root().to_owned())
    }

    /// A `PyRepoView` of the repo at its **head** operation, scoped to this workspace.
    fn head_view(&self, py: Python<'_>) -> PyResult<PyRepoView> {
        let ws = self.locked()?;
        let name = ws.workspace_name().to_owned();
        let root = ws.workspace_root().to_owned();
        let loader = ws.repo_loader();
        let repo = py
            .allow_threads(|| loader.load_at_head())
            .map_err(map_backend_err)?;
        Ok(PyRepoView::new(repo, name, root, self.user_email.clone()))
    }

    /// The id of the current head operation (what a fresh `head_view` loads at).
    fn head_operation(&self, py: Python<'_>) -> PyResult<String> {
        let ws = self.locked()?;
        let loader = ws.repo_loader();
        let repo = py
            .allow_threads(|| loader.load_at_head())
            .map_err(map_backend_err)?;
        Ok(repo.operation().id().hex())
    }

    /// A historical `PyRepoView` of the repo at the operation named by `op_str` (an op id,
    /// prefix, or expression like `@-`). Reads see that past state; nothing is written.
    fn at_operation(&self, py: Python<'_>, op_str: &str) -> PyResult<PyRepoView> {
        let ws = self.locked()?;
        let name = ws.workspace_name().to_owned();
        let root = ws.workspace_root().to_owned();
        let loader = ws.repo_loader();
        let repo = py.allow_threads(|| -> PyResult<_> {
            // An invalid/ambiguous op spec is user-input error → PyjutsuError base; a load
            // failure of a valid op is a backend problem.
            let op = op_walk::resolve_op_for_load(loader, op_str).map_err(to_py_err)?;
            loader.load_at(&op).map_err(map_backend_err)
        })?;
        Ok(PyRepoView::new(repo, name, root, self.user_email.clone()))
    }
}
