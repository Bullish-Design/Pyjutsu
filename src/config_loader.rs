//! Jujutsu 0.44 configuration policy for workspace authoring.
//!
//! jj-lib provides configuration layers, secure configuration, and conditional resolution.
//! jj-cli provides environment and user-path policy. Pyjutsu reproduces the small relevant policy
//! here so every workspace gets the same `UserSettings` without depending on the CLI crate.

use std::collections::HashMap;
use std::env;
use std::path::{Path, PathBuf};

use etcetera::BaseStrategy as _;
use jj_lib::config::{ConfigLayer, ConfigResolutionContext, ConfigSource, StackedConfig};
use jj_lib::secure_config::SecureConfig;
use jj_lib::settings::UserSettings;
use jj_lib::workspace::{
    DefaultWorkspaceLoaderFactory, WorkspaceLoader, WorkspaceLoaderFactory as _,
};
use pyo3::PyErr;
use rand::SeedableRng as _;
use rand_chacha::ChaCha20Rng;

use crate::errors::map_workspace_err;

const REPO_CONFIG_DIR: &str = "repos";
const WORKSPACE_CONFIG_DIR: &str = "workspaces";
const OP_HOSTNAME: &str = "operation.hostname";
const OP_USERNAME: &str = "operation.username";
const REVSET_DEFAULTS: &str = include_str!("config/revsets.toml");

/// Final settings and the loader that resolved their repository identity.
pub(crate) struct ResolvedWorkspaceSettings {
    pub settings: UserSettings,
    pub loader: Box<dyn WorkspaceLoader>,
    pub warnings: Vec<String>,
}

struct ConfigEnvironment {
    config_dir: Option<PathBuf>,
    home_dir: Option<PathBuf>,
    hostname: String,
    environment: HashMap<String, String>,
    rng: ChaCha20Rng,
}

impl ConfigEnvironment {
    fn from_environment() -> Self {
        let config_dir = etcetera::choose_base_strategy()
            .ok()
            .map(|strategy| strategy.config_dir());
        let home_dir = etcetera::home_dir()
            .ok()
            .map(|path| dunce::canonicalize(&path).unwrap_or(path));
        let hostname = whoami::hostname().unwrap_or_default();
        let environment = env::vars_os()
            .filter_map(|(key, value)| Some((key.into_string().ok()?, value.into_string().ok()?)))
            .collect();
        let rng = if let Ok(seed) = env::var("JJ_RANDOMNESS_SEED")
            .and_then(|s| s.parse::<u64>().map_err(|_| env::VarError::NotPresent))
        {
            ChaCha20Rng::seed_from_u64(seed)
        } else {
            rand::make_rng()
        };
        Self {
            config_dir,
            home_dir,
            hostname,
            environment,
            rng,
        }
    }

    fn root_config_dir(&self) -> Option<PathBuf> {
        self.config_dir.as_deref().map(|path| path.join("jj"))
    }

    fn user_config_paths(&self) -> Vec<PathBuf> {
        if let Ok(paths) = env::var("JJ_CONFIG") {
            return env::split_paths(&paths)
                .filter(|path| !path.as_os_str().is_empty())
                .collect();
        }

        let legacy = self
            .home_dir
            .as_deref()
            .map(|home| home.join(".jjconfig.toml"));
        let platform_file = self
            .config_dir
            .as_deref()
            .map(|dir| dir.join("jj").join("config.toml"));
        let platform_dir = self
            .config_dir
            .as_deref()
            .map(|dir| dir.join("jj").join("conf.d"));

        let mut paths = Vec::new();
        if let Some(path) = legacy
            && (path.exists() || platform_file.is_none())
        {
            paths.push(path);
        }
        if let Some(path) = platform_file {
            paths.push(path);
        }
        if let Some(path) = platform_dir
            && path.exists()
        {
            paths.push(path);
        }
        paths
    }
}

fn set_value(
    layer: &mut ConfigLayer,
    name: &'static str,
    value: impl Into<jj_lib::config::ConfigValue>,
) {
    layer
        .set_value(name, value)
        .expect("fixed config key must be valid");
}

/// Values supplied by the process environment at Jujutsu's two precedence positions.
fn environment_layers() -> (ConfigLayer, ConfigLayer) {
    let mut base = ConfigLayer::empty(ConfigSource::EnvBase);
    if let Ok(value) = whoami::hostname() {
        set_value(&mut base, OP_HOSTNAME, value);
    }
    if let Ok(value) = whoami::username() {
        set_value(&mut base, OP_USERNAME, value);
    } else if let Ok(value) = env::var("USER") {
        set_value(&mut base, OP_USERNAME, value);
    }
    if !env::var("NO_COLOR").unwrap_or_default().is_empty() {
        set_value(&mut base, "ui.color", "never");
    }
    if let Ok(value) = env::var("VISUAL") {
        set_value(&mut base, "ui.editor", value);
    } else if let Ok(value) = env::var("EDITOR") {
        set_value(&mut base, "ui.editor", value);
    }

    let mut overrides = ConfigLayer::empty(ConfigSource::EnvOverrides);
    for (variable, key) in [
        ("JJ_USER", "user.name"),
        ("JJ_EMAIL", "user.email"),
        ("JJ_TIMESTAMP", "debug.commit-timestamp"),
        ("JJ_OP_TIMESTAMP", "debug.operation-timestamp"),
        ("JJ_OP_HOSTNAME", OP_HOSTNAME),
        ("JJ_OP_USERNAME", OP_USERNAME),
        ("JJ_EDITOR", "ui.editor"),
        ("JJ_PAGER", "ui.pager"),
    ] {
        if let Ok(value) = env::var(variable) {
            set_value(&mut overrides, key, value);
        }
    }
    if let Ok(value) = env::var("JJ_RANDOMNESS_SEED")
        && let Ok(seed) = value.parse::<i64>()
    {
        set_value(&mut overrides, "debug.randomness-seed", seed);
    }
    (base, overrides)
}

