"""Guarded publication: the contract of ``Workspace.publish_if``.

Each test uses a disposable repository and the pinned ``jj`` CLI. A test asserts the outcome (which
commit is ``@``, which operations exist, which bytes are on disk), not only that an object exists.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu import PublishError, PublishIncompleteError, StalePublishError

from tests.diff.jj_cli import JjCli
from tests.publish_support import (
    PublishRepo,
    build_publish_repo,
    git_index_paths,
    git_out,
    load,
    observe_commit,
    op_descriptions,
    op_heads,
    op_ids,
    tracked_files,
    tree_diff_is_empty,
)


def _fingerprint(root: Path) -> dict[str, str]:
    """A hash of every working-copy file outside ``.jj`` and ``.git``."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts[0] in {".jj", ".git"} or not path.is_file():
            continue
        out[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _state_files(root: Path) -> dict[str, bytes]:
    """The working-copy state files. A dropped lock handle must leave them unchanged."""
    base = root / ".jj/working_copy"
    return {p.name: p.read_bytes() for p in sorted(base.iterdir()) if p.name != "working_copy.lock"}


# 1. Happy path ---------------------------------------------------------------------------------


def test_happy_path_colocated_makes_at_a_clean_child_of_onto(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    ops_before = op_ids(jj, repo.root)

    result = load(repo).publish_if(repo.expected, repo.onto, "publish smoke")

    # `@` is a new empty commit whose only parent is the exact `--onto` commit.
    new_wc = observe_commit(jj, repo.root, "@")
    assert result.wc_commit == new_wc
    assert new_wc != repo.expected
    assert observe_commit(jj, repo.root, "@-") == repo.onto
    assert jj(repo.root, "--ignore-working-copy", "log", "-r", "@", "--no-graph", "-T", "parents.len()").strip() == "1"
    assert tree_diff_is_empty(jj, repo.root, repo.onto, "@")
    # The working-copy files are `P`'s tree.
    assert (repo.root / "a.txt").read_text() == "a edited in P\n"
    assert (repo.root / "p.txt").read_text() == "prepared\n"
    # Git HEAD and the Git index equal the new parent. `git status` shows no change.
    assert git_out(repo.root, "rev-parse", "HEAD").strip() == repo.onto
    assert git_index_paths(repo.root) == tracked_files(jj, repo.root, repo.onto)
    assert git_out(repo.root, "status", "--porcelain").strip() == ""

    # Operations: one publication operation, plus one Git synchronization operation.
    new_ops = op_ids(jj, repo.root)[: len(op_ids(jj, repo.root)) - len(ops_before)]
    assert len(new_ops) == 2
    assert op_descriptions(jj, repo.root)[:2] == ["sync colocated git", "publish smoke"]
    assert result.operation == new_ops[1]
    assert result.head_operation == new_ops[0] == result.git_sync_operation
    assert result.git_sync == "synced"
    assert result.onto == repo.onto
    assert result.expected_wc_commit == repo.expected

    # A following ordinary jj command adds no operation and reports no stale working copy.
    before = op_ids(jj, repo.root)
    status = jj(repo.root, "status")
    assert "stale" not in status.lower()
    assert op_ids(jj, repo.root) == before
    assert len(op_heads(repo.root)) == 1


def test_happy_path_without_git_checkout_adds_one_operation(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj, colocated=False)
    ops_before = len(op_ids(jj, repo.root))

    result = load(repo).publish_if(repo.expected, repo.onto, "publish smoke")

    assert len(op_ids(jj, repo.root)) == ops_before + 1
    assert op_descriptions(jj, repo.root)[0] == "publish smoke"
    assert result.git_sync == "not-colocated"
    assert result.git_sync_operation is None
    assert result.head_operation == result.operation
    assert observe_commit(jj, repo.root, "@-") == repo.onto
    assert (repo.root / "p.txt").read_text() == "prepared\n"
    assert len(op_heads(repo.root)) == 1


def test_publishing_onto_the_current_parent_adds_no_git_operation(tmp_path: Path, jj: JjCli) -> None:
    # Git HEAD already names `@`'s parent, so Git needs no operation. Only the index is rebuilt.
    repo = build_publish_repo(tmp_path, jj)
    result = load(repo).publish_if(repo.expected, repo.base, "publish onto parent")
    assert result.git_sync == "unchanged"
    assert result.git_sync_operation is None
    assert result.head_operation == result.operation


# 2. A committed foreign writer ------------------------------------------------------------------


def _assert_nothing_published(jj: JjCli, repo: PublishRepo, ops_before: list[str]) -> None:
    assert op_ids(jj, repo.root) == ops_before
    assert len(op_heads(repo.root)) == 1
    assert not (repo.root / "p.txt").exists()
    assert (repo.root / "a.txt").read_text() == "base\n"


def test_committed_foreign_writer_from_the_jj_cli_rejects_before_at_moves(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    (repo.root / "foreign.txt").write_text("foreign bytes\n")
    jj(repo.root, "commit", "-m", "foreign work")  # the pinned CLI moves `@`
    ops_before = op_ids(jj, repo.root)
    foreign_commit = observe_commit(jj, repo.root, 'description(exact:"foreign work\\n")')
    at_before = observe_commit(jj, repo.root, "@")

    with pytest.raises(StalePublishError) as caught:
        load(repo).publish_if(repo.expected, repo.onto, "publish smoke")

    assert caught.value.reason == "commit-moved"
    assert caught.value.expected_wc_commit == repo.expected
    assert caught.value.observed_wc_commit == at_before != repo.expected
    assert caught.value.dirty is False
    _assert_nothing_published(jj, repo, ops_before)
    assert observe_commit(jj, repo.root, "@") == at_before
    # The foreign bytes are on disk and in a reachable commit.
    assert (repo.root / "foreign.txt").read_text() == "foreign bytes\n"
    assert jj(repo.root, "--ignore-working-copy", "file", "show", "-r", foreign_commit, "foreign.txt") == "foreign bytes\n"


def test_committed_foreign_writer_from_the_bundled_jj_lib_rejects_before_at_moves(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    writer = load(repo)
    (repo.root / "foreign.txt").write_text("foreign bytes\n")
    with writer.transaction("foreign work") as tx:  # the bundled jj-lib 0.44 moves `@`
        tx.new()
    ops_before = op_ids(jj, repo.root)
    at_before = observe_commit(jj, repo.root, "@")
    assert at_before != repo.expected

    with pytest.raises(StalePublishError) as caught:
        load(repo).publish_if(repo.expected, repo.onto, "publish smoke")

    assert caught.value.reason == "commit-moved"
    _assert_nothing_published(jj, repo, ops_before)
    assert observe_commit(jj, repo.root, "@") == at_before
    assert (repo.root / "foreign.txt").read_text() == "foreign bytes\n"
    committed = observe_commit(jj, repo.root, "@-")
    assert jj(repo.root, "--ignore-working-copy", "file", "show", "-r", committed, "foreign.txt") == "foreign bytes\n"


# 3. Direct writers and stale state --------------------------------------------------------------


@pytest.mark.parametrize("colocated", [True, False])
def test_dirty_file_edit_rejects_with_bytes_intact_and_no_operation(
    tmp_path: Path, jj: JjCli, colocated: bool
) -> None:
    repo = build_publish_repo(tmp_path, jj, colocated=colocated)
    (repo.root / "a.txt").write_text("writer edit, not committed\n")
    (repo.root / "writer-new.txt").write_text("writer new file\n")
    ops_before = op_ids(jj, repo.root)
    state_before = _state_files(repo.root)
    files_before = _fingerprint(repo.root)

    with pytest.raises(StalePublishError) as caught:
        load(repo).publish_if(repo.expected, repo.onto, "publish smoke")

    assert caught.value.reason == "dirty-working-copy"
    assert caught.value.dirty is True
    # No operation, same heads, same `@`, same working-copy state files, same bytes on disk.
    assert op_ids(jj, repo.root) == ops_before
    assert observe_commit(jj, repo.root, "@") == repo.expected
    assert _state_files(repo.root) == state_before
    assert _fingerprint(repo.root) == files_before
    assert (repo.root / "a.txt").read_text() == "writer edit, not committed\n"
    assert not (repo.root / "p.txt").exists()

    # The next ordinary jj command snapshots the writer's bytes into `@`, so they stay reachable.
    jj(repo.root, "status")
    assert jj(repo.root, "--ignore-working-copy", "file", "show", "-r", "@", "a.txt") == "writer edit, not committed\n"
    assert jj(repo.root, "--ignore-working-copy", "file", "show", "-r", "@", "writer-new.txt") == "writer new file\n"


def test_stale_working_copy_state_rejects_and_recovers(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    # Move `@` onto `P` without touching the files: the recorded working-copy tree now differs
    # from `@`'s tree, which is a stale working copy.
    jj(repo.root, "--ignore-working-copy", "rebase", "-r", "@", "-d", repo.onto)
    moved = observe_commit(jj, repo.root, "@")
    ops_before = op_ids(jj, repo.root)
    files_before = _fingerprint(repo.root)
    state_before = _state_files(repo.root)

    with pytest.raises(StalePublishError) as caught:
        load(repo).publish_if(moved, repo.onto, "publish smoke")

    assert caught.value.reason == "stale-working-copy"
    assert caught.value.observed_wc_commit == moved
    assert op_ids(jj, repo.root) == ops_before
    assert _fingerprint(repo.root) == files_before  # no file was written or removed
    assert _state_files(repo.root) == state_before

    # Recovery: `recover` updates the stale working copy, then a retry can succeed.
    ws = load(repo)
    assert ws.is_stale()
    ws.recover()
    assert not ws.is_stale()
    assert (repo.root / "p.txt").read_text() == "prepared\n"


# 4. Input and store errors ----------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "abc", "@", "main", "A" * 40, "g" * 40, "a" * 39, "a" * 41])
def test_ids_must_be_full_lowercase_hex(tmp_path: Path, jj: JjCli, bad: str) -> None:
    repo = build_publish_repo(tmp_path, jj)
    ops_before = op_ids(jj, repo.root)
    with pytest.raises(ValueError):
        load(repo).publish_if(bad, repo.onto, "x")
    with pytest.raises(ValueError):
        load(repo).publish_if(repo.expected, bad, "x")
    assert op_ids(jj, repo.root) == ops_before


def test_unknown_onto_commit_is_a_refusal_that_changes_nothing(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    ops_before = op_ids(jj, repo.root)
    with pytest.raises(PublishError) as caught:
        load(repo).publish_if(repo.expected, "1" * len(repo.expected), "x")
    assert not isinstance(caught.value, (StalePublishError, PublishIncompleteError))
    assert caught.value.reason == "onto-not-found"
    assert op_ids(jj, repo.root) == ops_before
    assert not (repo.root / "p.txt").exists()


def test_unsupported_operation_heads_store_is_refused_on_load(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    (repo.root / ".jj/repo/op_heads/type").write_text("lockless_heads_store")
    with pytest.raises(pyjutsu.WorkspaceError, match="lockless_heads_store"):
        load(repo)


def test_unsupported_working_copy_type_is_refused_on_load(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    (repo.root / ".jj/working_copy/type").write_text("virtual")
    with pytest.raises(pyjutsu.WorkspaceError, match="virtual"):
        load(repo)


def test_open_transaction_blocks_publication(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    ws = load(repo)
    with ws.transaction("open"):
        with pytest.raises(pyjutsu.PyjutsuError, match="transaction is open"):
            ws.publish_if(repo.expected, repo.onto, "x")


def test_the_release_surface_names_the_documented_reasons() -> None:
    assert set(pyjutsu.errors.STALE_REASONS) == {
        "commit-moved",
        "dirty-working-copy",
        "stale-working-copy",
        "op-heads-moved",
    }
    assert issubclass(StalePublishError, PublishError)
    assert issubclass(PublishIncompleteError, PublishError)
