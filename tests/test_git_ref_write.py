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


def _gitdir(d: Path) -> Path:
    gd = Path(_git(d, "rev-parse", "--git-dir"))
    return gd if gd.is_absolute() else d / gd


def _write_loose(gitdir: Path, name: str, oid: str) -> None:
    """Force a genuinely-*loose* `refs/heads/<name>` file on disk (bypassing git, which refuses
    to create D/F-conflicting loose refs) — the layout a `jj git export` leaves behind."""
    p = gitdir / "refs" / "heads" / Path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(oid + "\n")


def _ref_oid(d: Path, ref: str) -> str:
    """Robust exact ref-oid oracle: `for-each-ref` resolves packed refs even when a D/F-conflicting
    loose file shadows them (raw `git rev-parse` reports such a ref as 'ambiguous'). Matches the ref
    exactly (a bare `for-each-ref <ref>` is a prefix pattern, so it would also match descendants).
    Returns ``""`` if the ref is absent."""
    out = _git(d, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/")
    for line in out.splitlines():
        name, _, oid = line.partition(" ")
        if name == ref:
            return oid
    return ""


def _write_packed(gitdir: Path, entries: list[tuple[str, str]]) -> None:
    body = "# pack-refs with: peeled fully-peeled sorted \n"
    body += "".join(f"{oid} refs/heads/{name}\n" for name, oid in entries)
    (gitdir / "packed-refs").write_text(body)


def test_write_three_level_loose_ancestor_file(bookmarked_repo: Path, jj: JjCli) -> None:
    """The real gitman case (`test_reconcile_refreshes_stale_grandchild_workspace`): a *loose* file
    `refs/heads/T` blocks writing `refs/heads/T/api`. Unlike the two-level test above, `T` here is
    loose from an outside export (not written via `write_git_ref`, which would have packed it), so
    it stays loose and trips gix's D/F-blind existing-ref probe until we pack it."""
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    tip = jj.commit_id(bookmarked_repo, "@")
    parent = jj.commit_id(bookmarked_repo, "@-")
    gitdir = _gitdir(bookmarked_repo)

    _write_loose(gitdir, "T", tip)
    assert (gitdir / "refs/heads/T").is_file()  # genuinely loose

    ws.write_git_ref("T/api", parent)
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/T/api") == parent
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/T") == tip


def test_write_three_level_loose_descendant_dir(bookmarked_repo: Path, jj: JjCli) -> None:
    """Reverse D/F: a loose `refs/heads/T/api/handler` makes `refs/heads/T/` a directory, blocking
    a loose write of `refs/heads/T`. Packing every head resolves it."""
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    tip = jj.commit_id(bookmarked_repo, "@")
    parent = jj.commit_id(bookmarked_repo, "@-")
    gitdir = _gitdir(bookmarked_repo)

    _write_loose(gitdir, "T/api/handler", tip)
    assert (gitdir / "refs/heads/T/api").is_dir()  # T/ is a directory

    ws.write_git_ref("T", parent)
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/T") == parent
    assert _git(bookmarked_repo, "rev-parse", "refs/heads/T/api/handler") == tip


def test_write_three_level_mixed_loose_and_packed(bookmarked_repo: Path, jj: JjCli) -> None:
    """The full bidirectional three-level D/F: a loose *file* `refs/heads/T` blocks from below while
    `refs/heads/T/api` + `refs/heads/T/api/handler` live (packed) under it. Updating `T/api` must
    still land, leaving all three names resolvable."""
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    tip = jj.commit_id(bookmarked_repo, "@")
    parent = jj.commit_id(bookmarked_repo, "@-")
    gitdir = _gitdir(bookmarked_repo)

    _write_packed(gitdir, [("T/api", tip), ("T/api/handler", tip)])
    _write_loose(gitdir, "T", tip)  # loose file coexisting with packed children
    assert _ref_oid(bookmarked_repo, "refs/heads/T/api/handler") == tip

    ws.write_git_ref("T/api", parent)  # bidirectional D/F
    assert _ref_oid(bookmarked_repo, "refs/heads/T/api") == parent
    assert _ref_oid(bookmarked_repo, "refs/heads/T") == tip
    assert _ref_oid(bookmarked_repo, "refs/heads/T/api/handler") == tip


def test_delete_three_level_refs_idempotent(bookmarked_repo: Path, jj: JjCli) -> None:
    """`delete_git_ref` clears each of a three-level fractal set (loose + packed) and is idempotent.
    Deletion has no D/F *write* conflict, so it needed no change — this locks that in for 3 levels."""
    ws = pyjutsu.Workspace.load(bookmarked_repo)
    tip = jj.commit_id(bookmarked_repo, "@")
    gitdir = _gitdir(bookmarked_repo)

    _write_packed(gitdir, [("T/api", tip), ("T/api/handler", tip)])
    _write_loose(gitdir, "T", tip)

    ws.delete_git_ref("T/api/handler")
    assert _ref_oid(bookmarked_repo, "refs/heads/T/api/handler") == ""
    ws.delete_git_ref("T/api")
    assert _ref_oid(bookmarked_repo, "refs/heads/T/api") == ""
    ws.delete_git_ref("T")
    assert _ref_oid(bookmarked_repo, "refs/heads/T") == ""

    # Idempotent on all three (absent ⇒ no-op).
    ws.delete_git_ref("T")
    ws.delete_git_ref("T/api")
    ws.delete_git_ref("T/api/handler")


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
