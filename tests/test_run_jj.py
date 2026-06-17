"""Slice 3 — the ``run_jj`` escape hatch: raw subprocess, read-back parity with the typed surface.

These prove the escape hatch runs the pinned ``jj`` against the *same* repo the in-process surface
sees (so a CLI read agrees with a typed read, and a CLI mutation is visible to a typed read), and
that ``check``/binary-resolution behave. It parses nothing — only raw text/exit cross the boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pyjutsu import JjResult, Workspace
from pyjutsu.errors import JjCliError


def test_run_jj_reads_back(scratch_repo: Path) -> None:
    """The escape hatch's CLI read agrees with the typed read of ``@``."""
    ws = Workspace.load(scratch_repo)
    result = ws.run_jj(["log", "-r", "@", "--no-graph", "-T", "change_id"])
    assert isinstance(result, JjResult)
    assert result.returncode == 0
    assert result.stdout.strip() == ws.working_copy().change_id


def test_run_jj_check_raises(scratch_repo: Path) -> None:
    """``check=True`` (default) raises JjCliError carrying the non-zero code + stderr."""
    ws = Workspace.load(scratch_repo)
    with pytest.raises(JjCliError) as excinfo:
        ws.run_jj(["definitely-not-a-jj-command"])
    err = excinfo.value
    assert err.returncode is not None and err.returncode != 0
    assert err.stderr  # jj wrote a diagnostic to stderr


def test_run_jj_no_check_returns(scratch_repo: Path) -> None:
    """``check=False`` returns the result for a failing command instead of raising."""
    ws = Workspace.load(scratch_repo)
    result = ws.run_jj(["definitely-not-a-jj-command"], check=False)
    assert result.returncode != 0
    assert result.args == ["definitely-not-a-jj-command"]


def test_run_jj_mutation_visible(scratch_repo: Path) -> None:
    """A CLI mutation through the escape hatch is visible to a fresh typed read (same repo)."""
    ws = Workspace.load(scratch_repo)
    ws.run_jj(["describe", "-m", "via cli"])
    # jj normalizes descriptions with a trailing newline; the typed read returns it verbatim.
    assert Workspace.load(scratch_repo).working_copy().description.strip() == "via cli"


def test_run_jj_binary_missing(scratch_repo: Path) -> None:
    """An unlaunchable binary raises JjCliError rather than leaking the OSError."""
    ws = Workspace.load(scratch_repo)
    with pytest.raises(JjCliError):
        ws.run_jj(["status"], jj_binary="/nonexistent/jj")


def test_jj_version(scratch_repo: Path) -> None:
    """The optional version helper returns the external jj's version string."""
    ws = Workspace.load(scratch_repo)
    assert ws.jj_version().startswith("jj ")
