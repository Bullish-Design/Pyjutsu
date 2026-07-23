"""Gitignore-status query (project 14 §P3): ``Workspace.tracked_ignored_paths``.

Paths tracked in ``@`` that the working-copy gitignore would also ignore — the tracked-but-ignored
churn source ``untrack_paths`` fixes.
"""

from __future__ import annotations

from pathlib import Path

import pyjutsu

from tests.diff.jj_cli import JjCli


def test_tracked_then_ignored_path_is_reported(tmp_path: Path, jj: JjCli) -> None:
    repo = tmp_path / "ign"
    repo.mkdir()
    jj.init_colocated(repo)
    # Track both files in a described commit, then add a .gitignore matching one of them.
    (repo / "keep.txt").write_text("keep\n")
    (repo / "local.json").write_text("{}\n")
    jj(repo, "describe", "-m", "track both")
    (repo / ".gitignore").write_text("local.json\n")
    jj(repo, "new")  # snapshot picks up .gitignore; @- holds the tracked tree

    ws = pyjutsu.Workspace.load(repo)
    ignored = ws.tracked_ignored_paths()
    assert "local.json" in ignored  # tracked AND matched by the ignore rule
    assert "keep.txt" not in ignored
    assert ".gitignore" not in ignored
