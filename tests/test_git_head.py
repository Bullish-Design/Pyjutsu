"""D4: git HEAD state (lane `004/d4`).

Oracle is the `git` binary: `git symbolic-ref HEAD` and `git rev-parse HEAD`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest

from tests.diff.jj_cli import JjCli


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _symbolic_ref(repo: Path) -> str | None:
    result = _git(repo, "symbolic-ref", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else None


def _rev_parse(repo: Path, rev: str = "HEAD") -> str | None:
    result = _git(repo, "rev-parse", rev)
    return result.stdout.strip() if result.returncode == 0 else None


def test_head_matches_git_after_a_commit(tmp_path: Path, jj: JjCli) -> None:
    """jj keeps a colocated HEAD detached at `@`'s parent; the read reports exactly that."""
    repo = tmp_path / "head"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "a.txt").write_text("alpha\n")
    jj(repo, "describe", "-m", "commit A")
    jj(repo, "new")

    ws = pyjutsu.Workspace.load(repo)
    head = ws.git.head()
    assert head.detached is True
    assert head.name is None
    assert head.oid == _rev_parse(repo)
    assert _symbolic_ref(repo) is None


def test_head_of_a_fresh_repo_is_unborn(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "unborn"
    repo.mkdir()
    jj.init_colocated(repo)

    ws = pyjutsu.Workspace.load(repo)
    head = ws.git.head()
    assert head.detached is False
    assert head.oid is None
    # git agrees: HEAD is symbolic, and resolving it fails.
    assert head.name == _symbolic_ref(repo)
    assert _rev_parse(repo) is None


def test_init_with_trunk_points_head_at_that_branch(tmp_path: Path, jj: JjCli) -> None:
    """`init(colocate=True, trunk=...)` sets HEAD through gix now, not a raw file write."""
    repo = tmp_path / "trunked"
    repo.mkdir()
    ws = pyjutsu.Workspace.init(repo, colocate=True, trunk="mainline")

    assert _symbolic_ref(repo) == "refs/heads/mainline"
    head = ws.git.head()
    assert head.name == "refs/heads/mainline"
    assert head.detached is False
    assert head.oid is None  # the branch has no commit yet


def test_set_head_matches_git_symbolic_ref(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "sethead"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "a.txt").write_text("alpha\n")
    jj(repo, "describe", "-m", "commit A")
    jj(repo, "bookmark", "create", "feature", "-r", "@")
    jj(repo, "git", "export")

    ws = pyjutsu.Workspace.load(repo)
    ws.git.set_head("feature")

    assert _symbolic_ref(repo) == "refs/heads/feature"
    head = ws.git.head()
    assert head.name == "refs/heads/feature"
    assert head.detached is False
    assert head.oid == _rev_parse(repo, "refs/heads/feature")


def test_set_head_accepts_a_full_ref_name(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "fullref"
    repo.mkdir()
    jj.init_colocated(repo)

    ws = pyjutsu.Workspace.load(repo)
    ws.git.set_head("refs/heads/explicit")
    assert _symbolic_ref(repo) == "refs/heads/explicit"


def test_set_head_allows_an_absent_branch(tmp_path: Path, jj: JjCli) -> None:
    """Pointing at a branch with no commit is how git models an unborn branch."""
    repo = tmp_path / "absent"
    repo.mkdir()
    jj.init_colocated(repo)

    ws = pyjutsu.Workspace.load(repo)
    ws.git.set_head("not-created-yet")

    assert _symbolic_ref(repo) == "refs/heads/not-created-yet"
    head = ws.git.head()
    assert head.name == "refs/heads/not-created-yet"
    assert head.oid is None
    assert head.detached is False


def test_set_head_rejects_an_invalid_ref_name(tmp_path: Path, jj: JjCli) -> None:
    """gix validates the name — this replaced the old hand-rolled newline check."""
    repo = tmp_path / "invalid"
    repo.mkdir()
    jj.init_colocated(repo)
    ws = pyjutsu.Workspace.load(repo)

    for bad in ("has space", "trailing\n", "dot..dot", ""):
        with pytest.raises(pyjutsu.PyjutsuError):
            ws.git.set_head(bad)


def test_set_head_publishes_no_operation(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "noop"
    repo.mkdir()
    jj.init_colocated(repo)
    ws = pyjutsu.Workspace.load(repo)

    before = ws.head_operation()
    ws.git.set_head("some-branch")
    ws.git.head()
    assert ws.head_operation() == before


def test_a_jj_mutation_detaches_head_again(tmp_path: Path, jj: JjCli) -> None:
    """jj's own verbs move HEAD back; the read shows that, rather than hiding it."""
    repo = tmp_path / "resync"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "a.txt").write_text("alpha\n")
    jj(repo, "describe", "-m", "commit A")
    jj(repo, "new")

    ws = pyjutsu.Workspace.load(repo)
    ws.git.set_head("main")
    assert ws.git.head().detached is False

    # jj re-detaches the colocated HEAD at `@`'s parent on its next colocated sync.
    jj(repo, "new")
    assert ws.git.head().detached is True
    assert _symbolic_ref(repo) is None
