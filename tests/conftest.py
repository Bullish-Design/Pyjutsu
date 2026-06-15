"""Shared fixtures: a scratch jj repo built by the pinned CLI, plus the CLI driver itself."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.diff.jj_cli import JjCli, write_config

#: Description set on `@` in the scratch repo; tests assert the binding reads it back.
WC_DESCRIPTION = "hello from pyjutsu test"


@pytest.fixture
def jj(tmp_path: Path) -> JjCli:
    """A `jj` CLI driver bound to an isolated config under this test's tmp dir."""
    return JjCli(write_config(tmp_path))


@pytest.fixture
def scratch_repo(tmp_path: Path, jj: JjCli) -> Path:
    """A fresh colocated jj repo with one described working-copy commit (`@`)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "describe", "-m", WC_DESCRIPTION)
    return repo
