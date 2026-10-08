"""Kill ``pyjutsu publish-if`` at each documented point, then recover in a fresh process.

Each test stops the publisher at a named stage with a test-only barrier, sends ``SIGKILL``, and
checks the state a fresh process sees: locks, operation log, operation heads, working-copy files,
and Git. These kills model process death. They do not model power loss.

| Stage            | Landed? | Working copy | Recovery                          |
| ---------------- | ------- | ------------ | --------------------------------- |
| LOCKED           | no      | fresh        | none; retry the call              |
| BEFORE_PUBLISH   | no      | fresh        | none; retry. Orphan op is garbage |
| AFTER_PUBLISH    | yes     | stale (old)  | ``pyjutsu recover``               |
| AFTER_CHECKOUT   | yes     | stale (new)  | ``pyjutsu recover``               |
| AFTER_GIT_SYNC   | yes (2) | stale (new)  | ``pyjutsu recover``               |
| AFTER_FINISH     | yes (2) | fresh        | none                              |
"""

from __future__ import annotations

import subprocess
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
    kill_hard,
    lock_is_free,
    observe_commit,
    op_descriptions,
    op_heads,
    op_ids,
    parse_output,
    run_publish,
    start_publish,
)

pytestmark = pytest.mark.skipif(not HAS_TEST_HOOKS, reason="needs a build with the test-hooks feature")


def _crash_at(repo: PublishRepo, stage: str, tmp_path: Path) -> None:
    barrier = Barrier(tmp_path, stage)
    proc = start_publish(repo, barrier)
    barrier.wait_reached()
    kill_hard(proc)


