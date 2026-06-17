"""Slice 11: git network — `git_fetch` / `git_push` + the pure-Python `git_clone` classmethod.

These are `Workspace`-level verbs that sync jj with *remote* git repos (jj 0.38 drives them via a
`git` subprocess). All tests are local — they use on-disk **bare** remotes, no real network. The
differential oracle is `jj git fetch|push|clone`; the common assertion is **ref state** read
straight from the bare remote with `git show-ref` (dodging the colocated jj-read trap), plus the
binding publishing **exactly one op** per fetch/push.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import GitError

from tests.diff.jj_cli import JjCli


def _op_count(repo: Path, jj: JjCli) -> int:
    return len(jj.op_log_ids(repo))


def _init_bare(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    return path


def _has_ref(git_dir: Path, ref: str) -> bool:
    """Whether ``ref`` exists in the git repo at ``git_dir`` (works for bare repos)."""
    result = subprocess.run(
        ["git", "-C", str(git_dir), "show-ref", "--verify", "--quiet", ref],
        capture_output=True,
    )
    return result.returncode == 0


def _remote_heads(git_dir: Path) -> set[str]:
    """The set of branch names under ``refs/heads/`` in the (bare) git repo at ``git_dir``."""
    # `git show-ref --heads` exits non-zero when there are no heads, so don't `check`.
    out = subprocess.run(
        ["git", "-C", str(git_dir), "show-ref", "--heads"],
        capture_output=True,
        text=True,
    ).stdout
    return {line.split("refs/heads/", 1)[1] for line in out.splitlines() if "refs/heads/" in line}


# --- git_push (headline) ----------------------------------------------------------------------


def test_push_bookmark_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Put a real (described, non-working-copy) commit under a bookmark on each side, then push to
    # that side's own bare origin. Both sides start from the byte-identical `scratch_repo`.
    other = tmp_path / "copy"
    subprocess.run(["cp", "-r", str(scratch_repo), str(other)], check=True)

    origin_b = _init_bare(tmp_path / "origin_b.git")
    origin_c = _init_bare(tmp_path / "origin_c.git")

    # Binding: advance `@` so the described commit becomes `@-`, bookmark it, add remote, push.
    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("new") as tx:
        tx.new()
    with ws.transaction("bookmark") as tx:
        tx.create_bookmark("feature", "@-")
    ws.add_remote("origin", str(origin_b))
    ops_before = _op_count(scratch_repo, jj)

    op = ws.git_push("origin", "feature", allow_new=True)

    assert op is not None
    assert "push" in op.description.lower()
    assert _has_ref(origin_b, "refs/heads/feature")
    # The remote-tracking row `feature@origin` now exists in the binding's view.
    assert ("feature", "origin") in {(b.name, b.remote) for b in ws.bookmarks()}
    # Exactly one op published by the push.
    assert _op_count(scratch_repo, jj) == ops_before + 1

    # CLI oracle on the sibling: same shape, its own bare origin → the ref lands there too.
    jj(other, "new")
    jj(other, "bookmark", "create", "feature", "-r", "@-")
    jj(other, "git", "remote", "add", "origin", str(origin_c))
    jj.git_push(other, "feature", allow_new=True)
    assert _has_ref(origin_c, "refs/heads/feature")


def test_push_new_without_allow_new_raises(scratch_repo: Path, tmp_path: Path) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("bookmark") as tx:
        tx.create_bookmark("feature", "@")
    ws.add_remote("origin", str(origin))
    # `feature` has no remote-tracking ref yet ⇒ it's new on the remote ⇒ refused without allow_new.
    with pytest.raises(GitError):
        ws.git_push("origin", "feature")


def test_push_unknown_remote_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("bookmark") as tx:
        tx.create_bookmark("feature", "@")
    with pytest.raises(GitError):
        ws.git_push("nope", "feature", allow_new=True)


def test_push_unknown_bookmark_raises(scratch_repo: Path, tmp_path: Path) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_remote("origin", str(origin))
    with pytest.raises(GitError):
        ws.git_push("origin", "ghost", allow_new=True)


def test_push_delete_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Push `feature`, then delete it on the remote. Oracle: `jj bookmark delete` + `jj git push
    # --deleted`. The differential assertion is ref state in each side's own bare origin.
    other = tmp_path / "copy"
    subprocess.run(["cp", "-r", str(scratch_repo), str(other)], check=True)
    origin_b = _init_bare(tmp_path / "origin_b.git")
    origin_c = _init_bare(tmp_path / "origin_c.git")

    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("new") as tx:
        tx.new()
    with ws.transaction("bookmark") as tx:
        tx.create_bookmark("feature", "@-")
    ws.add_remote("origin", str(origin_b))
    ws.git_push("origin", "feature", allow_new=True)
    assert _has_ref(origin_b, "refs/heads/feature")

    op = ws.git_push("origin", "feature", delete=True)
    assert op is not None
    assert "push" in op.description.lower()
    assert not _has_ref(origin_b, "refs/heads/feature")  # gone on the remote

    # CLI oracle on the sibling: same push, then delete-and-push → ref gone there too.
    jj(other, "new")
    jj(other, "bookmark", "create", "feature", "-r", "@-")
    jj(other, "git", "remote", "add", "origin", str(origin_c))
    jj.git_push(other, "feature", allow_new=True)
    assert _has_ref(origin_c, "refs/heads/feature")
    jj(other, "bookmark", "delete", "feature")
    jj(other, "git", "push", "--deleted")
    assert not _has_ref(origin_c, "refs/heads/feature")


def test_push_multiple_bookmarks(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Push two bookmarks in one call → both refs land on the remote, one op published.
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("new") as tx:
        tx.new()
    with ws.transaction("bookmarks") as tx:
        tx.create_bookmark("feat-a", "@-")
        tx.create_bookmark("feat-b", "@-")
    ws.add_remote("origin", str(origin))
    ops_before = _op_count(scratch_repo, jj)

    op = ws.git_push("origin", ["feat-a", "feat-b"], allow_new=True)

    assert op is not None
    assert _has_ref(origin, "refs/heads/feat-a")
    assert _has_ref(origin, "refs/heads/feat-b")
    # One operation for the whole multi-bookmark push.
    assert _op_count(scratch_repo, jj) == ops_before + 1


def test_push_all_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # `all=True` pushes every local bookmark (creating new ones), one op. Both sides start from the
    # byte-identical `scratch_repo` and push to their own bare origin; the head sets must match.
    other = tmp_path / "copy"
    subprocess.run(["cp", "-r", str(scratch_repo), str(other)], check=True)
    origin_b = _init_bare(tmp_path / "origin_b.git")
    origin_c = _init_bare(tmp_path / "origin_c.git")

    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("new") as tx:
        tx.new()
    with ws.transaction("bookmarks") as tx:
        tx.create_bookmark("feat-a", "@-")
        tx.create_bookmark("feat-b", "@-")
    ws.add_remote("origin", str(origin_b))
    ops_before = _op_count(scratch_repo, jj)

    op = ws.git_push("origin", all=True)

    assert op is not None
    assert _op_count(scratch_repo, jj) == ops_before + 1  # one op for the whole bulk push

    # CLI oracle on the sibling.
    jj(other, "new")
    jj(other, "bookmark", "create", "feat-a", "-r", "@-")
    jj(other, "bookmark", "create", "feat-b", "-r", "@-")
    jj(other, "git", "remote", "add", "origin", str(origin_c))
    jj.git_push_all(other)

    assert _remote_heads(origin_b) == _remote_heads(origin_c) == {"feat-a", "feat-b"}


def test_push_tracked_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # `tracked=True` pushes only bookmarks already tracking the remote: feat-a (pushed, then moved
    # forward) is pushed; the never-pushed feat-b is left alone, not created on the remote.
    other = tmp_path / "copy"
    subprocess.run(["cp", "-r", str(scratch_repo), str(other)], check=True)
    origin_b = _init_bare(tmp_path / "origin_b.git")
    origin_c = _init_bare(tmp_path / "origin_c.git")

    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("new") as tx:
        tx.new()
    with ws.transaction("bookmark") as tx:
        tx.create_bookmark("feat-a", "@-")
    ws.add_remote("origin", str(origin_b))
    ws.git_push("origin", "feat-a", allow_new=True)  # feat-a is now tracked on origin
    # Move feat-a to a described, direct child of @- (so its push has something to do and never
    # drags an empty-description commit along), and add an untracked feat-b.
    with ws.transaction("advance") as tx:
        c = tx.new("@-")
        tx.describe(c.change_id, "advance feat-a")
        tx.set_bookmark("feat-a", c.change_id)
        tx.create_bookmark("feat-b", c.change_id)
    ops_before = _op_count(scratch_repo, jj)

    op = ws.git_push("origin", tracked=True)

    assert op is not None
    assert _op_count(scratch_repo, jj) == ops_before + 1
    assert _remote_heads(origin_b) == {"feat-a"}  # feat-b (untracked) was not pushed

    # CLI oracle on the sibling: same logical shape.
    jj(other, "new")
    jj(other, "bookmark", "create", "feat-a", "-r", "@-")
    jj(other, "git", "remote", "add", "origin", str(origin_c))
    jj.git_push(other, "feat-a", allow_new=True)
    jj(other, "new", "-r", "@-", "-m", "advance feat-a")
    jj(other, "bookmark", "set", "feat-a", "-r", "@")
    jj(other, "bookmark", "create", "feat-b", "-r", "@")
    jj.git_push_tracked(other)

    assert _remote_heads(origin_c) == {"feat-a"}
    assert _remote_heads(origin_b) == _remote_heads(origin_c)


def test_push_all_and_tracked_raises(scratch_repo: Path, tmp_path: Path) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_remote("origin", str(origin))
    with pytest.raises(GitError):
        ws.git_push("origin", all=True, tracked=True)


def test_push_all_with_names_raises(scratch_repo: Path, tmp_path: Path) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_remote("origin", str(origin))
    with pytest.raises(GitError):
        ws.git_push("origin", "feature", all=True)


def test_push_delete_nonexistent_raises(scratch_repo: Path, tmp_path: Path) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_remote("origin", str(origin))
    # No remote-tracking ref for `ghost` ⇒ nothing to delete on the remote.
    with pytest.raises(GitError):
        ws.git_push("origin", "ghost", delete=True)


def test_push_empty_bookmarks_raises(scratch_repo: Path, tmp_path: Path) -> None:
    origin = _init_bare(tmp_path / "origin.git")
    ws = pyjutsu.Workspace.load(scratch_repo)
    ws.add_remote("origin", str(origin))
    with pytest.raises(GitError):
        ws.git_push("origin", [])


# --- git_fetch (headline) ---------------------------------------------------------------------


def test_fetch_matches_cli(bookmarked_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # `bookmarked_repo`'s bare `origin` already has `feature` pushed. A fresh repo that adds the
    # same remote and fetches should pick up `feature@origin` — on both the binding and the CLI.
    origin = tmp_path / "origin.git"

    b = tmp_path / "B"
    b.mkdir()
    ws_b = pyjutsu.Workspace.init(b, colocate=True)
    ws_b.add_remote("origin", str(origin))
    ops_before = _op_count(b, jj)

    op = ws_b.git_fetch("origin")

    assert op is not None
    assert "fetch" in op.description.lower()
    assert ("feature", "origin") in {(bm.name, bm.remote) for bm in ws_b.bookmarks()}
    assert _op_count(b, jj) == ops_before + 1

    # CLI oracle: another fresh repo on the same origin sees `feature@origin` too.
    c = tmp_path / "C"
    c.mkdir()
    jj(c, "git", "init", "--colocate")
    jj(c, "git", "remote", "add", "origin", str(origin))
    jj.git_fetch(c, "origin")
    assert ("feature", "origin") in {(n, r) for (n, r, *_) in jj.bookmarks(c)}


def test_fetch_noop_returns_none(bookmarked_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    origin = tmp_path / "origin.git"
    b = tmp_path / "B"
    b.mkdir()
    ws_b = pyjutsu.Workspace.init(b, colocate=True)
    ws_b.add_remote("origin", str(origin))
    ws_b.git_fetch("origin")  # first fetch imports `feature@origin`

    ops_before = _op_count(b, jj)
    assert ws_b.git_fetch("origin") is None  # nothing new ⇒ no op
    assert _op_count(b, jj) == ops_before


def test_fetch_unknown_remote_raises(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(GitError):
        ws.git_fetch("nope")


def _multi_bookmark_origin(jj: JjCli, tmp_path: Path) -> Path:
    """A bare ``origin`` with bookmarks ``feature/a``, ``feature/b``, ``main`` (so a glob can
    discriminate `feature/*` from `main`)."""
    up = tmp_path / "up"
    up.mkdir()
    jj.init_colocated(up)
    (up / "x.txt").write_text("x\n")
    jj(up, "describe", "-m", "base")
    for bm in ("feature/a", "feature/b", "main"):
        jj(up, "bookmark", "create", bm, "-r", "@")
    jj(up, "new", "-m", "top")  # move @ off the bookmarked (described) commit before pushing
    origin = _init_bare(tmp_path / "origin.git")
    jj(up, "git", "remote", "add", "origin", str(origin))
    jj(up, "git", "push", "--all")
    return origin


def _remote_rows(ws: pyjutsu.Workspace) -> set[tuple[str, str]]:
    return {(bm.name, bm.remote) for bm in ws.bookmarks() if bm.remote}


def _cli_remote_rows(jj: JjCli, repo: Path) -> set[tuple[str, str]]:
    return {(n, r) for (n, r, *_) in jj.bookmarks(repo) if r}


def test_fetch_glob_matches_cli(tmp_path: Path, jj: JjCli) -> None:
    # `glob:feature/*` imports the two feature bookmarks but NOT `main` — binding vs CLI.
    origin = _multi_bookmark_origin(jj, tmp_path)

    b = tmp_path / "B"
    b.mkdir()
    ws_b = pyjutsu.Workspace.init(b, colocate=True)
    ws_b.add_remote("origin", str(origin))
    ops_before = _op_count(b, jj)

    op = ws_b.git_fetch("origin", bookmarks=["glob:feature/*"])

    assert op is not None
    rows_b = _remote_rows(ws_b)
    assert ("feature/a", "origin") in rows_b
    assert ("feature/b", "origin") in rows_b
    assert ("main", "origin") not in rows_b
    assert _op_count(b, jj) == ops_before + 1

    c = tmp_path / "C"
    c.mkdir()
    jj(c, "git", "init", "--colocate")
    jj(c, "git", "remote", "add", "origin", str(origin))
    jj(c, "git", "fetch", "--remote", "origin", "--branch", "glob:feature/*")
    assert _cli_remote_rows(jj, c) == rows_b


def test_fetch_negative_pattern_matches_cli(tmp_path: Path, jj: JjCli) -> None:
    # `glob:feature/*` minus `feature/b` ⇒ only `feature/a`. Oracle: `--branch 'glob:feature/* ~
    # feature/b'` (jj's set-difference grammar).
    origin = _multi_bookmark_origin(jj, tmp_path)

    b = tmp_path / "B"
    b.mkdir()
    ws_b = pyjutsu.Workspace.init(b, colocate=True)
    ws_b.add_remote("origin", str(origin))

    op = ws_b.git_fetch("origin", bookmarks=["glob:feature/*", "~feature/b"])

    assert op is not None
    rows_b = _remote_rows(ws_b)
    assert ("feature/a", "origin") in rows_b
    assert ("feature/b", "origin") not in rows_b
    assert ("main", "origin") not in rows_b

    c = tmp_path / "C"
    c.mkdir()
    jj(c, "git", "init", "--colocate")
    jj(c, "git", "remote", "add", "origin", str(origin))
    jj(c, "git", "fetch", "--remote", "origin", "--branch", "glob:feature/* ~ feature/b")
    assert _cli_remote_rows(jj, c) == rows_b


def test_fetch_exact_still_matches_cli(tmp_path: Path, jj: JjCli) -> None:
    # A literal name fetches exactly that bookmark (glob of a literal ≡ exact) — the pre-0.4.0
    # behavior, still matching the CLI.
    origin = _multi_bookmark_origin(jj, tmp_path)

    b = tmp_path / "B"
    b.mkdir()
    ws_b = pyjutsu.Workspace.init(b, colocate=True)
    ws_b.add_remote("origin", str(origin))

    ws_b.git_fetch("origin", bookmarks=["main"])

    rows_b = _remote_rows(ws_b)
    assert ("main", "origin") in rows_b
    assert ("feature/a", "origin") not in rows_b

    c = tmp_path / "C"
    c.mkdir()
    jj(c, "git", "init", "--colocate")
    jj(c, "git", "remote", "add", "origin", str(origin))
    jj(c, "git", "fetch", "--remote", "origin", "--branch", "main")
    assert _cli_remote_rows(jj, c) == rows_b


def test_fetch_bad_glob_raises(bookmarked_repo: Path, tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    b = tmp_path / "B"
    b.mkdir()
    ws_b = pyjutsu.Workspace.init(b, colocate=True)
    ws_b.add_remote("origin", str(origin))
    with pytest.raises(GitError):
        ws_b.git_fetch("origin", bookmarks=["glob:["])  # unbalanced bracket


# --- git_clone (headline) ---------------------------------------------------------------------


def test_clone_matches_cli(bookmarked_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Make the bare `origin`'s default branch resolvable so both sides place `@` on its tip.
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/feature"],
        check=True,
        capture_output=True,
    )

    dest = tmp_path / "clone"
    ws = pyjutsu.Workspace.git_clone(str(origin), dest)

    # The clone has `origin` configured, fetched the remote's `feature` bookmark, and a `.jj`.
    assert (dest / ".jj").is_dir()
    assert {r.name for r in ws.remotes()} == {"origin"}
    assert ("feature", "origin") in {(b.name, b.remote) for b in ws.bookmarks()}

    # The default branch (`feature`) was discovered, so `@` is a fresh empty child of its tip.
    feature_tip = ws.head().resolve("feature@origin").commit_id
    at = ws.working_copy()
    assert at.is_empty
    assert at.parent_ids == [feature_tip]

    # CLI oracle: `jj git clone` yields the same shape against the same bare origin.
    cli_dest = tmp_path / "cli_clone"
    jj.git_clone(str(origin), cli_dest, colocate=False)
    assert (cli_dest / ".jj").is_dir()
    assert jj.remotes(cli_dest).get("origin") == str(origin)
    assert ("feature", "origin") in {(n, r) for (n, r, *_) in jj.bookmarks(cli_dest)}
    cli_tip = jj.commit_id(cli_dest, "feature@origin")
    assert jj.parent_commit_ids(cli_dest, "@") == [cli_tip]


def test_push_then_fetch_roundtrip(scratch_repo: Path, tmp_path: Path) -> None:
    # Push a bookmark from A, then a fresh B that shares the origin fetches it back.
    origin = _init_bare(tmp_path / "origin.git")

    ws_a = pyjutsu.Workspace.load(scratch_repo)
    with ws_a.transaction("bookmark") as tx:
        tx.create_bookmark("feature", "@")
    ws_a.add_remote("origin", str(origin))
    ws_a.git_push("origin", "feature", allow_new=True)

    b = tmp_path / "B"
    b.mkdir()
    ws_b = pyjutsu.Workspace.init(b, colocate=True)
    ws_b.add_remote("origin", str(origin))
    ws_b.git_fetch("origin")
    assert ("feature", "origin") in {(bm.name, bm.remote) for bm in ws_b.bookmarks()}
