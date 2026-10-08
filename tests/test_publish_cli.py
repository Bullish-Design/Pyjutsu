"""The ``pyjutsu publish-if`` and ``pyjutsu recover`` commands: output, exit codes, failures.

The exit codes are 0 success, 1 stale, 2 infrastructure or landed-but-incomplete, 3 usage. The
output is ``key=value`` lines; ``result=`` comes first.
"""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.diff.jj_cli import JjCli
from tests.publish_support import (
    build_publish_repo,
    cli_command,
    console_script,
    git_out,
    observe_commit,
    op_descriptions,
    op_ids,
    parse_output,
    run_publish,
)


def test_console_script_exists_and_publishes(tmp_path: Path, jj: JjCli) -> None:
    script = console_script()
    assert script.is_file(), f"no installed `pyjutsu` executable at {script}"
    repo = build_publish_repo(tmp_path, jj)

    proc = run_publish(repo, script=True)

    assert proc.returncode == 0, proc.stderr
    facts = parse_output(proc.stdout)
    assert next(iter(facts)) == "result"
    assert facts["result"] == "published"
    assert set(facts) == {
        "result",
        "operation",
        "head_operation",
        "wc_commit",
        "onto",
        "expected_wc_commit",
        "git_sync",
        "git_sync_operation",
    }
    assert facts["onto"] == repo.onto
    assert facts["expected_wc_commit"] == repo.expected
    assert facts["wc_commit"] == observe_commit(jj, repo.root, "@")
    assert facts["git_sync"] == "synced"
    assert facts["operation"] in op_ids(jj, repo.root)
    assert facts["head_operation"] == op_ids(jj, repo.root)[0]
    assert (repo.root / "p.txt").read_text() == "prepared\n"


