"""Modern repository and workspace configuration loading."""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest

from tests.diff.jj_cli import JjCli


def _author_from_root(workspace: pyjutsu.Workspace, description: str) -> pyjutsu.Commit:
    with workspace.transaction(description) as tx:
        return tx.new("root()")


def test_secondary_workspace_loads_secure_repo_identity(
    tmp_path: Path,
    jj: JjCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "config-home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    repo = tmp_path / "repo-config"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "config", "set", "--repo", "user.name", "Repository Author")
    jj(repo, "config", "set", "--repo", "user.email", "repo@example.invalid")

    secondary_path = tmp_path / "secondary-config"
    jj(repo, "workspace", "add", "--name", "secondary", "-r", "root()", str(secondary_path))

    primary_commit = _author_from_root(pyjutsu.Workspace.load(repo), "primary identity")
    secondary_commit = _author_from_root(
        pyjutsu.Workspace.load(secondary_path), "secondary identity"
    )

    expected = ("Repository Author", "repo@example.invalid")
    assert (primary_commit.author.name, primary_commit.author.email) == expected
    assert (secondary_commit.author.name, secondary_commit.author.email) == expected
    cli_signature = jj.signature(secondary_path, "@", "author")
    assert (cli_signature["name"], cli_signature["email"]) == expected


def test_workspace_config_overrides_repo_config(
    tmp_path: Path,
    jj: JjCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    repo = tmp_path / "workspace-config-repo"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "config", "set", "--repo", "user.name", "Repository Author")
    jj(repo, "config", "set", "--repo", "user.email", "repo@example.invalid")

    secondary_path = tmp_path / "workspace-config-secondary"
    jj.add_workspace(repo, secondary_path, name="secondary", revisions=["root()"])
    jj(secondary_path, "config", "set", "--workspace", "user.name", "Workspace Author")
    jj(
        secondary_path,
        "config",
        "set",
        "--workspace",
        "user.email",
        "workspace@example.invalid",
    )

    primary_commit = _author_from_root(pyjutsu.Workspace.load(repo), "primary config")
    secondary_commit = _author_from_root(
        pyjutsu.Workspace.load(secondary_path), "workspace config"
    )

    assert (primary_commit.author.name, primary_commit.author.email) == (
        "Repository Author",
        "repo@example.invalid",
    )
    assert (secondary_commit.author.name, secondary_commit.author.email) == (
        "Workspace Author",
        "workspace@example.invalid",
    )


def test_conditional_config_uses_canonical_repo_workspace_and_environment(
    tmp_path: Path,
    jj: JjCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.setenv("PYJUTSU_CONFIG_TEST", "enabled")
    repo = tmp_path / "conditional-repo"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "config", "set", "--repo", "user.name", "Fallback Author")
    jj(repo, "config", "set", "--repo", "user.email", "fallback@example.invalid")

    secondary_path = tmp_path / "conditional-secondary"
    jj.add_workspace(repo, secondary_path, name="secondary", revisions=["root()"])
    config_path = jj.config_path(repo, "repo")
    config_path.write_text(
        config_path.read_text()
        + f"""

[[--scope]]
--when.repositories = [{str((repo / '.jj' / 'repo').resolve())!r}]
[--scope.user]
name = "Repository Path Author"

[[--scope]]
--when.workspaces = [{str(secondary_path.resolve())!r}]
[--scope.user]
name = "Workspace Path Author"

[[--scope]]
--when.environments = ["PYJUTSU_CONFIG_TEST=enabled"]
[--scope.user]
email = "environment@example.invalid"
"""
    )

    primary_commit = _author_from_root(pyjutsu.Workspace.load(repo), "conditional primary")
    secondary_commit = _author_from_root(
        pyjutsu.Workspace.load(secondary_path), "conditional secondary"
    )
    jj(secondary_path, "new", "root()")
    cli_signature = jj.signature(secondary_path, "@", "author")

    assert (primary_commit.author.name, primary_commit.author.email) == (
        "Repository Path Author",
        "environment@example.invalid",
    )
    expected_secondary = ("Workspace Path Author", "environment@example.invalid")
    assert (secondary_commit.author.name, secondary_commit.author.email) == expected_secondary
    assert (cli_signature["name"], cli_signature["email"]) == expected_secondary


def test_environment_overrides_workspace_config(
    tmp_path: Path,
    jj: JjCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    repo = tmp_path / "environment-repo"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "config", "set", "--repo", "user.name", "Repository Author")
    jj(repo, "config", "set", "--repo", "user.email", "repo@example.invalid")
    secondary_path = tmp_path / "environment-secondary"
    jj.add_workspace(repo, secondary_path, name="secondary", revisions=["root()"])
    jj(secondary_path, "config", "set", "--workspace", "user.name", "Workspace Author")
    jj(
        secondary_path,
        "config",
        "set",
        "--workspace",
        "user.email",
        "workspace@example.invalid",
    )
    monkeypatch.setenv("JJ_USER", "Environment Author")
    monkeypatch.setenv("JJ_EMAIL", "environment@example.invalid")

    commit = _author_from_root(pyjutsu.Workspace.load(secondary_path), "environment override")
    assert (commit.author.name, commit.author.email) == (
        "Environment Author",
        "environment@example.invalid",
    )


def test_platform_config_and_conf_d_load_when_jj_config_is_unset(
    tmp_path: Path,
    jj: JjCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "platform-repo"
    repo.mkdir()
    jj.init_colocated(repo)

    config_home = tmp_path / "platform-config"
    config_dir = config_home / "jj"
    conf_d = config_dir / "conf.d"
    conf_d.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[user]\nname = "Platform Author"\nemail = "platform@example.invalid"\n'
    )
    (conf_d / "20-identity.toml").write_text('[user]\nname = "Conf D Author"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("JJ_CONFIG")

    commit = _author_from_root(pyjutsu.Workspace.load(repo), "platform identity")
    assert (commit.author.name, commit.author.email) == (
        "Conf D Author",
        "platform@example.invalid",
    )


def test_legacy_home_config_loads_when_jj_config_is_unset(
    tmp_path: Path,
    jj: JjCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "legacy-user-repo"
    repo.mkdir()
    jj.init_colocated(repo)

    home = tmp_path / "home"
    home.mkdir()
    (home / ".jjconfig.toml").write_text(
        '[user]\nname = "Legacy Author"\nemail = "legacy@example.invalid"\n'
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "unused-platform-config"))
    monkeypatch.delenv("JJ_CONFIG")

    commit = _author_from_root(pyjutsu.Workspace.load(repo), "legacy identity")
    assert (commit.author.name, commit.author.email) == (
        "Legacy Author",
        "legacy@example.invalid",
    )


def test_explicit_empty_jj_config_disables_default_user_paths(
    tmp_path: Path,
    jj: JjCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "disabled-platform-config"
    platform_dir = config_home / "jj"
    platform_dir.mkdir(parents=True)
    (platform_dir / "config.toml").write_text(
        '[user]\nname = "Must Not Load"\nemail = "platform@example.invalid"\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    repo = tmp_path / "empty-jj-config-repo"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "config", "set", "--repo", "user.email", "repo@example.invalid")
    monkeypatch.setenv("JJ_CONFIG", "")

    commit = _author_from_root(pyjutsu.Workspace.load(repo), "empty JJ_CONFIG")
    assert commit.author.name == ""
    assert commit.author.email == "repo@example.invalid"


def test_normal_load_does_not_create_secure_config(
    tmp_path: Path,
    jj: JjCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "empty-config-home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    repo = tmp_path / "no-config-repo"
    repo.mkdir()
    jj.init_colocated(repo)

    pyjutsu.Workspace.load(repo)

    assert not (repo / ".jj" / "repo" / "config-id").exists()
    assert not (repo / ".jj" / "workspace-config-id").exists()
    assert not (config_home / "jj" / "repos").exists()
    assert not (config_home / "jj" / "workspaces").exists()


def test_init_does_not_create_secure_config(
    tmp_path: Path,
    jj: JjCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`init` re-resolves settings through the load path, but still writes no secure config."""
    config_home = tmp_path / "init-empty-config-home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    repo = tmp_path / "init-no-config-repo"
    repo.mkdir()

    pyjutsu.Workspace.init(repo)

    assert not (repo / ".jj" / "repo" / "config-id").exists()
    assert not (repo / ".jj" / "workspace-config-id").exists()
    assert not (config_home / "jj" / "repos").exists()
    assert not (config_home / "jj" / "workspaces").exists()


def test_init_handle_applies_repository_conditional_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handle `init` returns must resolve conditional scopes keyed on the repository path.

    `init` bootstraps with `repo_path = None`, so a `--when.repositories` scope cannot match while
    the repository is being created. The returned handle must re-resolve settings afterwards.
    """
    # The conditional scope belongs in the *user* config. `JJ_CONFIG` (set by the `jj` fixture)
    # would shadow the platform path, so remove it and use `XDG_CONFIG_HOME` alone.
    monkeypatch.delenv("JJ_CONFIG", raising=False)
    config_home = tmp_path / "init-scope-config-home"
    config_dir = config_home / "jj"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    repo = tmp_path / "init-scope-repo"
    repo.mkdir()
    repo_config_path = repo.resolve() / ".jj" / "repo"
    (config_dir / "config.toml").write_text(
        f"""[user]
name = "Base Author"
email = "base@example.invalid"

[[--scope]]
--when.repositories = [{str(repo_config_path)!r}]
[--scope.user]
name = "Scoped Author"
"""
    )

    init_handle = pyjutsu.Workspace.init(repo)
    init_commit = _author_from_root(init_handle, "init identity")
    load_commit = _author_from_root(pyjutsu.Workspace.load(repo), "load identity")

    expected = ("Scoped Author", "base@example.invalid")
    assert (init_commit.author.name, init_commit.author.email) == expected
    assert (load_commit.author.name, load_commit.author.email) == expected


def test_secure_config_migration_warning_reaches_python(
    tmp_path: Path,
    jj: JjCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "migration-config-home"))
    repo = tmp_path / "migration-repo"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / ".jj" / "repo" / "config.toml").write_text(
        '[user]\nname = "Migrated Author"\nemail = "migrated@example.invalid"\n'
    )

    with pytest.warns(UserWarning, match="migrated"):
        workspace = pyjutsu.Workspace.load(repo)
    commit = _author_from_root(workspace, "migrated identity")
    assert (commit.author.name, commit.author.email) == (
        "Migrated Author",
        "migrated@example.invalid",
    )
