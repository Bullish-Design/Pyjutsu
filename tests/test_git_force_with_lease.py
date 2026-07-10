"""Project 13 / P1: force-with-lease is the push contract (a probe, not a new API).

jj-lib 0.42 has no fast-forward guard: `git::push_updates` always force-pushes with
`--force-with-lease`, the lease being each bookmark's remote-tracking target (the `before` of the
`Diff` `git_push` builds). So a *non-fast-forward* bookmark move — pushing a content-equal but
hash-divergent trunk over `origin/<trunk>` — succeeds when the remote-tracking ref is current, and
is rejected (never a blind clobber) when the remote moved out-of-band since the last fetch. These
probes lock that in; there is deliberately no `force=`/`force_with_lease=` flag. This retires
gitman's raw `git push --force-with-lease` migration escape (`push --reset-origin`, field
reports 13/15).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import GitError

from tests.diff.jj_cli import JjCli


def _init_bare(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    return path


def _ref_sha(git_dir: Path, ref: str) -> str:
    """The commit SHA a ref points at in the (bare) git repo (colocated jj commit ids are git SHAs)."""
    out = subprocess.run(
        ["git", "-C", str(git_dir), "show-ref", "--verify", ref],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()[0]


def _setup_pushed_trunk(scratch_repo: Path, origin: Path) -> tuple[pyjutsu.Workspace, str]:
    """A repo with a described `trunk` bookmark pushed to `origin`; returns (ws, pushed SHA)."""
    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("new") as tx:
        tx.new()
    with ws.transaction("bookmark") as tx:
        tx.create_bookmark("trunk", "@-")
    ws.add_remote("origin", str(origin))
    ws.git_push("origin", "trunk", allow_new=True)
    return ws, _ref_sha(origin, "refs/heads/trunk")


def test_nonff_push_succeeds_when_lease_is_current(
    scratch_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws, sha1 = _setup_pushed_trunk(scratch_repo, origin)
    assert jj.commit_id(scratch_repo, "trunk") == sha1

    # Reword `trunk` in place: same tree (content-equal), new commit id (hash-divergent) — a sibling
    # of `sha1`, not a descendant, so pushing it over `origin/trunk` is a genuine non-fast-forward.
    with ws.transaction("reword") as tx:
        tx.describe("trunk", "reworded trunk")
    sha2 = jj.commit_id(scratch_repo, "trunk")
    assert sha2 != sha1

    # No force flag anywhere — the lease (remote-tracking `trunk@origin` == sha1) still matches the
    # remote, so jj-lib's always-force-with-lease push advances origin to the divergent local SHA.
    op = ws.git_push("origin", "trunk")

    assert op is not None
    assert _ref_sha(origin, "refs/heads/trunk") == sha2


def test_stale_lease_push_is_rejected_not_clobbered(
    scratch_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws, sha1 = _setup_pushed_trunk(scratch_repo, origin)

    # A first non-FF push succeeds and updates the remote-tracking lease to sha2.
    with ws.transaction("reword") as tx:
        tx.describe("trunk", "reworded trunk")
    sha2 = jj.commit_id(scratch_repo, "trunk")
    ws.git_push("origin", "trunk")
    assert _ref_sha(origin, "refs/heads/trunk") == sha2

    # Move origin out-of-band (back to sha1, an object it already holds) — the local view still
    # believes origin is at sha2, so its lease is now stale.
    subprocess.run(
        ["git", "-C", str(origin), "update-ref", "refs/heads/trunk", sha1],
        check=True,
        capture_output=True,
    )

    # Another reword, then push with the stale lease → rejected (lease sha2 != actual sha1).
    with ws.transaction("reword-again") as tx:
        tx.describe("trunk", "reworded again")
    with pytest.raises(GitError):
        ws.git_push("origin", "trunk")

    # The push was refused, not forced: origin was not clobbered — still at sha1.
    assert _ref_sha(origin, "refs/heads/trunk") == sha1
