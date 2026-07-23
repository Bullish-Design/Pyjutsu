"""Read colocated ``refs/heads/*`` (project 14 §P2): ``Workspace.git_refs``.

Reads the on-disk git refs directly, which may drift from jj's last-imported ``@git`` — seeing that
drift is the point, so ``bookmarks()`` is not a substitute.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu

from tests.diff.jj_cli import JjCli


def test_git_refs_sees_on_disk_ref_while_bookmarks_stale(bookmarked_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    refs = ws.git_refs()  # default refs/heads/
    assert "feature" in refs
    assert refs["feature"] == jj.commit_id(bookmarked_repo, "feature")

    # Write a head out-of-band, then assert git_refs sees it but jj's bookmarks() do not (yet).
    tip = jj.commit_id(bookmarked_repo, "@")
    subprocess.run(
        ["git", "-C", str(bookmarked_repo), "update-ref", "refs/heads/stray", tip],
        check=True,
        capture_output=True,
    )
    refs2 = ws.git_refs()
    assert refs2.get("stray") == tip
    assert "stray" not in {b.name for b in ws.head().bookmarks() if b.remote is None}
