"""Configured revset immutability matches the pinned jj policy where Pyjutsu rewrites history."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import ImmutableCommitError

from tests.diff.jj_cli import JjCli


def _protect_previous_commit(repo: Path, jj: JjCli) -> None:
    jj(repo, "config", "set", "--repo", 'revset-aliases."immutable_heads()"', "@-")


def test_default_immutability_only_protects_the_vendored_trunk_root(
    linear_repo: Path,
) -> None:
    """A normal non-root commit remains mutable until a user config says otherwise."""
    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("describe mutable", auto_snapshot=False) as tx:
        changed = tx.describe("@-", "still mutable")
    assert changed.description == "still mutable\n"


def test_repository_immutability_matches_the_pinned_cli(linear_repo: Path, jj: JjCli) -> None:
    _protect_previous_commit(linear_repo, jj)

    cli = subprocess.run(
        ["jj", "describe", "-r", "@-", "-m", "blocked"],
        cwd=linear_repo,
        capture_output=True,
        text=True,
    )
    assert cli.returncode != 0
    assert "immutable" in cli.stderr.lower()

    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(ImmutableCommitError, match=r"immutable_heads\(\)"):
        with ws.transaction("blocked", auto_snapshot=False) as tx:
            tx.describe("@-", "blocked")


def test_repository_configuration_can_widen_and_narrow_immutability(
    linear_repo: Path, jj: JjCli
) -> None:
    _protect_previous_commit(linear_repo, jj)
    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(ImmutableCommitError):
        with ws.transaction("blocked", auto_snapshot=False) as tx:
            tx.describe("@-", "blocked")

    jj(
        linear_repo,
        "config",
        "set",
        "--repo",
        'revset-aliases."immutable_heads()"',
        "root()",
    )
    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("allowed", auto_snapshot=False) as tx:
        changed = tx.describe("@-", "allowed")
    assert changed.description == "allowed\n"


def test_ignore_immutable_bypasses_configuration_but_never_root(
    linear_repo: Path, jj: JjCli
) -> None:
    _protect_previous_commit(linear_repo, jj)
    ws = pyjutsu.Workspace.load(linear_repo)

    with ws.transaction("administrative rewrite", auto_snapshot=False, ignore_immutable=True) as tx:
        changed = tx.describe("@-", "administratively changed")
    assert changed.description == "administratively changed\n"

    with pytest.raises(ImmutableCommitError, match="root commit"):
        with ws.transaction("never root", auto_snapshot=False, ignore_immutable=True) as tx:
            tx.describe("root()", "never")


Rewrite = Callable[[pyjutsu.Transaction], object]


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda tx: tx.describe("@-", "blocked"), id="describe"),
        pytest.param(lambda tx: tx.edit("@-"), id="edit"),
        pytest.param(lambda tx: tx.abandon("@-"), id="abandon"),
        pytest.param(lambda tx: tx.rebase("@-", onto="root()"), id="rebase"),
        pytest.param(lambda tx: tx.squash("@", "@-"), id="squash"),
        pytest.param(lambda tx: tx.restore("@-", from_="@--"), id="restore"),
        pytest.param(lambda tx: tx.split("@-", {"c.txt": None}), id="split"),
    ],
)
def test_every_rewrite_verb_refuses_a_configured_immutable_commit(
    linear_repo: Path, jj: JjCli, operation: Rewrite
) -> None:
    _protect_previous_commit(linear_repo, jj)
    ws = pyjutsu.Workspace.load(linear_repo)

    with pytest.raises(ImmutableCommitError, match=r"immutable_heads\(\)"):
        with ws.transaction("blocked rewrite", auto_snapshot=False) as tx:
            operation(tx)
