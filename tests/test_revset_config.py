"""Revset settings use the same resolved configuration as the pinned jj CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest


def _run_jj(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["jj", *args], cwd=repo, capture_output=True, check=True, text=True
    )
    return result.stdout


def _change_ids(repo: Path, revset: str) -> list[str]:
    return [
        line
        for line in _run_jj(repo, "log", "-r", revset, "--no-graph", "-T", 'change_id ++ "\\n"').splitlines()
        if line
    ]


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_jj(repo, "git", "init", "--colocate")
    for description in ("pattern one", "pattern two"):
        _run_jj(repo, "describe", "-m", description)
        _run_jj(repo, "new")
    return repo


def test_repository_alias_matches_cli_without_jj_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The secure repository layer drives both the CLI and Pyjutsu."""
    monkeypatch.delenv("JJ_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    repo = _make_repo(tmp_path)
    _run_jj(repo, "config", "set", "--repo", 'revset-aliases."chosen()"', "@-")

    view = pyjutsu.Workspace.load(repo).head()
    assert [commit.change_id for commit in view.log("chosen()")] == _change_ids(repo, "chosen()")


def test_user_and_repository_alias_precedence_matches_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JJ_CONFIG", raising=False)
    config_home = tmp_path / "config-home"
    config_dir = config_home / "jj"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('[revset-aliases]\n"chosen()" = "root()"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    repo = _make_repo(tmp_path)
    _run_jj(repo, "config", "set", "--repo", 'revset-aliases."chosen()"', "@-")

    actual = [commit.change_id for commit in pyjutsu.Workspace.load(repo).head().log("chosen()")]
    assert actual == _change_ids(repo, "chosen()")
    assert actual != _change_ids(repo, "root()")


def test_glob_setting_and_default_match_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JJ_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    repo = _make_repo(tmp_path)

    view = pyjutsu.Workspace.load(repo).head()
    pattern = 'description("pattern *")'
    assert [commit.change_id for commit in view.log(pattern)] == _change_ids(repo, pattern)

    _run_jj(repo, "config", "set", "--repo", "ui.revsets-use-glob-by-default", "false")
    view = pyjutsu.Workspace.load(repo).head()
    assert [commit.change_id for commit in view.log(pattern)] == _change_ids(repo, pattern)


def test_malformed_alias_warns_and_keeps_unrelated_revsets_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JJ_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    repo = _make_repo(tmp_path)
    config_path = Path(_run_jj(repo, "config", "path", "--repo").strip())
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('[revset-aliases]\n"bad(" = "@"\n')

    with pytest.warns(UserWarning, match="ignored revset alias"):
        view = pyjutsu.Workspace.load(repo).head()
    assert [commit.change_id for commit in view.log("@-")] == _change_ids(repo, "@-")
