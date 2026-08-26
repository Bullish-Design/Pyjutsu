"""D7: git submodules (lane `004/d7`), read-only.

Oracle is `git submodule status`, whose lines are ``[ +-U]<index-oid> <path> [(describe)]``:
a leading ``-`` means "not initialized".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu

from tests.diff.jj_cli import JjCli, suite_object_hash


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        # `git submodule add` refuses a local path unless this is set.
        env={**_ENV, "GIT_ALLOW_PROTOCOL": "file"},
    ).stdout


_ENV = {
    "PATH": subprocess.os.environ["PATH"],
    "HOME": subprocess.os.environ.get("HOME", "/tmp"),
    "GIT_AUTHOR_NAME": "Pyjutsu Test",
    "GIT_AUTHOR_EMAIL": "test@pyjutsu.invalid",
    "GIT_COMMITTER_NAME": "Pyjutsu Test",
    "GIT_COMMITTER_EMAIL": "test@pyjutsu.invalid",
}


def _submodule_status(repo: Path) -> dict[str, tuple[str, str]]:
    """path -> (index_oid, status_flag) from `git submodule status`."""
    out = _git(repo, "submodule", "status")
    rows: dict[str, tuple[str, str]] = {}
    for line in out.splitlines():
        if not line:
            continue
        flag = line[0] if line[0] in " +-U" else " "
        rest = line[1:] if line[0] in " +-U" else line
        oid, path = rest.split(" ", 1)
        rows[path.split(" ")[0]] = (oid, flag)
    return rows


def _child_repo(tmp_path: Path) -> Path:
    """A plain git repository to use as a submodule source.

    It must share the superproject's object format: git refuses to add "a submodule of a
    different hash algorithm", the same rule `init_bare_remote` handles for push/fetch remotes.
    """
    child = tmp_path / "child"
    child.mkdir()
    args = ["init", "-q", "--initial-branch=main"]
    object_hash = suite_object_hash()
    if object_hash:
        args.append(f"--object-format={object_hash}")
    _git(child, *args, ".")
    (child / "c.txt").write_text("child\n")
    _git(child, "add", "c.txt")
    _git(child, "commit", "-q", "-m", "child commit")
    return child


def test_no_submodules_reads_as_empty(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()
    jj.init_colocated(repo)
    ws = pyjutsu.Workspace.load(repo)
    assert ws.git.submodules() == []


def test_submodule_matches_git_status(tmp_path: Path, jj: JjCli) -> None:
    child = _child_repo(tmp_path)
    repo = tmp_path / "super"
    repo.mkdir()
    jj.init_colocated(repo)
    _git(repo, "submodule", "add", "-q", str(child), "vendor/child")

    ws = pyjutsu.Workspace.load(repo)
    subs = ws.git.submodules()
    assert len(subs) == 1
    sub = subs[0]
    assert sub.name == "vendor/child"
    assert sub.path == "vendor/child"
    assert sub.url == str(child)
    assert sub.active is True

    status = _submodule_status(repo)
    assert sub.index_oid == status["vendor/child"][0]
    # An added submodule is checked out, so HEAD and the recorded index oid agree.
    assert sub.head_oid == sub.index_oid


def test_submodules_are_sorted_by_name(tmp_path: Path, jj: JjCli) -> None:
    child = _child_repo(tmp_path)
    repo = tmp_path / "many"
    repo.mkdir()
    jj.init_colocated(repo)
    for name in ("zeta", "alpha", "mid"):
        _git(repo, "submodule", "add", "-q", str(child), f"vendor/{name}")

    ws = pyjutsu.Workspace.load(repo)
    names = [s.name for s in ws.git.submodules()]
    assert names == sorted(names)
    assert names == ["vendor/alpha", "vendor/mid", "vendor/zeta"]


def test_an_uninitialized_submodule_has_no_head(tmp_path: Path, jj: JjCli) -> None:
    """git prints a leading `-` for a submodule with no checkout; `head_oid` is None."""
    child = _child_repo(tmp_path)
    repo = tmp_path / "deinit"
    repo.mkdir()
    jj.init_colocated(repo)
    _git(repo, "submodule", "add", "-q", str(child), "vendor/child")
    _git(repo, "commit", "-q", "-m", "add submodule")
    _git(repo, "submodule", "deinit", "-f", "vendor/child")

    ws = pyjutsu.Workspace.load(repo)
    sub = ws.git.submodules()[0]
    assert _submodule_status(repo)["vendor/child"][1] == "-"
    assert sub.head_oid is None
    assert sub.index_oid == _submodule_status(repo)["vendor/child"][0]


def test_git_submodule_status_is_reconstructible(tmp_path: Path, jj: JjCli) -> None:
    """`head_oid` + `index_oid` carry everything `git submodule status` prints."""
    child = _child_repo(tmp_path)
    repo = tmp_path / "moved"
    repo.mkdir()
    jj.init_colocated(repo)
    _git(repo, "submodule", "add", "-q", str(child), "vendor/child")
    _git(repo, "commit", "-q", "-m", "add submodule")
    # Move the submodule's checkout ahead of what the superproject records.
    (child / "d.txt").write_text("more\n")
    _git(child, "add", "d.txt")
    _git(child, "commit", "-q", "-m", "child second")
    _git(repo / "vendor" / "child", "fetch", "-q", "origin")
    _git(repo / "vendor" / "child", "checkout", "-q", "origin/main")

    ws = pyjutsu.Workspace.load(repo)
    sub = ws.git.submodules()[0]
    flag = "-" if sub.head_oid is None else ("+" if sub.head_oid != sub.index_oid else " ")
    oid = sub.index_oid if sub.head_oid is None else sub.head_oid

    status_oid, status_flag = _submodule_status(repo)["vendor/child"]
    assert (oid, flag) == (status_oid, status_flag)
    assert flag == "+"  # the checkout moved


def test_listing_publishes_no_operation(tmp_path: Path, jj: JjCli) -> None:
    child = _child_repo(tmp_path)
    repo = tmp_path / "readonly"
    repo.mkdir()
    jj.init_colocated(repo)
    _git(repo, "submodule", "add", "-q", str(child), "vendor/child")

    ws = pyjutsu.Workspace.load(repo)
    before = ws.head_operation()
    ws.git.submodules()
    assert ws.head_operation() == before
