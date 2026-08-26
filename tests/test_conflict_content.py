"""C1: conflict content, sides, and resolution (lane `003/c1`).

Oracle is the pinned `jj` CLI: `conflict_content` must match `jj file show`
byte-for-byte in each marker style, and a resolved conflict must leave
`jj resolve --list` empty.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import ConflictError, RevsetError

from tests.diff.jj_cli import JjCli


def test_conflict_content_matches_jj_file_show_diff_style(conflict_repo: Path, jj: JjCli) -> None:
    view = pyjutsu.Workspace.load(conflict_repo).head()
    ours = view.conflict_content("file.txt", "@", style="diff")
    cli = jj(conflict_repo, "file", "show", "-r", "@", "file.txt")
    assert ours == cli


def test_conflict_content_matches_jj_file_show_git_style(conflict_repo: Path, jj: JjCli) -> None:
    view = pyjutsu.Workspace.load(conflict_repo).head()
    ours = view.conflict_content("file.txt", "@", style="git")
    cli = jj(
        conflict_repo,
        "file",
        "show",
        "-r",
        "@",
        "--config",
        "ui.conflict-marker-style=git",
        "file.txt",
    )
    assert ours == cli


def test_conflict_content_matches_jj_file_show_snapshot_style(
    conflict_repo: Path, jj: JjCli
) -> None:
    view = pyjutsu.Workspace.load(conflict_repo).head()
    ours = view.conflict_content("file.txt", "@", style="snapshot")
    cli = jj(
        conflict_repo,
        "file",
        "show",
        "-r",
        "@",
        "--config",
        "ui.conflict-marker-style=snapshot",
        "file.txt",
    )
    assert ours == cli


def test_conflict_content_plain_file_is_raw_content(linear_repo: Path, jj: JjCli) -> None:
    view = pyjutsu.Workspace.load(linear_repo).head()
    # `@-` is commit C whose tree holds b.txt ("contents of b\n").
    assert view.conflict_content("b.txt", "@-") == "contents of b\n"
    assert view.conflict_content("b.txt", "@-") == jj(
        linear_repo, "file", "show", "-r", "@-", "b.txt"
    )


def test_conflict_content_absent_path_raises(conflict_repo: Path) -> None:
    view = pyjutsu.Workspace.load(conflict_repo).head()
    with pytest.raises(ConflictError):
        view.conflict_content("ghost.txt", "@")


def test_conflict_content_bad_style_raises(conflict_repo: Path) -> None:
    view = pyjutsu.Workspace.load(conflict_repo).head()
    with pytest.raises(ValueError):
        view.conflict_content("file.txt", "@", style="fancy")


def test_conflict_content_requires_single_revision(conflict_repo: Path) -> None:
    view = pyjutsu.Workspace.load(conflict_repo).head()
    with pytest.raises(RevsetError):
        view.conflict_content("file.txt", "all()")


def test_conflict_sides_are_parsed_terms(conflict_repo: Path) -> None:
    view = pyjutsu.Workspace.load(conflict_repo).head()
    sides = view.conflict_sides("file.txt", "@")
    # A regular 3-way conflict's term order: each add with its preceding base,
    # starting with the first add — [side_a, base, side_b].
    assert sides == ["version A\n", "base\n", "version B\n"]


def test_conflict_sides_round_trip_with_content(conflict_repo: Path) -> None:
    """Materializing then parsing yields the same sides `jj file show` shows."""
    view = pyjutsu.Workspace.load(conflict_repo).head()
    marked = view.conflict_content("file.txt", "@", style="diff")
    sides = view.conflict_sides("file.txt", "@")
    # Every side's content appears inside the marked text.
    for side in sides:
        assert side in marked


def test_conflict_sides_requires_conflict(linear_repo: Path) -> None:
    view = pyjutsu.Workspace.load(linear_repo).head()
    with pytest.raises(ConflictError):
        view.conflict_sides("b.txt", "@-")


def test_resolve_conflict_round_trip(conflict_repo: Path, jj: JjCli) -> None:
    """Materialize, edit one side, resolve, assert `jj` reports no conflict."""
    ws = pyjutsu.Workspace.load(conflict_repo)
    view = ws.head()
    marked = view.conflict_content("file.txt", "@", style="git")
    # Keep one side only: a plain resolved file, no markers.
    sides = view.conflict_sides("file.txt", "@")
    resolved = sides[0]  # "version A\n"
    assert resolved in marked

    with ws.transaction("resolve conflict") as tx:
        commit = tx.resolve_conflict("file.txt", resolved)
    assert commit.has_conflict is False

    # The CLI agrees: no conflicted paths remain in `@` (jj resolve --list errors with
    # "No conflicts found at this revision", exit 2), `@`'s tree holds the resolution,
    # and the working-copy file was checked out to it.
    with pytest.raises(subprocess.CalledProcessError):
        jj(conflict_repo, "resolve", "--list")
    assert jj(conflict_repo, "file", "show", "-r", "@", "file.txt") == "version A\n"
    assert (conflict_repo / "file.txt").read_text() == "version A\n"


def test_resolve_conflict_accepts_full_content(conflict_repo: Path, jj: JjCli) -> None:
    """Resolving with a plain (marker-free) content writes that content verbatim."""
    ws = pyjutsu.Workspace.load(conflict_repo)
    with ws.transaction("resolve conflict") as tx:
        commit = tx.resolve_conflict("file.txt", "merged text\n")
    assert commit.has_conflict is False
    assert jj(conflict_repo, "file", "show", "-r", "@", "file.txt") == "merged text\n"
    assert (conflict_repo / "file.txt").read_text() == "merged text\n"


def test_resolve_conflict_non_conflict_path_raises(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    with ws.transaction("resolve") as tx:
        with pytest.raises(ConflictError):
            tx.resolve_conflict("b.txt", "x\n")
