"""Slice 7: `diff_stat`, differential vs `jj diff --stat`."""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import DiffStat, FileStat, RevsetError

from tests.diff.jj_cli import JjCli


def test_totals_match_cli(diffstat_repo: Path, jj: JjCli) -> None:
    stat = pyjutsu.Workspace.load(diffstat_repo).diff_stat("@-")
    assert (stat.total_insertions, stat.total_deletions) == jj.diff_stat_totals(diffstat_repo, "@-")


def test_per_file_counts(diffstat_repo: Path) -> None:
    stat = pyjutsu.Workspace.load(diffstat_repo).diff_stat("@-")
    assert isinstance(stat, DiffStat)
    by_path = {f.path: f for f in stat.files}
    assert by_path.keys() == {"a.txt", "b.txt"}
    assert (by_path["a.txt"].insertions, by_path["a.txt"].deletions) == (2, 1)
    assert (by_path["b.txt"].insertions, by_path["b.txt"].deletions) == (2, 0)
    assert all(isinstance(f, FileStat) for f in stat.files)


def test_totals_are_sum_of_files(diffstat_repo: Path) -> None:
    stat = pyjutsu.Workspace.load(diffstat_repo).diff_stat("@-")
    assert stat.total_insertions == sum(f.insertions for f in stat.files)
    assert stat.total_deletions == sum(f.deletions for f in stat.files)


def test_empty_and_root_commits_have_empty_stat(diffstat_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(diffstat_repo)
    for revset in ("@", "root()"):  # `@` is an empty working-copy commit
        stat = ws.diff_stat(revset)
        assert stat.files == []
        assert (stat.total_insertions, stat.total_deletions) == (0, 0)


def test_binary_file_listed_with_zero_counts(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "bin"
    repo.mkdir()
    jj.init_colocated(repo)
    jj(repo, "describe", "-m", "base")
    jj(repo, "new")
    (repo / "data.bin").write_bytes(b"\x00\x01\x02\x00binary\x00")
    jj(repo, "describe", "-m", "add binary")

    stat = pyjutsu.Workspace.load(repo).diff_stat("@")
    binary = next(f for f in stat.files if f.path == "data.bin")
    assert (binary.insertions, binary.deletions) == (0, 0)  # not line-diffable
    assert stat.total_insertions == 0


def test_diff_stat_requires_single_revision(diffstat_repo: Path) -> None:
    with pytest.raises(RevsetError):
        pyjutsu.Workspace.load(diffstat_repo).diff_stat("all()")


# --- two-revset diff_stat (`diff_stat(from_, to)`, concept §12) ---------------------------------


def test_two_revset_diff_stat_parent_to_child_equals_single(linear_repo: Path) -> None:
    """`diff_stat(A, B)` must equal `diff_stat(B)` when B's only parent is A. In `linear_repo`,
    `@--` is B and `@---` is A."""
    ws = pyjutsu.Workspace.load(linear_repo)
    single = ws.diff_stat("@--")  # B vs parent A
    between = ws.diff_stat("@---", "@--")  # A -> B
    assert isinstance(between, DiffStat)
    assert (between.total_insertions, between.total_deletions) == (
        single.total_insertions,
        single.total_deletions,
    )
    assert {(f.path, f.insertions, f.deletions) for f in between.files} == {
        (f.path, f.insertions, f.deletions) for f in single.files
    }


def test_two_revset_diff_stat_spans_commits_matches_cli(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    between = ws.diff_stat("@---", "@-")  # A -> C
    assert (between.total_insertions, between.total_deletions) == jj.diff_stat_totals_between(
        linear_repo, "@---", "@-"
    )


def test_two_revset_diff_stat_same_revision_is_empty(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    stat = ws.diff_stat("@-", "@-")
    assert stat.files == []
    assert (stat.total_insertions, stat.total_deletions) == (0, 0)


def test_two_revset_diff_stat_rejects_multi_revision_endpoint(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    with pytest.raises(RevsetError):
        ws.diff_stat("@---", "@-|@--")
