"""Shared fixtures: a scratch jj repo built by the pinned CLI, plus the CLI driver itself."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.diff.jj_cli import JjCli, write_config

#: Description set on `@` in the scratch repo; tests assert the binding reads it back.
WC_DESCRIPTION = "hello from pyjutsu test"


@pytest.fixture
def jj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JjCli:
    """A `jj` CLI driver bound to an isolated config under this test's tmp dir.

    The config path is also exported as ``JJ_CONFIG`` in *this* process so the in-process binding
    loads the same identity + pinned timestamp the CLI subprocess uses (it reads ``JJ_CONFIG`` from
    the process environment, exactly like the CLI). Without this, a mutation authored by the
    binding would use different settings than the CLI and produce different commit ids.
    """
    config = write_config(tmp_path)
    monkeypatch.setenv("JJ_CONFIG", str(config))
    return JjCli(config)


@pytest.fixture
def scratch_repo(tmp_path: Path, jj: JjCli) -> Path:
    """A fresh colocated jj repo with one described working-copy commit (`@`)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "describe", "-m", WC_DESCRIPTION)
    return repo


@pytest.fixture
def linear_repo(tmp_path: Path, jj: JjCli) -> Path:
    """A colocated repo with a 3-commit linear history under an empty `@`.

    Layout (oldest → newest): ``A`` (file a) → ``B`` (file b) → ``C`` (file c) → ``@`` (empty).
    Each described commit also writes a file so diff-based reads have content to work with.
    """
    repo = tmp_path / "linear"
    repo.mkdir()
    jj.init_colocated(repo)
    for name in ("a", "b", "c"):
        (repo / f"{name}.txt").write_text(f"contents of {name}\n")
        jj(repo, "describe", "-m", f"commit {name.upper()}")
        jj(repo, "new")
    return repo


@pytest.fixture
def bookmarked_repo(tmp_path: Path, jj: JjCli) -> Path:
    """A colocated repo with a local bookmark ``feature`` pushed to an ``origin`` git remote.

    Yields both a local bookmark row and remote-tracking rows (``feature@git``, ``feature@origin``)
    so bookmark reads exercise local + remote + tracking state.
    """
    repo = tmp_path / "bookmarked"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "f.txt").write_text("hello\n")
    jj(repo, "describe", "-m", "base commit")
    jj(repo, "bookmark", "create", "feature", "-r", "@")
    jj(repo, "new", "-m", "work on top")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    jj(repo, "git", "remote", "add", "origin", str(origin))
    jj(repo, "git", "push", "--bookmark", "feature")
    return repo


@pytest.fixture
def diffstat_repo(tmp_path: Path, jj: JjCli) -> Path:
    """A repo whose `@-` modifies one file (+2/-1) and adds another (+2); `@` is empty.

    `a.txt` goes from 3 lines to 4 (one line changed, one appended); `b.txt` is new (2 lines).
    """
    repo = tmp_path / "diffstat"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "a.txt").write_text("l1\nl2\nl3\n")
    jj(repo, "describe", "-m", "base")
    jj(repo, "new")
    (repo / "a.txt").write_text("l1\nCHANGED\nl3\nl4\n")
    (repo / "b.txt").write_text("b1\nb2\n")
    jj(repo, "describe", "-m", "edit")
    jj(repo, "new")
    return repo


@pytest.fixture
def conflict_repo(tmp_path: Path, jj: JjCli) -> Path:
    """A colocated repo whose `@` is a merge of two diverging edits → a conflict in `file.txt`."""
    repo = tmp_path / "conflict"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "file.txt").write_text("base\n")
    jj(repo, "describe", "-m", "base")
    jj(repo, "bookmark", "create", "base", "-r", "@")

    jj(repo, "new", "base", "-m", "side A")
    (repo / "file.txt").write_text("version A\n")
    jj(repo, "bookmark", "create", "sideA", "-r", "@")

    jj(repo, "new", "base", "-m", "side B")
    (repo / "file.txt").write_text("version B\n")
    jj(repo, "bookmark", "create", "sideB", "-r", "@")

    jj(repo, "new", "sideA", "sideB", "-m", "merge")  # `@` now conflicts in file.txt
    return repo
