"""Read colocated ``refs/heads/*`` (project 14 §P2, lane D1): ``ws.git.refs``.

Reads the on-disk git refs directly, which may drift from jj's last-imported ``@git`` — seeing that
drift is the point, so ``bookmarks()`` is not a substitute. The pre-D1 ``Workspace.git_refs`` name
survives as a deprecating alias.
"""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

import pyjutsu

from tests.diff.jj_cli import JjCli


def test_git_refs_sees_on_disk_ref_while_bookmarks_stale(bookmarked_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    refs = ws.git.refs()  # default refs/heads/
    assert "feature" in refs
    assert refs["feature"] == jj.commit_id(bookmarked_repo, "feature")

    # Write a head out-of-band, then assert git.refs sees it but jj's bookmarks() do not (yet).
    tip = jj.commit_id(bookmarked_repo, "@")
    subprocess.run(
        ["git", "-C", str(bookmarked_repo), "update-ref", "refs/heads/stray", tip],
        check=True,
        capture_output=True,
    )
    refs2 = ws.git.refs()
    assert refs2.get("stray") == tip
    assert "stray" not in {b.name for b in ws.head().bookmarks() if b.remote is None}


def test_git_refs_alias_deprecated(bookmarked_repo: Path, jj: JjCli) -> None:
    """The pre-D1 `Workspace.git_refs` name still works and warns."""
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        refs = ws.git_refs()
    assert refs["feature"] == jj.commit_id(bookmarked_repo, "feature")
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
