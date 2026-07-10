"""Project 13 / P3: `Workspace.sync_colocated` — reset colocated git HEAD **and** index.

jj-lib 0.42's `git::reset_head` (which `git_export` already calls) resets the colocated git HEAD
*and* rebuilds the git index from `@`'s parent tree. This surfaces that repair as a standalone,
idempotent verb so callers can run it without needing a refs change to trigger `git_export`. The
regression it locks in is gitman field report 15-RC6: after a trunk move dropped a tracked file, the
git index still listed it, so raw `git check-ignore` misreported the (now machine-local) file as
*not* ignored until a manual `git rm --cached`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu

from tests.diff.jj_cli import JjCli


def _index_files(repo: Path) -> set[str]:
    """The paths recorded in the colocated git index (`git ls-files`)."""
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return set(out.split())


def _is_ignored(repo: Path, path: str) -> bool:
    """Whether raw git reports ``path`` as ignored. With the index consulted (the default), a
    *tracked* path is reported NOT ignored — which is exactly the RC6 lie a stale index produces."""
    return (
        subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", path], capture_output=True
        ).returncode
        == 0
    )


def _tree_files(jj: JjCli, repo: Path, rev: str) -> set[str]:
    # `--ignore-working-copy`: read the recorded tree without triggering an auto-snapshot that would
    # perturb `@` (and the colocated index) mid-assertion.
    return set(jj(repo, "--ignore-working-copy", "file", "list", "-r", rev).split())


def test_sync_colocated_resets_index_after_trunk_dropped_file(scratch_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)

    # Track a machine-local file alongside a kept one, then gitignore + untrack it and advance `@`
    # past the untracking commit — so `@`'s parent (which HEAD/index must mirror) no longer holds it,
    # while the file itself stays on disk. This reproduces the post-`land` shape of 15-RC6.
    (scratch_repo / "keep.txt").write_text("keep\n")
    (scratch_repo / "machine.txt").write_text("local\n")
    ws.snapshot()
    (scratch_repo / ".gitignore").write_text("machine.txt\n")
    ws.snapshot()
    ws.untrack_paths(["machine.txt"])  # @ drops machine.txt; file stays on disk
    with ws.transaction("advance") as tx:
        tx.new()  # @'s parent is now the untracking commit (no machine.txt)

    op = ws.sync_colocated()

    # HEAD moved (init HEAD was elsewhere), so an operation was published and `@` itself is untouched.
    assert op is not None
    assert "sync" in op.description.lower()

    # The git index no longer tracks the dropped file, so `check-ignore` tells the truth and a
    # raw-git tool is no longer lied to — with no `git rm --cached`.
    assert "machine.txt" not in _index_files(scratch_repo)
    assert _is_ignored(scratch_repo, "machine.txt")
    assert (scratch_repo / "machine.txt").exists()  # still on disk (machine-local)

    # The index tree equals `@`'s parent tree (what HEAD points at).
    assert _index_files(scratch_repo) == _tree_files(jj, scratch_repo, "@-")


def test_sync_colocated_is_idempotent(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("new") as tx:
        tx.new()  # move `@`, leaving colocated HEAD stale

    ws.sync_colocated()  # first call repairs HEAD/index
    # Already in sync ⇒ no view change ⇒ no operation published.
    assert ws.sync_colocated() is None


def test_sync_colocated_clean_repo_no_error(scratch_repo: Path) -> None:
    # Safe to call on an untouched colocated repo (a no-op or a single HEAD repair, never an error).
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.sync_colocated()
    assert ws.sync_colocated() is None
