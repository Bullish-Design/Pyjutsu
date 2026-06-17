"""Slice 1 — the revset builder renders strings; correctness is rendered-string + result parity.

The builder evaluates nothing, so it is correct iff (a) the rendered ``.expr`` equals the
hand-written jj string and (b) feeding that builder to a read gives the same result as feeding the
equivalent string (which the rest of the suite already proves matches the CLI).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pyjutsu import Pattern, Workspace
from pyjutsu import revset as R
from pyjutsu.revset import _quote

from tests.diff.jj_cli import JjCli


def test_render_matches_handwritten() -> None:
    """A spread of builder expressions renders byte-identically to the hand-written jj strings."""
    assert R.all_().expr == "all()"
    assert R.root().expr == "root()"
    assert R.working_copy().expr == "@"
    assert R.author("alice").expr == 'author(substring:"alice")'
    assert R.description(Pattern.exact("fix")).expr == 'description(exact:"fix")'
    assert R.bookmark("main").expr == 'bookmarks(exact:"main")'
    assert R.bookmarks().expr == "bookmarks()"
    # Combinators over-parenthesize each operand so precedence is never ambiguous.
    assert (
        R.author("a") & ~R.description(Pattern.glob("x*"))
    ).expr == '(author(substring:"a") & (~description(glob:"x*")))'
    assert (R.author("a") | R.committer("b")).expr == (
        '(author(substring:"a") | committer(substring:"b"))'
    )
    assert (R.all_() - R.root()).expr == "(all() ~ root())"
    assert R.range(R.root(), R.working_copy()).expr == "(root()..@)"
    assert R.bookmark("main").dag_range(R.working_copy()).expr == '(bookmarks(exact:"main")::@)'
    assert R.working_copy().ancestors().expr == "(::@)"
    assert R.bookmark("main").descendants().expr == '(bookmarks(exact:"main")::)'
    assert R.latest(R.author("a"), 3).expr == 'latest(author(substring:"a"), 3)'
    assert R.heads(R.all_()).expr == "heads(all())"


def test_quote_escapes() -> None:
    """Quoting mirrors jj's escape_string exactly (the cases that matter for dogfooding)."""
    assert _quote('he said "hi"\n\\') == '"he said \\"hi\\"\\n\\\\"'
    assert _quote("a\tb") == '"a\\tb"'
    assert _quote("plain") == '"plain"'
    # A non-named ASCII control becomes \xNN; non-ASCII passes through verbatim.
    assert _quote("\x01") == '"\\x01"'
    assert _quote("café") == '"café"'


def test_builder_equals_string_query(linear_repo: Path) -> None:
    """Feeding a builder to log() yields the same commits as the equivalent hand-written string."""
    ws = Workspace.load(linear_repo)
    assert ws.log(R.description(Pattern.glob("commit *"))) == ws.log('description(glob:"commit *")')
    assert ws.log(R.all_()) == ws.log("all()")
    assert ws.log(R.range(R.root(), R.working_copy())) == ws.log("root()..@")


def test_builder_equals_cli(linear_repo: Path, jj: JjCli) -> None:
    """The builder's rendered query resolves to the same revisions the CLI reports."""
    ws = Workspace.load(linear_repo)
    got = [c.change_id for c in ws.log(R.range(R.root(), R.working_copy()))]
    assert got == jj.change_ids(linear_repo, "root()..@")


def test_raw_escape_hatch(linear_repo: Path) -> None:
    """raw() wraps a literal fragment verbatim and evaluates identically to the string form."""
    assert R.raw("root()..@").expr == "root()..@"
    ws = Workspace.load(linear_repo)
    assert ws.log(R.raw("root()..@")) == ws.log("root()..@")


def test_commit_rejects_empty() -> None:
    """commit() guards against an empty symbol (would otherwise render an invalid revset)."""
    with pytest.raises(ValueError):
        R.commit("")