def _recover(repo: PublishRepo) -> dict[str, str]:
    proc = subprocess.run(cli_command("recover", "--repo", str(repo.root)), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    facts = parse_output(proc.stdout)
    assert facts["result"] == "recovered"
    return facts


def _visible_heads(jj: JjCli, repo: Path) -> list[str]:
    out = jj(repo, "--ignore-working-copy", "log", "-r", "heads(all())", "--no-graph", "-T", 'commit_id ++ "\\n"')
    return [line for line in out.splitlines() if line]


def _assert_recovered(jj: JjCli, repo: PublishRepo) -> None:
    """The end state of every successful publication, reached directly or by recovery."""
    assert (repo.root / "a.txt").read_text() == "a edited in P\n"
    assert (repo.root / "p.txt").read_text() == "prepared\n"
    assert observe_commit(jj, repo.root, "@-") == repo.onto
    if repo.colocated:
        assert git_out(repo.root, "rev-parse", "HEAD").strip() == repo.onto
    assert len(op_heads(repo.root)) == 1
    # Exactly one visible head: `@`. Recovery left no stray commit.
    assert _visible_heads(jj, repo.root) == [observe_commit(jj, repo.root, "@")]
    status = subprocess.run(["jj", "status"], cwd=repo.root, capture_output=True, text=True, env=jj._env())
    assert status.returncode == 0, status.stderr
    assert "stale" not in (status.stdout + status.stderr).lower()


def _assert_locks_free(repo: PublishRepo) -> None:
    assert lock_is_free(repo.root / ".jj/working_copy/working_copy.lock")
    assert lock_is_free(repo.root / ".jj/repo/op_heads/heads/lock")
    assert lock_is_free(repo.root / ".jj/repo/git_import_export.lock")


@pytest.mark.parametrize("stage", ["LOCKED", "BEFORE_PUBLISH"])
@pytest.mark.parametrize("colocated", [True, False])
def test_crash_before_publication_leaves_the_repository_as_it_was(
    tmp_path: Path, jj: JjCli, stage: str, colocated: bool
) -> None:
    repo = build_publish_repo(tmp_path, jj, colocated=colocated)
    ops_before = op_ids(jj, repo.root)
    heads_before = op_heads(repo.root)

    _crash_at(repo, stage, tmp_path)

    # Process death released the locks. Nothing landed. Files and `@` are untouched.
    _assert_locks_free(repo)
    assert op_ids(jj, repo.root) == ops_before
    assert op_heads(repo.root) == heads_before
    assert observe_commit(jj, repo.root, "@") == repo.expected
    assert (repo.root / "a.txt").read_text() == "base\n"
    assert not (repo.root / "p.txt").exists()
    status = subprocess.run(["jj", "status"], cwd=repo.root, capture_output=True, text=True, env=jj._env())
    assert status.returncode == 0 and "stale" not in status.stdout.lower()
    assert op_ids(jj, repo.root) == ops_before  # `status` found nothing to snapshot

    # Recovery has nothing to do, and a retry of the same call succeeds.
    facts = _recover(repo)
    assert facts["operations"] == "" and facts["stale"] == "0"
    retry = run_publish(repo)
    assert retry.returncode == 0, retry.stderr
    _assert_recovered(jj, repo)


def test_crash_after_the_operation_is_written_leaves_only_unreachable_garbage(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj, colocated=False)
    operations = repo.root / ".jj/repo/op_store/operations"
    known = {p.name for p in operations.iterdir()}
    ops_before = op_ids(jj, repo.root)

    _crash_at(repo, "BEFORE_PUBLISH", tmp_path)

    orphans = {p.name for p in operations.iterdir()} - known
    assert len(orphans) == 1  # the written, never-published operation
    assert orphans.isdisjoint(set(op_ids(jj, repo.root)))
    assert op_ids(jj, repo.root) == ops_before


def test_crash_after_publication_before_checkout_leaves_a_stale_working_copy(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    ops_before = op_ids(jj, repo.root)

    _crash_at(repo, "AFTER_PUBLISH", tmp_path)

    _assert_locks_free(repo)
    # The operation landed; the git sync did not run. The disk still holds the old files.
    landed = op_ids(jj, repo.root)
    assert len(landed) == len(ops_before) + 1
    assert op_descriptions(jj, repo.root)[0] == "publish smoke"
    assert len(op_heads(repo.root)) == 1
    assert (repo.root / "a.txt").read_text() == "base\n"
    assert not (repo.root / "p.txt").exists()
    status = subprocess.run(["jj", "status"], cwd=repo.root, capture_output=True, text=True, env=jj._env())
    assert status.returncode != 0 and "stale" in (status.stdout + status.stderr).lower()
    # Git is consistent with jj's view of Git: both still name the old parent.
    assert git_out(repo.root, "rev-parse", "HEAD").strip() == repo.base

    facts = _recover(repo)
    assert facts["stale"] == "0"
    assert len(facts["operations"].split(",")) == 1  # recovery adds the missing Git sync operation
    _assert_recovered(jj, repo)
    assert op_descriptions(jj, repo.root)[:2] == ["sync colocated git", "publish smoke"]
    assert _recover(repo)["operations"] == ""  # idempotent


def test_crash_after_checkout_before_finish_recovers_without_a_stray_commit(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)

    _crash_at(repo, "AFTER_CHECKOUT", tmp_path)

    _assert_locks_free(repo)
    # The files are already new, but the state file still names the old operation.
    assert (repo.root / "p.txt").read_text() == "prepared\n"
    status = subprocess.run(["jj", "status"], cwd=repo.root, capture_output=True, text=True, env=jj._env())
    assert status.returncode != 0 and "stale" in (status.stdout + status.stderr).lower()

    facts = _recover(repo)
    assert facts["stale"] == "0"
    _assert_recovered(jj, repo)


def test_jj_update_stale_after_the_same_crash_is_messy_but_loses_no_data(tmp_path: Path, jj: JjCli) -> None:
    """Document the stock-CLI recovery for the AFTER_CHECKOUT crash.

    `jj workspace update-stale` also ends with the right `@` and the right files. It can print
    that the working copy is not stale and can leave a stray divergent head. `pyjutsu recover`
    never snapshots first, so it avoids that. This test pins what the CLI does so a change shows.
    """
    repo = build_publish_repo(tmp_path, jj)
    _crash_at(repo, "AFTER_CHECKOUT", tmp_path)

    proc = subprocess.run(
        ["jj", "workspace", "update-stale"], cwd=repo.root, capture_output=True, text=True, env=jj._env()
    )

    assert proc.returncode == 0, proc.stderr
    assert observe_commit(jj, repo.root, "@-") == repo.onto
    assert (repo.root / "a.txt").read_text() == "a edited in P\n"
    assert (repo.root / "p.txt").read_text() == "prepared\n"
    # Record the visible heads. A stray head, if any, holds no lost content.
    assert "Attempted recovery, but the working copy is not stale" in proc.stderr
    heads = _visible_heads(jj, repo.root)
    assert observe_commit(jj, repo.root, "@") in heads
    assert len(heads) == 2  # `@` and one stray head from the CLI's own snapshot
    for head in heads:
        diff = jj(repo.root, "--ignore-working-copy", "diff", "-r", head, "--summary")
        assert "keep.txt" not in diff


@pytest.mark.parametrize("stage", ["AFTER_GIT_SYNC", "AFTER_FINISH"])
def test_crash_late_in_the_call(tmp_path: Path, jj: JjCli, stage: str) -> None:
    repo = build_publish_repo(tmp_path, jj)
    ops_before = op_ids(jj, repo.root)

    _crash_at(repo, stage, tmp_path)

    _assert_locks_free(repo)
    # Both operations landed: the publication and the Git synchronization.
    assert op_descriptions(jj, repo.root)[:2] == ["sync colocated git", "publish smoke"]
    assert len(op_ids(jj, repo.root)) == len(ops_before) + 2
    assert git_out(repo.root, "rev-parse", "HEAD").strip() == repo.onto
    facts = _recover(repo)
    assert facts["operations"] == "" and facts["stale"] == "0"
    _assert_recovered(jj, repo)
