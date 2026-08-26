"""D3: git configuration (lane `004/d3`).

Oracle is the `git` binary: `git config --get` for the effective read, `git config --local --get`
for the repository-local write.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _git_local(repo: Path, key: str) -> str | None:
    result = _git(repo, "config", "--local", "--get", key)
    return result.stdout.strip() if result.returncode == 0 else None


def _git_effective(repo: Path, key: str) -> str | None:
    result = _git(repo, "config", "--get", key)
    return result.stdout.strip() if result.returncode == 0 else None


def test_set_then_get_matches_git(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.git.config_set("core.hooksPath", ".githooks")

    assert ws.git.config_get("core.hooksPath") == ".githooks"
    assert _git_local(scratch_repo, "core.hooksPath") == ".githooks"


def test_get_reads_a_value_git_wrote(scratch_repo: Path) -> None:
    _git(scratch_repo, "config", "--local", "user.signingkey", "ABC123")
    ws = pyjutsu.Workspace.load(scratch_repo)
    assert ws.git.config_get("user.signingkey") == "ABC123"
    assert ws.git.config_get("user.signingkey") == _git_effective(scratch_repo, "user.signingkey")


def test_get_returns_none_for_an_unset_key(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    assert ws.git.config_get("pyjutsu.neverSet") is None
    assert _git_effective(scratch_repo, "pyjutsu.neverSet") is None


def test_subsection_keys_round_trip(scratch_repo: Path) -> None:
    """`section.subsection.key` — the three-part form, here on a remote."""
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.git.config_set("remote.upstream.url", "https://example.invalid/repo.git")

    assert ws.git.config_get("remote.upstream.url") == "https://example.invalid/repo.git"
    assert _git_local(scratch_repo, "remote.upstream.url") == "https://example.invalid/repo.git"


def test_a_subsection_may_contain_dots(scratch_repo: Path) -> None:
    """Only the first and last components are fixed, exactly git's rule."""
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.git.config_set("remote.my.remote.url", "https://example.invalid/dotted.git")

    assert _git_local(scratch_repo, "remote.my.remote.url") == "https://example.invalid/dotted.git"
    assert ws.git.config_get("remote.my.remote.url") == "https://example.invalid/dotted.git"


def test_set_overwrites_an_existing_value(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.git.config_set("core.hooksPath", "first")
    ws.git.config_set("core.hooksPath", "second")

    assert ws.git.config_get("core.hooksPath") == "second"
    # One value, not a multivar.
    result = _git(scratch_repo, "config", "--local", "--get-all", "core.hooksPath")
    assert result.stdout.split() == ["second"]


def test_unset_removes_the_key(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.git.config_set("core.hooksPath", ".githooks")
    ws.git.config_unset("core.hooksPath")

    assert ws.git.config_get("core.hooksPath") is None
    assert _git_local(scratch_repo, "core.hooksPath") is None


def test_unset_of_an_absent_key_is_a_no_op(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.git.config_unset("pyjutsu.neverSet")  # must not raise
    assert ws.git.config_get("pyjutsu.neverSet") is None


def test_config_writes_publish_no_operation(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    before = ws.head_operation()
    ws.git.config_set("core.hooksPath", ".githooks")
    ws.git.config_unset("core.hooksPath")
    assert ws.head_operation() == before


def test_a_key_without_a_section_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(pyjutsu.PyjutsuError, match="no section"):
        ws.git.config_get("hooksPath")
    with pytest.raises(pyjutsu.PyjutsuError, match="no section"):
        ws.git.config_set("hooksPath", "x")


def test_existing_local_config_survives_a_write(scratch_repo: Path) -> None:
    """A write must not truncate the repository's own configuration file."""
    ws = pyjutsu.Workspace.load(scratch_repo)
    before = _git_local(scratch_repo, "core.bare")
    ws.git.config_set("pyjutsu.marker", "1")
    assert _git_local(scratch_repo, "core.bare") == before
    assert _git_local(scratch_repo, "pyjutsu.marker") == "1"
