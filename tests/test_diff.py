"""0.6.0 diff read surface, differential vs `jj diff`.

Slice 1: name-status (`diff()`) vs `jj diff --summary`.
Slice 2: content hunks vs `jj diff --git` (per-file added/removed line multisets).
Slice 3: copy/rename detection vs `jj diff --summary` (`R {old => new}`).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import Diff, FileChange, RevsetError

from tests.diff.jj_cli import JjCli


def _file_lines(fc: FileChange) -> tuple[list[str], list[str]]:
    """Flatten a FileChange's hunks into ``(added, removed)`` line lists, newline-stripped."""
    added = [ln.content.rstrip("\n") for h in fc.hunks for ln in h.lines if ln.kind == "added"]
    removed = [ln.content.rstrip("\n") for h in fc.hunks for ln in h.lines if ln.kind == "removed"]
    return added, removed


def test_diff_name_status_matches_cli(diffstat_repo: Path, jj: JjCli) -> None:
    diff = pyjutsu.Workspace.load(diffstat_repo).diff("@-")
    assert isinstance(diff, Diff)
    assert all(isinstance(f, FileChange) for f in diff.files)
    binding = {f.path: f.kind for f in diff.files}
    assert binding == jj.diff_summary(diffstat_repo, "@-")
    assert binding == {"a.txt": "modified", "b.txt": "added"}


def test_diff_added_and_removed(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "addrm"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "describe", "-m", "base")
    jj(repo, "new")
    (repo / "gone.txt").write_text("here today\n")
    jj(repo, "describe", "-m", "add gone")
    jj(repo, "new")
    (repo / "gone.txt").unlink()
    jj(repo, "describe", "-m", "remove gone")

    ws = pyjutsu.Workspace.load(repo)
    add = {f.path: f.kind for f in ws.diff("@-").files}
    assert add == jj.diff_summary(repo, "@-") == {"gone.txt": "added"}
    rm = {f.path: f.kind for f in ws.diff("@").files}
    assert rm == jj.diff_summary(repo, "@") == {"gone.txt": "removed"}


