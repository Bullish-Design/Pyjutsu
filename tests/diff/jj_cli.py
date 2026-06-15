"""Driver for the devenv-pinned `jj` CLI — the other half of every differential test.

Each operation Pyjutsu performs is checked against the exact `jj` 0.38.0 binary the devenv
pins (concept §7). This module just runs that binary against a repo with an isolated config
so results are reproducible and independent of the developer's `~/.jjconfig`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Minimal isolated config: a fixed identity so `jj` can author commits deterministically and
# nothing leaks in from the host's user config.
_CONFIG_TOML = '[user]\nname = "Pyjutsu Test"\nemail = "test@pyjutsu.invalid"\n'


def write_config(directory: Path) -> Path:
    """Write the isolated jj config into ``directory`` and return its path."""
    config = directory / "jjconfig.toml"
    config.write_text(_CONFIG_TOML)
    return config


class JjCli:
    """Runs the pinned ``jj`` CLI against repos, using one isolated config file."""

    def __init__(self, config: Path) -> None:
        self._config = config

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["JJ_CONFIG"] = str(self._config)
        return env

    def __call__(self, repo: Path, *args: str) -> str:
        """Run ``jj <args>`` inside ``repo`` and return stdout (raising on non-zero exit)."""
        result = subprocess.run(
            ["jj", *args],
            cwd=repo,
            env=self._env(),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def init_colocated(self, repo: Path) -> None:
        """``jj git init --colocate`` in ``repo``."""
        self(repo, "git", "init", "--colocate")

    def template(self, repo: Path, revset: str, expr: str) -> str:
        """Render jj template ``expr`` for the single revision ``revset`` (stripped)."""
        return self(repo, "log", "-r", revset, "--no-graph", "-T", expr).strip()
