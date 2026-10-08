"""Shared helpers for the guarded-publication tests (``publish_if`` and ``pyjutsu publish-if``).

Every test builds a disposable repository with the pinned ``jj`` CLI. The repository has a base
commit, a prepared commit ``P`` (a sibling of ``@``'s parent that adds ``p.txt`` and edits
``a.txt``), and an empty ``@`` on the base. The tests publish ``P``'s tree as the new ``@``.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pyjutsu
from pyjutsu import _pyjutsu

from tests.diff.jj_cli import JjCli

HAS_TEST_HOOKS = _pyjutsu.has_test_hooks()

#: The exact text of the prepared commit's description. Tests find ``P`` with it.
PREPARED = "prepared"


@dataclass
class PublishRepo:
    root: Path
    #: The working-copy commit id the caller expects (the empty ``@`` on the base).
    expected: str
    #: The prepared commit ``P``.
    onto: str
    #: The base commit.
    base: str
    colocated: bool


def build_publish_repo(tmp_path: Path, jj: JjCli, *, colocated: bool = True, name: str = "repo") -> PublishRepo:
    repo = tmp_path / name
    repo.mkdir()
    if colocated:
        jj.init_colocated(repo)
    else:
        jj(repo, "git", "init", "--no-colocate")
    (repo / "a.txt").write_text("base\n")
    (repo / "keep.txt").write_text("kept by every commit\n")
    jj(repo, "describe", "-m", "base")
    jj(repo, "new", "-m", PREPARED)
    (repo / "a.txt").write_text("a edited in P\n")
    (repo / "p.txt").write_text("prepared\n")
    base = jj.commit_id(repo, 'description(exact:"base\\n")')
    jj(repo, "new", base)
    onto = jj.commit_id(repo, f'description(exact:"{PREPARED}\\n")')
    expected = observe_commit(jj, repo, "@")
    return PublishRepo(root=repo, expected=expected, onto=onto, base=base, colocated=colocated)


def observe_commit(jj: JjCli, repo: Path, revset: str) -> str:
    """A commit id read without a snapshot, so the observation never moves ``@``."""
    return jj(
        repo, "--ignore-working-copy", "log", "-r", revset, "--no-graph", "-T", "commit_id"
    ).strip()


def op_ids(jj: JjCli, repo: Path) -> list[str]:
    """Operation ids, newest first, read without a snapshot."""
    out = jj(repo, "--ignore-working-copy", "op", "log", "--no-graph", "-T", 'id ++ "\\n"')
    return [line for line in out.splitlines() if line]


def op_descriptions(jj: JjCli, repo: Path) -> list[str]:
    out = jj(
        repo,
        "--ignore-working-copy",
        "op",
        "log",
        "--no-graph",
        "-T",
        'description.first_line() ++ "\\n"',
    )
    return [line for line in out.splitlines() if line]


def op_heads(repo: Path) -> list[str]:
    """The operation head ids on disk."""
    return sorted(p.name for p in (repo / ".jj/repo/op_heads/heads").iterdir())


def tracked_files(jj: JjCli, repo: Path, revset: str) -> set[str]:
    out = jj(repo, "--ignore-working-copy", "file", "list", "-r", revset)
    return {line for line in out.splitlines() if line}


def tree_diff_is_empty(jj: JjCli, repo: Path, left: str, right: str) -> bool:
    out = jj(repo, "--ignore-working-copy", "diff", "--from", left, "--to", right, "--summary")
    return not out.strip()


def git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def git_index_paths(repo: Path) -> set[str]:
    return {line for line in git_out(repo, "ls-files").splitlines() if line}


def parse_output(stdout: str) -> dict[str, str]:
    """Parse the ``key=value`` lines of a ``pyjutsu`` command."""
    facts: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        assert sep, f"output line without '=': {line!r}"
        facts[key] = value
    return facts


def cli_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "pyjutsu", *args]


def console_script() -> Path:
    """The installed ``pyjutsu`` executable, next to the interpreter that runs the tests."""
    return Path(sys.executable).parent / "pyjutsu"


def run_publish(
    repo: PublishRepo | Path,
    expected: str | None = None,
    onto: str | None = None,
    message: str = "publish smoke",
    *,
    env: dict[str, str] | None = None,
    script: bool = False,
) -> subprocess.CompletedProcess[str]:
    root = repo.root if isinstance(repo, PublishRepo) else repo
    if isinstance(repo, PublishRepo):
        expected = repo.expected if expected is None else expected
        onto = repo.onto if onto is None else onto
    assert expected is not None and onto is not None
    base = [str(console_script())] if script else cli_command()
    return subprocess.run(
        [*base, "publish-if", "--repo", str(root), "--expect-wc", expected, "--onto", onto, "-m", message],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def load(repo: PublishRepo) -> pyjutsu.Workspace:
    return pyjutsu.Workspace.load(repo.root)


def lock_is_free(path: Path) -> bool:
    """Whether nobody holds an ``flock`` on ``path``. jj unlinks its lock files on a clean release."""
    if not path.exists():
        return True
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


class Barrier:
    """A FIFO pair that stops a ``publish-if`` process at one named stage.

    The process signals when it reaches the stage, then waits for ``release``. A test can run code
    while the process holds its locks, or kill it there. Needs a build with ``test-hooks``.
    """

    def __init__(self, directory: Path, stage: str) -> None:
        self.stage = stage
        self.signal_path = directory / f"signal-{stage}"
        self.wait_path = directory / f"wait-{stage}"
        os.mkfifo(self.signal_path)
        os.mkfifo(self.wait_path)
        self._reached = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        with open(self.signal_path, "rb") as fifo:
            fifo.read(1)
        self._reached.set()

    @property
    def env(self) -> dict[str, str]:
        return {
            f"PJ_SIGNAL_{self.stage}": str(self.signal_path),
            f"PJ_WAIT_{self.stage}": str(self.wait_path),
        }

    def wait_reached(self, timeout: float = 60.0) -> None:
        assert self._reached.wait(timeout), f"publish-if never reached {self.stage}"

    def release(self) -> None:
        with open(self.wait_path, "wb") as fifo:
            fifo.write(b"x")


def start_publish(
    repo: PublishRepo, *barriers: Barrier, message: str = "publish smoke", extra_env: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    env = {**os.environ, **(extra_env or {})}
    for barrier in barriers:
        env.update(barrier.env)
    return subprocess.Popen(
        cli_command(
            "publish-if", "--repo", str(repo.root), "--expect-wc", repo.expected, "--onto", repo.onto, "-m", message
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def kill_hard(proc: subprocess.Popen[str]) -> None:
    proc.send_signal(signal.SIGKILL)
    proc.communicate(timeout=30)


def run_jj_in_background(
    jj: JjCli, repo: Path, *args: str
) -> tuple[subprocess.Popen[str], float]:
    proc = subprocess.Popen(
        ["jj", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=jj._env(),
    )
    return proc, time.monotonic()
