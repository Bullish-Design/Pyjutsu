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


def _absent(d: Path, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(d), "rev-parse", ref], capture_output=True
        ).returncode
        != 0
    )


def test_write_fractal_ref_flat_then_nested(bookmarked_repo: Path, jj: JjCli) -> None:
    """D/F-safe write: a loose `refs/heads/T` must not block writing `refs/heads/T/api`."""
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    tip = jj.commit_id(bookmarked_repo, "@")
    parent = jj.commit_id(bookmarked_repo, "@-")

    ws.write_git_ref("T", tip)
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/T") == tip

    # `refs/heads/T` is a file — writing the nested `refs/heads/T/api` requires it to become a
    # directory. `git update-ref` packs the conflicting ref; so must we.
    ws.write_git_ref("T/api", parent)
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/T") == tip
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/T/api") == parent


def test_write_fractal_ref_nested_then_flat(bookmarked_repo: Path, jj: JjCli) -> None:
    """Reverse order: a `refs/heads/T/` directory must not block writing `refs/heads/T`."""
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    tip = jj.commit_id(bookmarked_repo, "@")
    parent = jj.commit_id(bookmarked_repo, "@-")

    ws.write_git_ref("T/api", parent)
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/T/api") == parent

    ws.write_git_ref("T", tip)
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/T/api") == parent
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/T") == tip


def test_delete_fractal_refs(bookmarked_repo: Path, jj: JjCli) -> None:
    """Both flat and nested refs delete cleanly and idempotently under a D/F pair."""
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    tip = jj.commit_id(bookmarked_repo, "@")
    parent = jj.commit_id(bookmarked_repo, "@-")

    ws.write_git_ref("T", tip)
    ws.write_git_ref("T/api", parent)

    ws.delete_git_ref("T/api")
    assert _absent(bookmarked_repo, "refs/heads/T/api")
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/T") == tip

    ws.delete_git_ref("T")
    assert _absent(bookmarked_repo, "refs/heads/T")

    # Idempotent on both.
    ws.delete_git_ref("T")
    ws.delete_git_ref("T/api")