fn base_config(config_env: &ConfigEnvironment) -> Result<StackedConfig, PyErr> {
    let mut config = StackedConfig::with_defaults();
    let revset_defaults = ConfigLayer::parse(ConfigSource::Default, REVSET_DEFAULTS)
        .expect("the vendored jj revset defaults must parse");
    config.add_layer(revset_defaults);
    let (base, overrides) = environment_layers();
    config.add_layer(base);
    config.add_layer(overrides);
    for path in config_env.user_config_paths() {
        if path.is_dir() {
            config
                .load_dir(ConfigSource::User, path)
                .map_err(map_workspace_err)?;
        } else if path.is_file() {
            config
                .load_file(ConfigSource::User, path)
                .map_err(map_workspace_err)?;
        }
    }
    Ok(config)
}

fn load_secure_layer(
    config: &mut StackedConfig,
    source: ConfigSource,
    secure: &SecureConfig,
    root: Option<&Path>,
    kind: &str,
    rng: &mut ChaCha20Rng,
    warnings: &mut Vec<String>,
) -> Result<(), PyErr> {
    let Some(root) = root else {
        return Ok(());
    };
    let loaded = secure
        .maybe_load_config(rng, &root.join(kind))
        .map_err(map_workspace_err)?;
    warnings.extend(loaded.warnings);
    if let Some(path) = loaded.config_file
        && path.is_file()
    {
        config.load_file(source, path).map_err(map_workspace_err)?;
    }
    Ok(())
}

fn resolve_settings(
    config: StackedConfig,
    config_env: &ConfigEnvironment,
    repo_path: Option<&Path>,
    workspace_path: Option<&Path>,
) -> Result<UserSettings, PyErr> {
    let context = ConfigResolutionContext {
        home_dir: config_env.home_dir.as_deref(),
        repo_path,
        workspace_path,
        command: None,
        hostname: &config_env.hostname,
        environment: &config_env.environment,
    };
    let resolved = jj_lib::config::resolve(&config, &context).map_err(map_workspace_err)?;
    UserSettings::from_config(resolved).map_err(map_workspace_err)
}

/// Load settings before repository initialization, when no repository configuration exists.
pub(crate) fn bootstrap_user_settings() -> Result<UserSettings, PyErr> {
    let config_env = ConfigEnvironment::from_environment();
    let config = base_config(&config_env)?;
    resolve_settings(config, &config_env, None, None)
}

/// The four `SignBehavior` names jj's `signing.behavior` key accepts.
///
/// Vendored, like the `git.object-hash` values: jj-lib defines the enum
/// (`signing.rs`, `SignBehavior`) but its serde names are the configuration contract, and
/// rejecting a bad name here gives a clear error instead of a config-deserialization one.
/// Re-diff at every jj-lib upgrade.
pub(crate) const SIGN_BEHAVIORS: [&str; 4] = ["drop", "keep", "own", "force"];

/// Resolve workspace identity first, then load every existing Jujutsu configuration layer.
///
/// `sign_behavior`, when given, is applied as a final `--config`-strength layer over
/// `signing.behavior`, so one loaded workspace can sign differently from the user's default
/// without editing any configuration file.
pub(crate) fn resolved_workspace_settings(
    workspace_root: &Path,
    sign_behavior: Option<&str>,
) -> Result<ResolvedWorkspaceSettings, PyErr> {
    let workspace_path = dunce::canonicalize(workspace_root).map_err(map_workspace_err)?;
    let loader = DefaultWorkspaceLoaderFactory
        .create(&workspace_path)
        .map_err(map_workspace_err)?;
    let repo_path = dunce::canonicalize(loader.repo_path()).map_err(map_workspace_err)?;

    let mut config_env = ConfigEnvironment::from_environment();
    let mut config = base_config(&config_env)?;
    let mut warnings = Vec::new();
    let root_config_dir = config_env.root_config_dir();
    load_secure_layer(
        &mut config,
        ConfigSource::Repo,
        &SecureConfig::new_repo(repo_path.clone()),
        root_config_dir.as_deref(),
        REPO_CONFIG_DIR,
        &mut config_env.rng,
        &mut warnings,
    )?;
    load_secure_layer(
        &mut config,
        ConfigSource::Workspace,
        &SecureConfig::new_workspace(workspace_path.join(".jj")),
        root_config_dir.as_deref(),
        WORKSPACE_CONFIG_DIR,
        &mut config_env.rng,
        &mut warnings,
    )?;
    if let Some(behavior) = sign_behavior {
        if !SIGN_BEHAVIORS.contains(&behavior) {
            return Err(map_workspace_err(format!(
                "invalid sign_behavior {behavior:?}: expected one of {SIGN_BEHAVIORS:?}"
            )));
        }
        let mut layer = ConfigLayer::empty(ConfigSource::CommandArg);
        layer
            .set_value("signing.behavior", behavior)
            .map_err(map_workspace_err)?;
        config.add_layer(layer);
    }
    let settings = resolve_settings(config, &config_env, Some(&repo_path), Some(&workspace_path))?;
    Ok(ResolvedWorkspaceSettings {
        settings,
        loader,
        warnings,
    })
}
