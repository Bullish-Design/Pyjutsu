"""Force-write / delete colocated head refs (project 14 §P4): ``write_git_ref`` / ``delete_git_ref``.

Reconcile-only escape hatch to heal colocated-ref drift. Oracle is raw git (like ``test_tags.py``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu

from tests.diff.jj_cli import JjCli


def _git(d: Path, *a: str) -> str:
    return subprocess.run(
        ["git", "-C", str(d), *a], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_write_and_delete_head_ref(bookmarked_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    tip = jj.commit_id(bookmarked_repo, "@")
    ws.write_git_ref("healed", tip)
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/healed") == tip

    # Force-move to a different target (no fast-forward check).
    parent = jj.commit_id(bookmarked_repo, "@-")
    ws.write_git_ref("healed", parent)
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/healed") == parent

    ws.delete_git_ref("healed")
    assert (
        subprocess.run(
            ["git", "-C", str(bookmarked_repo), "rev-parse", "refs/heads/healed"],
            capture_output=True,
        ).returncode
        != 0
    )

    ws.delete_git_ref("healed")  # idempotent: deleting an absent ref is a no-op, not an error