def test_python_dash_m_runs_the_same_command(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    proc = run_publish(repo)  # `python -m pyjutsu`
    assert proc.returncode == 0, proc.stderr
    assert parse_output(proc.stdout)["result"] == "published"


def test_output_without_git_sync_omits_the_git_operation(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj, colocated=False)
    facts = parse_output(run_publish(repo).stdout)
    assert facts["git_sync"] == "not-colocated"
    assert "git_sync_operation" not in facts
    assert facts["head_operation"] == facts["operation"]


def test_stale_exits_1_with_reason_and_observed_ids(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    jj(repo.root, "new", "-m", "foreign")  # moves `@`
    ops_before = op_ids(jj, repo.root)
    observed = observe_commit(jj, repo.root, "@")

    proc = run_publish(repo)

    assert proc.returncode == 1
    facts = parse_output(proc.stdout)
    assert list(facts)[0] == "result"
    assert facts == {
        "result": "stale",
        "reason": "commit-moved",
        "expected_wc_commit": repo.expected,
        "observed_wc_commit": observed,
        "onto": repo.onto,
        "head_operations": ops_before[0],
        "dirty": "0",
    }
    assert op_ids(jj, repo.root) == ops_before


def test_dirty_working_copy_exits_1_and_reports_dirty(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    (repo.root / "a.txt").write_text("direct edit\n")
    proc = run_publish(repo)
    assert proc.returncode == 1
    facts = parse_output(proc.stdout)
    assert facts["reason"] == "dirty-working-copy"
    assert facts["dirty"] == "1"
    assert (repo.root / "a.txt").read_text() == "direct edit\n"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["publish-if"],
        ["publish-if", "--repo", "."],
        ["publish-if", "--repo", ".", "--expect-wc", "a" * 40, "--onto", "b" * 40],  # no -m
        ["recover"],
        ["no-such-command"],
    ],
)
def test_usage_failures_exit_3(argv: list[str]) -> None:
    proc = subprocess.run(cli_command(*argv), capture_output=True, text=True)
    assert proc.returncode == 3
    assert proc.stdout == ""


@pytest.mark.parametrize("bad", ["abc", "@", "main", "A" * 40, "g" * 40, "a" * 39])
@pytest.mark.parametrize("flag", ["--expect-wc", "--onto"])
def test_invalid_commit_ids_exit_3(tmp_path: Path, jj: JjCli, flag: str, bad: str) -> None:
    repo = build_publish_repo(tmp_path, jj)
    good = {"--expect-wc": repo.expected, "--onto": repo.onto}
    good[flag] = bad
    proc = run_publish(repo, expected=good["--expect-wc"], onto=good["--onto"])
    assert proc.returncode == 3
    assert proc.stdout == ""
    assert "full lowercase hex commit id" in proc.stderr


def test_help_exits_0() -> None:
    proc = subprocess.run(cli_command("publish-if", "--help"), capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--expect-wc" in proc.stdout


def test_missing_repository_exits_2(tmp_path: Path) -> None:
    proc = run_publish(tmp_path / "nowhere", "a" * 40, "b" * 40)
    assert proc.returncode == 2
    facts = parse_output(proc.stdout)
    assert facts["result"] == "error"
    assert facts["reason"] == "repo-not-found"


def test_directory_that_is_not_a_workspace_exits_2(tmp_path: Path) -> None:
    empty = tmp_path / "plain"
    empty.mkdir()
    proc = run_publish(empty, "a" * 40, "b" * 40)
    assert proc.returncode == 2
    facts = parse_output(proc.stdout)
    assert facts["result"] == "error"
    assert facts["reason"] == "workspace-load-failed"


def test_unsupported_storage_exits_2_and_names_the_store(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    (repo.root / ".jj/repo/op_heads/type").write_text("lockless_heads_store")
    ops_dir = repo.root / ".jj/repo/op_store/operations"
    before = sorted(p.name for p in ops_dir.iterdir())

    proc = run_publish(repo)

    assert proc.returncode == 2
    facts = parse_output(proc.stdout)
    assert facts["result"] == "error"
    assert facts["reason"] == "workspace-load-failed"
    assert "lockless_heads_store" in facts["message"]
    assert sorted(p.name for p in ops_dir.iterdir()) == before
    assert not (repo.root / "p.txt").exists()


def test_unknown_onto_exits_2(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    proc = run_publish(repo, onto="1" * 40)
    assert proc.returncode == 2
    facts = parse_output(proc.stdout)
    assert facts["result"] == "error"
    assert facts["reason"] == "onto-not-found"


def test_sha1_id_on_the_wrong_length_for_the_store_exits_2(tmp_path: Path, jj: JjCli) -> None:
    # A 64-digit id is valid hex but cannot name a commit in a SHA-1 store. The suite can run
    # with PYJUTSU_TEST_OBJECT_HASH=sha256, so derive the wrong length from the real id.
    repo = build_publish_repo(tmp_path, jj)
    wrong = "a" * (104 - len(repo.expected))  # 40 <-> 64
    proc = run_publish(repo, onto=wrong)
    assert proc.returncode == 2
    assert parse_output(proc.stdout)["reason"] == "invalid-commit-id"


# Post-publication failures ----------------------------------------------------------------------


@contextmanager
def _read_only(path: Path) -> Iterator[None]:
    mode = stat.S_IMODE(path.stat().st_mode)
    path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    try:
        yield
    finally:
        path.chmod(mode)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_checkout_failure_after_publication_is_incomplete_not_stale(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    ops_before = op_ids(jj, repo.root)

    with _read_only(repo.root):  # the checkout cannot create `p.txt` or rewrite `a.txt`
        proc = run_publish(repo)

    # Exit 2 and `result=incomplete`: never the exit-1 "nothing happened" answer.
    assert proc.returncode == 2
    facts = parse_output(proc.stdout)
    assert facts["result"] == "incomplete"
    assert facts["reason"] == "published-checkout-failed"
    assert facts["stage"] == "checkout"
    assert facts["recovery"] == f"pyjutsu recover --repo {repo.root}"
    assert facts["operation"] == op_ids(jj, repo.root)[0]
    assert len(op_ids(jj, repo.root)) == len(ops_before) + 1  # the operation landed
    assert op_descriptions(jj, repo.root)[0] == "publish smoke"
    assert "recover" in proc.stderr

    # The working copy is stale; its files still hold the old tree.
    assert (repo.root / "a.txt").read_text() == "base\n"
    assert not (repo.root / "p.txt").exists()
    status = subprocess.run(["jj", "status"], cwd=repo.root, capture_output=True, text=True, env=jj._env())
    assert status.returncode != 0 and "stale" in (status.stdout + status.stderr).lower()

    # The recovery procedure finishes the job.
    recovered = subprocess.run(cli_command("recover", "--repo", str(repo.root)), capture_output=True, text=True)
    assert recovered.returncode == 0, recovered.stderr
    assert parse_output(recovered.stdout)["result"] == "recovered"
    assert parse_output(recovered.stdout)["stale"] == "0"
    assert (repo.root / "a.txt").read_text() == "a edited in P\n"
    assert (repo.root / "p.txt").read_text() == "prepared\n"
    assert observe_commit(jj, repo.root, "@-") == repo.onto
    assert git_out(repo.root, "rev-parse", "HEAD").strip() == repo.onto
    jj(repo.root, "status")  # works: nothing is stale


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_git_sync_failure_after_publication_is_incomplete_and_recoverable(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    git_dir = repo.root / ".git"

    with _read_only(git_dir):  # Git cannot lock `HEAD` or `index`
        proc = run_publish(repo)

    assert proc.returncode == 2
    facts = parse_output(proc.stdout)
    assert facts["result"] == "incomplete"
    assert facts["stage"] == "git-sync"
    assert facts["reason"] == "published-git-sync-failed"
    # The jj working copy is current: the files are new and `jj status` works.
    assert (repo.root / "p.txt").read_text() == "prepared\n"
    assert observe_commit(jj, repo.root, "@-") == repo.onto
    jj(repo.root, "--ignore-working-copy", "status")
    # Git lags: HEAD still names the old parent.
    assert git_out(repo.root, "rev-parse", "HEAD").strip() == repo.base

    recovered = subprocess.run(cli_command("recover", "--repo", str(repo.root)), capture_output=True, text=True)
    assert recovered.returncode == 0, recovered.stderr
    assert git_out(repo.root, "rev-parse", "HEAD").strip() == repo.onto
    assert git_out(repo.root, "status", "--porcelain").strip() == ""


def test_recover_is_a_noop_on_a_healthy_repository(tmp_path: Path, jj: JjCli) -> None:
    repo = build_publish_repo(tmp_path, jj)
    assert run_publish(repo).returncode == 0
    ops_before = op_ids(jj, repo.root)
    proc = subprocess.run(cli_command("recover", "--repo", str(repo.root)), capture_output=True, text=True)
    assert proc.returncode == 0
    assert parse_output(proc.stdout) == {"result": "recovered", "operations": "", "stale": "0"}
    assert op_ids(jj, repo.root) == ops_before
