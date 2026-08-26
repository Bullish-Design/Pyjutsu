"""C6: absorb (lane `003/c6`).

Oracle is the pinned `jj` CLI's `jj absorb`. Each fixture gives two ancestors
their own region of one file, then edits one line in each region from `@`, so
every hunk has exactly one owning ancestor.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyjutsu
import pytest

from tests.diff.jj_cli import JjCli

#: `@`'s parent content: lines 1-3 introduced by commit A, lines 4-6 by commit B.
_A_TEXT = "a1\na2\na3\n"
_B_TEXT = "a1\na2\na3\nb1\nb2\nb3\n"
#: `@` edits one line inside A's region and one inside B's region.
_SOURCE_TEXT = "a1\nA2\na3\nb1\nB2\nb3\n"


def _absorb_repo(tmp_path: Path, jj: JjCli, name: str = "absorb") -> Path:
    """A repo `A -> B -> @` whose `@` edits one A-owned line and one B-owned line.

    `@` stays description-less and un-snapshotted, exactly like a dirty working copy;
    both the CLI and the binding snapshot it themselves before absorbing.
    """
    repo = tmp_path / name
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "file.txt").write_text(_A_TEXT)
    jj(repo, "describe", "-m", "commit A")
    jj(repo, "new")
    (repo / "file.txt").write_text(_B_TEXT)
    jj(repo, "describe", "-m", "commit B")
    jj(repo, "new")
    (repo / "file.txt").write_text(_SOURCE_TEXT)
    return repo


def _repo_snapshot(jj: JjCli, repo: Path) -> dict[str, tuple[str, str]]:
    """commit_id -> (description, file.txt content) for every non-root commit except `@`.

    `@` is excluded because absorb abandons the emptied source and jj creates a fresh
    working-copy commit, whose change id is random — so its commit id differs between two
    otherwise identical runs. The tests assert `@`'s content separately.
    """
    out = jj(
        repo,
        "log",
        "-r",
        "all() ~ root() ~ @",
        "--no-graph",
        "-T",
        'commit_id ++ "|" ++ description.first_line() ++ "\\n"',
    )
    snapshot: dict[str, tuple[str, str]] = {}
    for line in out.splitlines():
        if not line:
            continue
        commit_id, description = line.split("|", 1)
        content = jj(repo, "file", "show", "-r", commit_id, "file.txt")
        snapshot[commit_id] = (description, content)
    return snapshot


def test_absorb_matches_cli(tmp_path: Path, jj: JjCli) -> None:
    """The binding's absorb leaves the repo in the same state as `jj absorb`.

    The shared test config pins `debug.commit-timestamp`, so the same mutation over two
    byte-identical repos produces identical commit ids — the comparison is exact.
    """
    cli_repo = _absorb_repo(tmp_path, jj)
    ws_repo = tmp_path / "absorb-ws"
    shutil.copytree(cli_repo, ws_repo)

    jj(cli_repo, "absorb")

    ws = pyjutsu.Workspace.load(ws_repo)
    with ws.transaction("absorb") as tx:
        result = tx.absorb("@")

    assert _repo_snapshot(jj, ws_repo) == _repo_snapshot(jj, cli_repo)
    assert jj(ws_repo, "file", "show", "-r", "@", "file.txt") == jj(
        cli_repo, "file", "show", "-r", "@", "file.txt"
    )

    # Both ancestors received a hunk; the source was empty afterwards and had no
    # description, so absorb abandoned it.
    assert len(result.rewritten_destinations) == 2
    assert {c.description.strip() for c in result.rewritten_destinations} == {
        "commit A",
        "commit B",
    }
    assert result.rewritten_source is None
    assert result.skipped_paths == []


def test_absorb_moves_each_hunk_to_its_own_ancestor(tmp_path: Path, jj: JjCli) -> None:
    repo = _absorb_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("absorb") as tx:
        result = tx.absorb("@")

    by_description = {c.description.strip(): c for c in result.rewritten_destinations}
    view = ws.head()
    # Commit A now owns the `A2` edit and nothing else; commit B owns `B2`.
    assert view.file_content("file.txt", by_description["commit A"].commit_id) == b"a1\nA2\na3\n"
    assert (
        view.file_content("file.txt", by_description["commit B"].commit_id)
        == _SOURCE_TEXT.encode()
    )
    assert result.num_rebased >= 0


def test_absorb_into_scopes_the_destinations(tmp_path: Path, jj: JjCli) -> None:
    """`into` narrows the candidate ancestors; a hunk outside it stays behind."""
    repo = _absorb_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    a_id = ws.head().resolve("@--").commit_id
    with ws.transaction("absorb into A") as tx:
        result = tx.absorb("@", into="@--")

    assert len(result.rewritten_destinations) == 1
    assert result.rewritten_destinations[0].description.strip() == "commit A"
    # The source kept the B-owned hunk, so it was rewritten rather than abandoned.
    assert result.rewritten_source is not None
    view = ws.head()
    assert view.file_content("file.txt", "@") == _SOURCE_TEXT.encode()
    # Commit B is untouched apart from the rebase onto the rewritten A.
    assert view.resolve("@-").description.strip() == "commit B"
    assert a_id not in [c.commit_id for c in result.rewritten_destinations]


def test_absorb_leaves_ambiguous_hunks_behind(tmp_path: Path, jj: JjCli) -> None:
    """A pure insertion on the A/B boundary belongs to no single ancestor, so it stays."""
    repo = tmp_path / "ambiguous"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "file.txt").write_text(_A_TEXT)
    jj(repo, "describe", "-m", "commit A")
    jj(repo, "new")
    (repo / "file.txt").write_text(_B_TEXT)
    jj(repo, "describe", "-m", "commit B")
    jj(repo, "new")
    # `zzz` is inserted exactly between A's last line and B's first line.
    (repo / "file.txt").write_text("a1\nA2\na3\nzzz\nb1\nb2\nb3\n")

    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("absorb") as tx:
        result = tx.absorb("@")

    # Only the A-owned edit moved; the boundary insertion had no unique owner.
    assert [c.description.strip() for c in result.rewritten_destinations] == ["commit A"]
    assert result.rewritten_source is not None
    assert b"zzz" in ws.head().file_content("file.txt", "@")


def test_absorb_skips_a_path_it_cannot_split(tmp_path: Path, jj: JjCli) -> None:
    """A symlink has no line structure, so absorb reports it in `skipped_paths`."""
    repo = tmp_path / "symlink"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "file.txt").write_text(_A_TEXT)
    (repo / "link").symlink_to("file.txt")
    jj(repo, "describe", "-m", "commit A")
    jj(repo, "new")
    (repo / "file.txt").write_text("a1\nA2\na3\n")
    (repo / "link").unlink()
    (repo / "link").symlink_to("other.txt")

    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("absorb") as tx:
        result = tx.absorb("@")

    assert result.skipped_paths == [("link", "Is a symlink")]
    # The file hunk still moved into commit A.
    assert [c.description.strip() for c in result.rewritten_destinations] == ["commit A"]


def test_absorb_requires_a_single_source(tmp_path: Path, jj: JjCli) -> None:
    repo = _absorb_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("absorb") as tx:
        with pytest.raises(pyjutsu.RevsetError):
            tx.absorb("all()")


def test_absorb_refuses_an_immutable_source(tmp_path: Path, jj: JjCli) -> None:
    repo = _absorb_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("absorb") as tx:
        with pytest.raises(pyjutsu.ImmutableCommitError):
            tx.absorb("root()")
