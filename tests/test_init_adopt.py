"""`Workspace.init(colocate=True)` adopting an existing `.git`, absolute workspace paths, and
`git_export` keeping colocated git's `HEAD` in sync.

These cover the gitman bootstrap gaps: `init_colocated_git` could only *create* a fresh `.git`
(failing on an existing repo), `workspaces()` could leak a relative path, and `git_export` left
`.git/HEAD` parked at `refs/jj/root` so bare `git` was broken. See the gitman bootstrap-issues note.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _git_rev(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref).strip()


def _make_git_repo(path: Path, *, with_commit: bool) -> None:
    """A plain (non-jj) colocated-able git repo on branch `main`."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    if with_commit:
        (path / "README.md").write_text("hello\n")
        _git(path, "add", "README.md")
        _git(path, "commit", "-m", "Initial commit")


# --- adopt an existing `.git` (A1) ------------------------------------------------------------


def test_init_adopts_existing_git_with_commit(tmp_path: Path, jj) -> None:
    repo = tmp_path / "existing"
    _make_git_repo(repo, with_commit=True)
    head = _git_rev(repo, "HEAD")
    # Uncommitted work present at adoption time — must survive.
    (repo / "work.txt").write_text("in progress\n")

    ws = pyjutsu.Workspace.init(repo, colocate=True)
    view = ws.head()

    # The existing branch is imported as a jj local bookmark at the initial commit...
    assert "main" in {b.name for b in view.bookmarks() if b.remote is None}
    assert view.resolve("main").commit_id == head
    # ...and `@` is an empty child of that commit (not a divergent change on root).
    wc = view.working_copy()
    assert wc.parent_ids == [head]

    # The uncommitted file is untouched on disk and is captured into `@` on the next snapshot.
    assert (repo / "work.txt").read_text() == "in progress\n"
    assert (repo / "README.md").read_text() == "hello\n"
    ws.snapshot()
    wc2 = ws.head().working_copy()
    assert wc2.is_empty is False
    assert wc2.parent_ids == [head]


def test_init_adopts_empty_git_repo(tmp_path: Path, jj) -> None:
    # A repo with no commits has no HEAD: adoption imports nothing and leaves the empty `@` on root.
    repo = tmp_path / "empty"
    _make_git_repo(repo, with_commit=False)

    ws = pyjutsu.Workspace.init(repo, colocate=True)
    view = ws.head()
    assert view.working_copy().is_empty is True
    assert [b for b in view.bookmarks() if b.remote is None] == []
    assert (repo / ".jj").is_dir() and (repo / ".git").is_dir()


def test_init_fresh_colocate_unchanged(tmp_path: Path, jj) -> None:
    # Regression: with no pre-existing `.git`, colocate init still creates a fresh repo with an
    # empty `@` on root and no bookmarks.
    repo = tmp_path / "fresh"
    repo.mkdir()

    ws = pyjutsu.Workspace.init(repo, colocate=True)
    view = ws.head()
    assert view.working_copy().is_empty is True
    assert [b for b in view.bookmarks() if b.remote is None] == []
    assert (repo / ".jj").is_dir() and (repo / ".git").is_dir()


# --- absolute workspace paths (A2) ------------------------------------------------------------


def test_workspaces_path_is_absolute(tmp_path: Path, jj) -> None:
    repo = tmp_path / "abs"
    repo.mkdir()
    ws = pyjutsu.Workspace.init(repo, colocate=True)

    rows = {w.name: w for w in ws.workspaces()}
    default = Path(rows["default"].path)
    assert default.is_absolute()
    assert default == repo.resolve()


# --- git_export syncs HEAD (A3) ---------------------------------------------------------------


def test_git_export_syncs_head(linear_repo: Path, jj) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)

    # Advance `@` onto a fresh commit D (bookmarked) so export has work and a real parent to sync.
    (linear_repo / "d.txt").write_text("d\n")
    with ws.transaction("describe") as tx:
        tx.describe("@", "commit D")
        tx.create_bookmark("main", "@")
    with ws.transaction("new") as tx:
        tx.new("@")
    new_parent = ws.head().working_copy().parent_ids[0]

    op = ws.git_export()
    assert op is not None

    # Colocated `.git/HEAD` now tracks `@`'s parent (detached), so bare git is usable again.
    assert _git_rev(linear_repo, "HEAD") == new_parent
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=linear_repo, capture_output=True, text=True
    )
    assert log.returncode == 0 and log.stdout.strip()
