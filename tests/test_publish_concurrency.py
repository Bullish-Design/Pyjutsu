"""Guarded publication against concurrent writers, and the limits the guard cannot remove.

- A second writer blocks while the guard holds the working-copy lock.
- A writer between operation preparation and the head update trips the operation-heads comparison.
  A test-only negative control proves the comparison matters.
- A bounded race sweep ends in exactly one of two outcomes.
- Each documented limit is shown here with a test, so the documentation states only verified facts.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from tests.diff.jj_cli import JjCli
from tests.publish_support import (
    HAS_TEST_HOOKS,
    Barrier,
    PublishRepo,
    build_publish_repo,
    cli_command,
    git_out,
    observe_commit,
    op_descriptions,
    op_heads,
    op_ids,
    parse_output,
    start_publish,
)

needs_hooks = pytest.mark.skipif(not HAS_TEST_HOOKS, reason="needs a build with the test-hooks feature")


def _finish(proc: subprocess.Popen[str], timeout: float = 60.0) -> tuple[int, dict[str, str]]:
    out, _ = proc.communicate(timeout=timeout)
    return proc.returncode, parse_output(out)


# Lock behavior ----------------------------------------------------------------------------------


@needs_hooks
@pytest.mark.parametrize("colocated", [True, False])
def test_a_second_writer_blocks_while_the_guard_holds_the_locks(
    tmp_path: Path, jj: JjCli, colocated: bool
) -> None:
    repo = build_publish_repo(tmp_path, jj, colocated=colocated)
    locked = Barrier(tmp_path, "LOCKED")
    publisher = start_publish(repo, locked)
    locked.wait_reached()  # the guard now holds its locks

    writer = subprocess.Popen(
        ["jj", "new", "-m", "second writer"],
        cwd=repo.root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=jj._env(),
    )
    time.sleep(1.5)
    assert writer.poll() is None, "the second writer did not block on the held lock"
    assert "second writer" not in "\n".join(op_descriptions(jj, repo.root))

    locked.release()
    code, facts = _finish(publisher)
    assert code == 0 and facts["result"] == "published"
    writer.communicate(timeout=60)
    assert writer.returncode == 0
    # The writer ran after the guard: its new commit sits on top of the published `@`.
    assert observe_commit(jj, repo.root, "@--") == repo.onto
    assert len(op_heads(repo.root)) == 1


# Operation-heads comparison ---------------------------------------------------------------------


def _competitor_between_prepare_and_publish(
    tmp_path: Path, jj: JjCli, repo: PublishRepo, *, skip_comparison: bool
) -> tuple[int, dict[str, str]]:
    """Pause the guard after it writes its operation, publish a competitor operation, release."""
    gate = Barrier(tmp_path, "BEFORE_PUBLISH")
    extra = {"PJ_TEST_SKIP_OPHEADS_CAS": "1"} if skip_comparison else None
    publisher = start_publish(repo, gate, extra_env=extra)
    gate.wait_reached()
    # `--ignore-working-copy` skips the working-copy lock the guard holds, so this writer runs.
    jj(repo.root, "--ignore-working-copy", "new", repo.base, "-m", "competitor")
    gate.release()
    return _finish(publisher)


@needs_hooks
def test_a_writer_between_preparation_and_head_update_is_caught_by_the_comparison(
    tmp_path: Path, jj: JjCli
) -> None:
    repo = build_publish_repo(tmp_path, jj, colocated=False)
    ops_before = op_ids(jj, repo.root)

    code, facts = _competitor_between_prepare_and_publish(tmp_path, jj, repo, skip_comparison=False)

    assert code == 1
    assert facts["result"] == "stale" and facts["reason"] == "op-heads-moved"
    ops_after = op_ids(jj, repo.root)
    competitor_op = ops_after[0]
    assert ops_after == [competitor_op, *ops_before]  # only the competitor's operation was added
    assert op_descriptions(jj, repo.root)[0] == "new empty commit"
    assert facts["head_operations"] == competitor_op  # reports the heads it saw
    assert op_heads(repo.root) == [competitor_op]  # still one head; ours never joined it
    # Nothing of ours is visible: `@` is the competitor's commit, and no file was written.
    assert observe_commit(jj, repo.root, "@-") == repo.base
    assert not (repo.root / "p.txt").exists()
    assert (repo.root / "a.txt").read_text() == "base\n"


@needs_hooks
def test_negative_control_without_the_comparison_a_third_outcome_appears(tmp_path: Path, jj: JjCli) -> None:
    """With the comparison removed, the same race forks the operation log.

    This test is the only protection against deleting the comparison as redundant. It uses a
    test-only switch that exists solely in a `test-hooks` build.
    """
    repo = build_publish_repo(tmp_path, jj, colocated=False)

    code, _facts = _competitor_between_prepare_and_publish(tmp_path, jj, repo, skip_comparison=True)

    # The guard published over a competitor it never saw: two operation heads.
    assert len(op_heads(repo.root)) == 2, "the comparison did not matter; the control is broken"
    assert code in (0, 2)  # published, or published with a later failure; never a clean refusal


# Race sweep -------------------------------------------------------------------------------------

OFFSETS_MS = [0, 3, -3, 10, -10, 30, -30, 60, -60, 130, -130, -250]


def _busy_wait(seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass


def _op_parents(jj: JjCli, repo: Path) -> dict[str, tuple[list[str], str]]:
    """``{operation id: (parent ids, description)}`` for every operation in the log."""
    out = jj(
        repo,
        "--ignore-working-copy",
        "op",
        "log",
        "--no-graph",
        "-T",
        'id ++ "\\t" ++ parents.map(|p| p.id()).join(",") ++ "\\t" ++ description.first_line() ++ "\\n"',
    )
    rows = {}
    for line in out.splitlines():
        op, parents, description = line.split("\t")
        rows[op] = ([p for p in parents.split(",") if p], description)
    return rows


@needs_hooks
@pytest.mark.parametrize("colocated", [True, False], ids=["colocated", "plain"])
@pytest.mark.parametrize("kind", ["newcommit", "new1"])
@pytest.mark.parametrize("offset_ms", OFFSETS_MS)
def test_race_with_a_jj_cli_writer_ends_in_a_legal_outcome(
    tmp_path: Path, jj: JjCli, colocated: bool, kind: str, offset_ms: int, record_property: pytest.RecordProperty
) -> None:
    """Release the guard and a jj CLI writer against each other, offset by ``offset_ms``.

    Legal outcomes:

    - ``published``: the guard published and the competitor ran after it.
    - ``rejected``: the guard published nothing and the competitor's work is intact.
    - ``published-late-fork``: the guard published, and a competitor that had already loaded the old
      head published its operation afterwards. This is the documented limit. The test proves its
      cause (both operations share a parent) and that the next jj command merges the fork with
      every commit and byte kept.

    Anything else fails: a stale working copy, lost competitor bytes, a clean refusal that left an
    operation of the guard's behind, or an exit code other than 0 and 1.
    """
    import os
    import sys

    repo = build_publish_repo(tmp_path, jj, colocated=colocated)
    start = Barrier(tmp_path, "BEFORE_LOCK")
    go_competitor = tmp_path / "go-competitor"
    os.mkfifo(go_competitor)
    competitor = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "publish_competitor.py"), str(repo.root), str(go_competitor), kind],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "JJ_CONFIG": str(jj._config)},
    )
    publisher = start_publish(repo, start)
    start.wait_reached()
    time.sleep(0.2)  # the competitor finishes interpreter startup and blocks on its FIFO

    def release_competitor() -> None:
        with open(go_competitor, "wb") as fifo:
            fifo.write(b"x")

    if offset_ms >= 0:
        start.release()
        _busy_wait(offset_ms / 1000)
        release_competitor()
    else:
        release_competitor()
        _busy_wait(-offset_ms / 1000)
        start.release()
    out, err = publisher.communicate(timeout=120)
    competitor.communicate(timeout=120)
    code, facts = publisher.returncode, parse_output(out)

    # Read the head files before any jj command: a jj command merges divergent heads.
    heads_at_rest = op_heads(repo.root)
    assert len(heads_at_rest) in (1, 2), heads_at_rest
    forked = len(heads_at_rest) == 2
    parents = _op_parents(jj, repo.root)  # this read merges a fork; `parents` still lists both
    ours = [op for op, (_, desc) in parents.items() if desc == "publish smoke"]
    on_disk_p = (repo.root / "p.txt").exists()

    if code == 0:
        assert facts["result"] == "published" and len(ours) == 1 and on_disk_p
        outcome = "published-late-fork" if forked else "published"
        if forked:
            # Cause: a competitor operation with the same parent as ours was written before ours
            # published and published after it. The guard cannot see or stop that.
            sharing = [
                op
                for op, (op_parents, desc) in parents.items()
                if op != ours[0] and op_parents == parents[ours[0]][0] and desc != "sync colocated git"
            ]
            assert sharing, parents
    else:
        assert code == 1 and facts["result"] == "stale", (code, out, err)
        assert facts["reason"] in {"commit-moved", "dirty-working-copy", "op-heads-moved"}
        assert not ours and not on_disk_p and not forked
        outcome = "rejected"
    record_property("outcome", outcome)

    # Every outcome ends healthy: one head after the next command, no stale working copy, and the
    # competitor's commit and bytes are kept.
    status = subprocess.run(["jj", "status"], cwd=repo.root, capture_output=True, text=True, env=jj._env())
    assert status.returncode == 0 and "stale" not in (status.stdout + status.stderr).lower(), status.stderr
    assert len(op_heads(repo.root)) == 1
    landed = jj(
        repo.root,
        "--ignore-working-copy",
        "log",
        "-r",
        'description(substring:"competitor")',
        "--no-graph",
        "-T",
        'commit_id ++ "\\n"',
    )
    assert landed.strip(), "the competitor's commit is missing"
    if kind == "newcommit":
        assert (repo.root / "comp.txt").read_text() == "competitor bytes\n"
        competitor_commit = observe_commit(jj, repo.root, 'description(exact:"competitor work\\n")')
        shown = jj(repo.root, "--ignore-working-copy", "file", "show", "-r", competitor_commit, "comp.txt")
        assert shown == "competitor bytes\n"
    if code == 0:
        # The guard's work holds. `@` is still the guard's commit, or a descendant of `onto`; the
        # next jj command did not move it, and Git agrees with jj.
        at_after_status = observe_commit(jj, repo.root, "@")
        jj(repo.root, "status")
        assert observe_commit(jj, repo.root, "@") == at_after_status
        assert jj(repo.root, "--ignore-working-copy", "log", "-r", f"{repo.onto}:: & @", "--no-graph", "-T", "commit_id").strip()
        if colocated:
            assert git_out(repo.root, "rev-parse", "HEAD").strip() == observe_commit(jj, repo.root, "@-")
        # The guard's work is in the history: `P`'s tree is under the visible `@`.
        assert (repo.root / "a.txt").read_text() == "a edited in P\n"
        assert observe_commit(jj, repo.root, 'description(exact:"prepared\\n")') == repo.onto


# Limits -----------------------------------------------------------------------------------------


@needs_hooks
def test_limit_a_direct_write_to_a_path_the_checkout_rewrites_is_overwritten(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    gate = Barrier(tmp_path, "AFTER_PUBLISH")
    publisher = start_publish(repo, gate)
    gate.wait_reached()
    (repo.root / "a.txt").write_text("writer bytes written during the checkout window\n")  # P rewrites a.txt
    (repo.root / "keep.txt").write_text("writer edit to an untouched file\n")  # P does not touch it
    (repo.root / "writer-new.txt").write_text("writer new file\n")
    gate.release()
    code, facts = _finish(publisher)
    assert code == 0 and facts["result"] == "published"

    # The rewritten path lost the writer's bytes. They are in no commit. This is the known limit.
    assert (repo.root / "a.txt").read_text() == "a edited in P\n"
    # An untouched tracked file and a new file survive and join `@` at the next snapshot.
    assert (repo.root / "keep.txt").read_text() == "writer edit to an untouched file\n"
    assert (repo.root / "writer-new.txt").read_text() == "writer new file\n"
    summary = jj(repo.root, "diff", "-r", "@", "--summary")  # snapshots
    assert "keep.txt" in summary and "writer-new.txt" in summary and "a.txt" not in summary
    assert "writer bytes" not in jj(repo.root, "--ignore-working-copy", "file", "show", "-r", "@", "a.txt")


@needs_hooks
def test_limit_an_operation_loaded_before_the_guard_publishes_later_as_a_fork(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj, colocated=False)
    loaded_before = op_ids(jj, repo.root)[0]  # a writer that read this head before the guard ran
    publisher = start_publish(repo)
    code, facts = _finish(publisher)
    assert code == 0
    published_wc = facts["wc_commit"]

    # The late writer publishes on its old head without any lock the guard holds.
    jj(repo.root, "--at-operation", loaded_before, "--ignore-working-copy", "new", repo.base, "-m", "late writer")
    assert len(op_heads(repo.root)) == 2  # the log forked

    # The next jj command merges the fork. Both writers' commits survive.
    jj(repo.root, "--ignore-working-copy", "log", "-r", "all()", "--no-graph", "-T", "commit_id")
    assert len(op_heads(repo.root)) == 1
    visible = jj(repo.root, "--ignore-working-copy", "log", "-r", "all()", "--no-graph", "-T", 'commit_id ++ "\\n"')
    assert published_wc in visible
    assert 'late writer' in jj(
        repo.root, "--ignore-working-copy", "log", "-r", "all()", "--no-graph", "-T", 'description ++ "\\n"'
    )


@needs_hooks
def test_limit_raw_git_does_not_take_jjs_git_lock(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    gate = Barrier(tmp_path, "LOCKED")
    publisher = start_publish(repo, gate)
    gate.wait_reached()  # the guard holds jj's Git import/export lock and the working-copy lock

    started = time.monotonic()
    git_out(repo.root, "update-ref", "refs/heads/raw-git-branch", repo.base)  # does not block
    assert time.monotonic() - started < 5
    assert git_out(repo.root, "rev-parse", "refs/heads/raw-git-branch").strip() == repo.base

    gate.release()
    code, _facts = _finish(publisher)
    assert code == 0


def test_cli_writer_in_a_colocated_repo_is_serialized_by_the_git_lock(tmp_path: Path, jj: JjCli) -> None:
    # The jj CLI holds `git_import_export.lock` for its whole command in a colocated repository, and
    # takes it before the working-copy lock. The guard follows that order.
    repo = build_publish_repo(tmp_path, jj)
    proc = subprocess.run(cli_command("publish-if", "--help"), capture_output=True, text=True)
    assert proc.returncode == 0
    from tests.publish_support import run_publish

    result = run_publish(repo)
    assert result.returncode == 0
    assert not (repo.root / ".jj/repo/git_import_export.lock").exists()


def test_limit_divergent_heads_are_merged_before_the_guard_compares(tmp_path: Path, jj: JjCli) -> None:
    # Loading the repository at head merges divergent operation heads, as every jj command does.
    # The merge is an operation that the call publishes before it compares. Then the call proceeds.
    repo = build_publish_repo(tmp_path, jj, colocated=False)
    loaded = op_ids(jj, repo.root)[0]
    jj(repo.root, "--at-operation", loaded, "--ignore-working-copy", "bookmark", "create", "side-a", "-r", repo.base)
    jj(repo.root, "--at-operation", loaded, "--ignore-working-copy", "bookmark", "create", "side-b", "-r", repo.base)
    assert len(op_heads(repo.root)) == 2

    proc = subprocess.run(cli_command("publish-if", "--repo", str(repo.root), "--expect-wc", repo.expected,
                                      "--onto", repo.onto, "-m", "publish smoke"),
                          capture_output=True, text=True)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(op_heads(repo.root)) == 1
    assert op_descriptions(jj, repo.root)[:2] == ["publish smoke", "reconcile divergent operations"]