def test_diff_empty_and_root(diffstat_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(diffstat_repo)
    for revset in ("@", "root()"):  # `@` is an empty working-copy commit
        assert ws.diff(revset).files == []


def test_diff_requires_single_revision(diffstat_repo: Path) -> None:
    with pytest.raises(RevsetError):
        pyjutsu.Workspace.load(diffstat_repo).diff("all()")


def test_diff_hunks_match_cli(diffstat_repo: Path, jj: JjCli) -> None:
    diff = pyjutsu.Workspace.load(diffstat_repo).diff("@-")
    cli = jj.diff_git(diffstat_repo, "@-")
    by_path = {f.path: f for f in diff.files}
    assert by_path.keys() == cli.keys()
    for path, (cli_added, cli_removed) in cli.items():
        added, removed = _file_lines(by_path[path])
        # Structured (multiset) parity, not hunk-boundary/byte parity.
        assert Counter(added) == Counter(cli_added), path
        assert Counter(removed) == Counter(cli_removed), path
    # Spot-check the actual content so a degenerate empty-vs-empty pass can't hide.
    assert _file_lines(by_path["a.txt"]) == (["CHANGED", "l4"], ["l2"])
    assert _file_lines(by_path["b.txt"]) == (["b1", "b2"], [])


def test_diff_binary_has_no_hunks(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "bin"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "describe", "-m", "base")
    jj(repo, "new")
    (repo / "data.bin").write_bytes(b"\x00\x01\x02\x00binary\x00")
    jj(repo, "describe", "-m", "add binary")

    binary = next(f for f in pyjutsu.Workspace.load(repo).diff("@").files if f.path == "data.bin")
    assert binary.kind == "added"
    assert binary.binary is True
    assert binary.hunks == []


def test_diff_hunk_line_kinds(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "kinds"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "describe", "-m", "base")
    jj(repo, "new")
    (repo / "added.txt").write_text("x1\nx2\nx3\n")  # pure addition
    jj(repo, "describe", "-m", "add file")
    jj(repo, "new")
    (repo / "added.txt").unlink()  # pure deletion
    jj(repo, "describe", "-m", "del file")

    ws = pyjutsu.Workspace.load(repo)
    add = next(f for f in ws.diff("@-").files if f.path == "added.txt")
    assert {ln.kind for h in add.hunks for ln in h.lines} == {"added"}
    rm = next(f for f in ws.diff("@").files if f.path == "added.txt")
    assert {ln.kind for h in rm.hunks for ln in h.lines} == {"removed"}


def test_diff_rename_matches_cli(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "rename"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "old.txt").write_text("one\ntwo\nthree\nfour\nfive\n")
    jj(repo, "describe", "-m", "base")
    jj(repo, "new")
    # A plain move (no `git mv`) — jj's git backend detects the delete+add as a rename.
    (repo / "old.txt").rename(repo / "new.txt")
    jj(repo, "describe", "-m", "rename")

    diff = pyjutsu.Workspace.load(repo).diff("@")
    cli = jj.rename_summary(repo, "@")
    assert cli == {"new.txt": ("renamed", "old.txt")}  # backend emits the rename record
    moved = next(f for f in diff.files if f.path == "new.txt")
    assert moved.kind == "renamed"
    assert moved.source == "old.txt"
    assert moved.binary is False
    assert moved.hunks == []  # identical content moved ⇒ no line changes
    # The binding agrees with the CLI's (kind, source).
    binding = {f.path: (f.kind, f.source) for f in diff.files}
    assert binding == cli


# --- two-revset diff (`diff(from_, to)`, concept §12) -------------------------------------------


def test_two_revset_diff_parent_to_child_equals_single(linear_repo: Path) -> None:
    """`diff(A, B)` (A→B range) must equal `diff(B)` when B's only parent is A: both diff A's tree
    against B's. In `linear_repo`, `@--` is B and `@---` is A (A→B→C→@)."""
    ws = pyjutsu.Workspace.load(linear_repo)
    single = ws.diff("@--")  # B vs its parent A
    between = ws.diff("@---", "@--")  # A -> B
    assert isinstance(between, Diff)
    assert [(f.path, f.kind, f.source) for f in between.files] == [
        (f.path, f.kind, f.source) for f in single.files
    ]
    assert [_file_lines(f) for f in between.files] == [_file_lines(f) for f in single.files]


def test_two_revset_diff_spans_commits_matches_cli(linear_repo: Path, jj: JjCli) -> None:
    """A range across multiple commits (A→C: adds b.txt then c.txt) matches
    `jj diff --from A --to C --summary`."""
    ws = pyjutsu.Workspace.load(linear_repo)
    between = ws.diff("@---", "@-")  # A -> C
    binding = {f.path: f.kind for f in between.files}
    assert binding == jj.diff_summary_between(linear_repo, "@---", "@-")
    assert binding == {"b.txt": "added", "c.txt": "added"}


def test_two_revset_diff_is_directional(linear_repo: Path) -> None:
    """Reversing the endpoints inverts each change: A→C adds b/c.txt, C→A removes them."""
    ws = pyjutsu.Workspace.load(linear_repo)
    forward = {f.path: f.kind for f in ws.diff("@---", "@-").files}
    backward = {f.path: f.kind for f in ws.diff("@-", "@---").files}
    assert forward == {"b.txt": "added", "c.txt": "added"}
    assert backward == {"b.txt": "removed", "c.txt": "removed"}


def test_two_revset_diff_same_revision_is_empty(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    assert ws.diff("@-", "@-").files == []


def test_two_revset_diff_rejects_multi_revision_endpoint(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(RevsetError):
        ws.diff("@---", "@-|@--")  # `to` names two revisions
    with pytest.raises(RevsetError):
        ws.diff("@-|@--", "@-")  # `from_` names two revisions
