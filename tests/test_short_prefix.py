"""C3: short id prefixes (lane `003/c3`).

Oracle is the pinned `jj` CLI's `.shortest()` templates. The binding
disambiguates across the whole repository (C3 open decision, recorded in
`src/id_prefix.rs`), so the oracle comparison runs in a repo whose visible set
equals the whole repo (a fresh fixture), where the CLI's `visible()` scoping
and whole-repo disambiguation agree.
"""

from __future__ import annotations

from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import PyjutsuError

from tests.diff.jj_cli import JjCli


def test_shortest_prefix_matches_cli_change_id(linear_repo: Path, jj: JjCli) -> None:
    ws = pyjutsu.Workspace.load(linear_repo)
    view = ws.head()
    for commit in view.log("all()"):
        cli = jj.template(linear_repo, commit.commit_id, "change_id.shortest()")
        ours = view.shortest_prefix(commit.change_id)
        assert ours == cli
        # The prefix is a prefix of the full z-k change id.
        assert commit.change_id.startswith(ours)


def test_shortest_prefix_matches_cli_commit_id(linear_repo: Path, jj: JjCli) -> None:
    """Whole-repo disambiguation can be longer than the CLI's `visible()`-scoped answer (the
    whole index includes hidden commits), so the contract is: a real prefix that resolves
    uniquely back to the same commit."""
    ws = pyjutsu.Workspace.load(linear_repo)
    view = ws.head()
    for commit in view.log("all()"):
        ours = view.shortest_prefix(commit.commit_id)
        cli = jj.template(linear_repo, commit.commit_id, "commit_id.shortest()")
        assert commit.commit_id.startswith(ours)
        assert len(ours) >= len(cli)  # whole-repo >= visible-set
        assert view.resolve(ours).commit_id == commit.commit_id


def test_commit_model_carries_short_ids(linear_repo: Path, jj: JjCli) -> None:
    view = pyjutsu.Workspace.load(linear_repo).head()
    commit = view.resolve("@-")
    assert commit.short_commit_id is not None
    assert commit.commit_id.startswith(commit.short_commit_id)
    assert view.resolve(commit.short_commit_id).commit_id == commit.commit_id
    # Change-id prefixes agree with the CLI (rewritten commits share change ids).
    assert commit.short_change_id == jj.template(
        linear_repo, "@-", "change_id.shortest()"
    )


def test_short_prefixes_are_unique(linear_repo: Path) -> None:
    view = pyjutsu.Workspace.load(linear_repo).head()
    commits = view.log("all()")
    change_prefixes = [view.shortest_prefix(c.change_id) for c in commits]
    commit_prefixes = [view.shortest_prefix(c.commit_id) for c in commits]
    assert len(set(change_prefixes)) == len(change_prefixes)
    assert len(set(commit_prefixes)) == len(commit_prefixes)


def test_short_prefix_resolves_back_to_its_id(linear_repo: Path) -> None:
    view = pyjutsu.Workspace.load(linear_repo).head()
    for commit in view.log("all()"):
        # A change-id prefix resolves as a revset symbol back to the same change.
        resolved = view.resolve(view.shortest_prefix(commit.change_id))
        assert resolved.change_id == commit.change_id
        # A commit-id prefix resolves back to the same commit id.
        resolved = view.resolve(view.shortest_prefix(commit.commit_id))
        assert resolved.commit_id == commit.commit_id


def test_shortest_prefix_unknown_id_yields_never_matching_prefix(linear_repo: Path) -> None:
    """An unknown id yields a prefix that never matches any commit id (jj's own contract), so
    resolving it fails rather than silently resolving to a different commit."""
    view = pyjutsu.Workspace.load(linear_repo).head()
    unknown = "f" * 40
    prefix = view.shortest_prefix(unknown)
    assert unknown.startswith(prefix)
    with pytest.raises(pyjutsu.RevsetError):
        view.resolve(prefix)


def test_shortest_prefix_rejects_garbage(linear_repo: Path) -> None:
    view = pyjutsu.Workspace.load(linear_repo).head()
    with pytest.raises(PyjutsuError):
        view.shortest_prefix("not-an-id-!")
    with pytest.raises(PyjutsuError):
        view.shortest_prefix("")
