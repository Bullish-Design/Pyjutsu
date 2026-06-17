"""0.6.0 diff read surface, differential vs `jj diff`.

Slice 1: name-status (`diff()`) vs `jj diff --summary`.
"""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import Diff, FileChange, RevsetError

from tests.diff.jj_cli import JjCli


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
