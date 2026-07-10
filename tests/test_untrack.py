"""Project 13 / P2: `Workspace.untrack_paths` — stop tracking a path, leave it on disk.

Mirrors `jj file untrack`: the path is removed from `@`'s tree and its working-copy file-state is
dropped, but the file stays on disk. Untracking is durable only when the path is also excluded from
the next snapshot — the intended path is a `.gitignore`, since jj-lib evaluates gitignore before the
`snapshot.auto-track` fileset, so an ignored, now-untracked path is not re-added. This retires
gitman's `rm → save → restore-on-disk → land` dance for machine-local files (e.g.
`.claude/settings.local.json`).
"""

from __future__ import annotations

from pathlib import Path

import pyjutsu

from tests.diff.jj_cli import JjCli


def _files(jj: JjCli, repo: Path, rev: str) -> set[str]:
    """The set of tracked file paths in ``rev``'s tree (the CLI oracle).

    Uses ``--ignore-working-copy`` so the read does NOT auto-snapshot: a plain ``jj file list``
    would re-snapshot ``@`` and re-add a just-untracked (but on-disk, non-ignored) file, hiding the
    very effect under test.
    """
    return set(jj(repo, "--ignore-working-copy", "file", "list", "-r", rev).split())


def test_untrack_removes_from_tree_leaves_file_on_disk(scratch_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    (scratch_repo / "machine.txt").write_text("local\n")
    ws.snapshot()  # machine.txt is now tracked in `@`
    assert "machine.txt" in _files(jj, scratch_repo, "@")
    ops_before = len(ws.operations())

    op = ws.untrack_paths(["machine.txt"])

    assert op is not None
    assert "untrack" in op.description.lower()
    assert len(ws.operations()) == ops_before + 1  # exactly one operation
    assert "machine.txt" not in _files(jj, scratch_repo, "@")  # gone from the tree
    assert (scratch_repo / "machine.txt").read_text() == "local\n"  # still on disk


def test_untrack_ignored_path_not_readded_by_snapshot(scratch_repo: Path, jj: JjCli) -> None:
    # The headline gitman symptom: an already-committed, now-gitignored file must stay out of the
    # tree across subsequent snapshots once untracked.
    ws = pyjutsu.Workspace.load(scratch_repo)
    (scratch_repo / "machine.txt").write_text("local\n")
    ws.snapshot()  # machine.txt tracked (it predates the ignore rule)
    assert "machine.txt" in _files(jj, scratch_repo, "@")

    (scratch_repo / ".gitignore").write_text("machine.txt\n")
    ws.snapshot()  # .gitignore tracked; machine.txt stays tracked (ignore doesn't drop tracked files)
    assert "machine.txt" in _files(jj, scratch_repo, "@")

    ws.untrack_paths(["machine.txt"])
    assert "machine.txt" not in _files(jj, scratch_repo, "@")

    # The next snapshot must NOT re-add the now-untracked, ignored file — the whole point.
    assert ws.snapshot() is None
    assert "machine.txt" not in _files(jj, scratch_repo, "@")
    assert (scratch_repo / "machine.txt").exists()


def test_untrack_untracked_path_returns_none(scratch_repo: Path) -> None:
    # Nothing tracked at the path ⇒ no operation published.
    ws = pyjutsu.Workspace.load(scratch_repo)
    ops_before = len(ws.operations())

    assert ws.untrack_paths(["never-existed.txt"]) is None
    assert len(ws.operations()) == ops_before


def test_untrack_empty_list_returns_none(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    assert ws.untrack_paths([]) is None


def test_untrack_directory_prefix_untracks_subtree(scratch_repo: Path, jj: JjCli) -> None:
    # A directory argument untracks the whole subtree (a `PrefixMatcher`), matching `jj file
    # untrack <dir>`.
    ws = pyjutsu.Workspace.load(scratch_repo)
    (scratch_repo / "sub").mkdir()
    (scratch_repo / "sub" / "a.txt").write_text("a\n")
    (scratch_repo / "sub" / "b.txt").write_text("b\n")
    (scratch_repo / "top.txt").write_text("top\n")
    ws.snapshot()
    assert {"sub/a.txt", "sub/b.txt", "top.txt"} <= _files(jj, scratch_repo, "@")

    op = ws.untrack_paths(["sub"])

    assert op is not None
    files = _files(jj, scratch_repo, "@")
    assert "sub/a.txt" not in files
    assert "sub/b.txt" not in files
    assert "top.txt" in files  # a sibling outside the prefix is untouched
    assert (scratch_repo / "sub" / "a.txt").exists()  # files remain on disk
    assert (scratch_repo / "sub" / "b.txt").exists()


def test_untrack_invalid_path_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    # An absolute path is not a valid repo-relative path.
    try:
        ws.untrack_paths(["/etc/passwd"])
    except pyjutsu.PyjutsuError:
        return
    raise AssertionError("expected a PyjutsuError for an invalid repo-relative path")
