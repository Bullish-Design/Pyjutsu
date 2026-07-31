"""Hooks: the in-process registry, declarative config, adapters, and the transaction/push wiring.

Pure-Python tests (registry, config parsing, argv builders) run without a repo; the wiring tests
use the standard ``scratch_repo``/``bookmarked_repo`` fixtures so pre/post-commit and pre/post-push
are exercised end-to-end against real jj operations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pyjutsu
import pytest
from pyjutsu.errors import HookAbort, PostHookError
from pyjutsu.hooks import HookRegistry, run_pre_commit, run_prek

# A dotted-path hook target used by the declarative-config tests (importable as tests.test_hooks).
_LAST_OPERATION: str | None = None


def _noop_hook(*args: object, **kwargs: object) -> None:
    pass


def _record_operation(operation_id: str, description: str) -> None:
    global _LAST_OPERATION
    _LAST_OPERATION = operation_id


# ---- registry (pure Python, no repo) ----------------------------------------


def test_registry_zero_hooks_is_a_noop() -> None:
    r = HookRegistry()
    r.fire("pre-commit")  # must not raise and must not allocate anything meaningful
    assert r.count() == 0
    assert r.count("pre-commit") == 0
    assert r.events() == []


def test_registry_add_remove_order_and_decorator() -> None:
    r = HookRegistry()
    calls: list[str] = []

    def a() -> None:
        calls.append("a")

    def b() -> None:
        calls.append("b")

    r.add("pre-commit", a)
    assert r.remove("pre-commit", a) is True
    assert r.remove("pre-commit", a) is False  # idempotent
    r.add("pre-commit", a)
    r.add("pre-commit", b)
    r.fire("pre-commit")
    assert calls == ["a", "b"]  # registration order

    @r.on("post-commit")
    def c(operation_id: str) -> None:
        calls.append(operation_id)

    r.fire("post-commit", "op123")
    assert calls[-1] == "op123"
    assert r.count("post-commit") == 1

    r.clear("pre-commit")
    assert r.count("pre-commit") == 0
    r.clear()
    assert r.count() == 0


def test_registry_context_manager_registers_for_the_block() -> None:
    r = HookRegistry()
    calls: list[int] = []

    def h() -> None:
        calls.append(1)

    with r("pre-commit", h):
        r.fire("pre-commit")
        assert r.count("pre-commit") == 1
    r.fire("pre-commit")
    assert calls == [1]  # fired once: inside the block only


def test_registry_runs_all_hooks_and_reports_all_failures_by_default() -> None:
    # fail_fast=False is the default (pre-commit parity): every hook runs, then the first failure
    # is raised with the rest attached as notes.
    r = HookRegistry()
    calls: list[str] = []

    def boom() -> None:
        raise ValueError("first failure")

    def also_boom() -> None:
        raise RuntimeError("second failure")

    def after() -> None:
        calls.append("after")

    r.add("pre-commit", boom)
    r.add("pre-commit", also_boom)
    r.add("pre-commit", after)
    with pytest.raises(ValueError, match="first failure") as ei:
        r.fire("pre-commit")
    assert calls == ["after"]  # every hook ran despite the earlier failures
    assert any("also failed" in note for note in ei.value.__notes__)


def test_registry_fail_fast_stops_at_the_first_failure() -> None:
    r = HookRegistry(fail_fast=True)
    calls: list[str] = []

    def boom() -> None:
        raise ValueError("nope")

    def after() -> None:
        calls.append("after")

    r.add("pre-commit", boom)
    r.add("pre-commit", after)
    with pytest.raises(ValueError, match="nope"):
        r.fire("pre-commit")
    assert calls == []  # short-circuited: the second hook never ran
    # An explicit per-fire override also works.
    with pytest.raises(ValueError):
        r.fire("pre-commit", fail_fast=False)
    assert calls == ["after"]


def test_registry_rejects_non_callable() -> None:
    r = HookRegistry()
    with pytest.raises(TypeError):
        r.add("pre-commit", "not a callable")  # type: ignore[arg-type]


# ---- declarative config -----------------------------------------------------


def test_load_config_commands_and_python_hooks(tmp_path: Path) -> None:
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / ".pyjutsu-hooks.toml"
    cfg.write_text(
        "[hooks.pre-commit]\n"
        'commands = [{ command = "true" }]\n'
        f'python = ["{__name__}:_noop_hook"]\n'
    )
    assert r.load_config(cfg) == 2
    r.fire("pre-commit")  # both the command and the in-process callable run clean


def test_load_config_rejects_unknown_event_and_keys(tmp_path: Path) -> None:
    r = HookRegistry()
    cfg = tmp_path / "c.toml"
    cfg.write_text('[hooks.pre-committ]\ncommands = [{ command = "true" }]\n')
    with pytest.raises(pyjutsu.PyjutsuError, match="unknown hook event"):
        r.load_config(cfg)
    cfg.write_text('[hooks.pre-commit]\nstages = ["pre-commit"]\n')
    with pytest.raises(pyjutsu.PyjutsuError, match="unknown key"):
        r.load_config(cfg)
    cfg.write_text('[hooks.pre-commit]\ncommands = [{ files = "[" }]\n')
    with pytest.raises(pyjutsu.PyjutsuError, match="regex"):
        r.load_config(cfg)


def test_command_hook_nonzero_exit_and_missing_binary(tmp_path: Path) -> None:
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text('[hooks.pre-commit]\ncommands = [{ command = "false" }]\n')
    r.load_config(cfg)
    with pytest.raises(HookAbort, match="exited 1"):
        r.fire("pre-commit")

    # A fresh registry: re-loading into the same one would stack both hooks.
    r = HookRegistry(root=tmp_path)
    cfg.write_text(
        '[hooks.pre-commit]\ncommands = [{ command = "definitely-not-a-real-binary-xyz" }]\n'
    )
    r.load_config(cfg)
    with pytest.raises(HookAbort, match="not found"):
        r.fire("pre-commit")


def test_load_config_missing_file_and_bad_toml_raise_pyjutsu_error(tmp_path: Path) -> None:
    r = HookRegistry()
    with pytest.raises(pyjutsu.PyjutsuError, match="cannot read hook config"):
        r.load_config(tmp_path / "absent.toml")
    cfg = tmp_path / "bad.toml"
    cfg.write_text("not-toml = [\n")
    with pytest.raises(pyjutsu.PyjutsuError, match="invalid TOML"):
        r.load_config(cfg)


def _argv_probe(tmp_path: Path) -> tuple[Path, Path, str]:
    """A tiny script that writes its argv (after the out-file arg) to ``out``; returns the pieces."""
    out = tmp_path / "args.txt"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('\\n'.join(sys.argv[2:]))\n"
    )
    return out, probe, sys.executable


def test_command_hook_files_exclude_filter(tmp_path: Path) -> None:
    out, probe, python = _argv_probe(tmp_path)
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / "c.toml"
    # TOML literal strings (single quotes) avoid backslash-escaping the regex.
    cfg.write_text(
        "[hooks.pre-commit]\n"
        f'commands = [{{ command = ["{python}", "{probe}", "{out}"], '
        f'files = \'\\.py$\', exclude = \'generated\' }}]\n'
    )
    r.load_config(cfg)
    r.fire("pre-commit", paths=["a.py", "b.txt", "generated/x.py", "c.py"])
    assert out.read_text().splitlines() == ["a.py", "c.py"]


def test_command_hook_skips_when_filters_match_nothing(tmp_path: Path) -> None:
    out, probe, python = _argv_probe(tmp_path)
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[hooks.pre-commit]\n"
        f'commands = [{{ command = ["{python}", "{probe}", "{out}"], files = \'\.py$\' }}]\n'
    )
    r.load_config(cfg)
    r.fire("pre-commit", paths=["README.md", "docs/x.txt"])
    assert not out.exists()  # no .py paths → the hook was skipped entirely (pre-commit semantics)
    r.fire("pre-commit", paths=["a.py"])
    assert out.read_text().splitlines() == ["a.py"]


def test_command_hook_without_filter_passes_all_paths(tmp_path: Path) -> None:
    out, probe, python = _argv_probe(tmp_path)
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[hooks.pre-commit]\n"
        f'commands = [{{ command = ["{python}", "{probe}", "{out}"] }}]\n'
    )
    r.load_config(cfg)
    r.fire("pre-commit", paths=["a.py", "b.txt"])
    assert out.read_text().splitlines() == ["a.py", "b.txt"]
    # An event that provides no path list (like pre-commit today) runs the command as configured.
    out.write_text("")
    r.fire("pre-commit")
    assert out.read_text().splitlines() == []


# ---- adapters ----------------------------------------------------------------


def test_run_prek_and_pre_commit_argv() -> None:
    assert run_prek("pre-commit") == ["prek", "run", "--all-files"]
    assert run_prek("post-commit") == ["prek", "run", "--all-files", "--hook-stage", "post-commit"]
    assert run_prek("pre-push") == ["prek", "run", "--all-files", "--hook-stage", "pre-push"]
    assert run_prek("pre-commit", mode="staged") == ["prek", "run"]
    with pytest.raises(pyjutsu.PyjutsuError, match="no 'post-push' stage"):
        run_prek("post-push")
    assert run_pre_commit("pre-commit") == ["pre-commit", "run", "--all-files"]
    assert run_pre_commit("post-commit", binary="/usr/bin/pre-commit") == [
        "/usr/bin/pre-commit",
        "run",
        "--all-files",
        "--hook-stage",
        "post-commit",
    ]


def test_adapter_in_config_runs_the_binary(tmp_path: Path) -> None:
    # A fake `prek` on the filesystem: records argv, exits 0.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "argv.txt"
    fake = bin_dir / "prek"
    fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {record}\nexit 0\n")
    fake.chmod(0o755)
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        f'[hooks.pre-commit]\nadapters = [{{ name = "run_prek", binary = "{fake}" }}]\n'
    )
    assert r.load_config(cfg) == 1
    r.fire("pre-commit")
    assert record.read_text().splitlines() == ["run", "--all-files"]


def test_adapter_failure_vetoes(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "prek"
    fake.write_text("#!/bin/sh\nexit 3\n")
    fake.chmod(0o755)
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        f'[hooks.pre-commit]\nadapters = [{{ name = "run_prek", binary = "{fake}" }}]\n'
    )
    r.load_config(cfg)
    with pytest.raises(HookAbort, match="exited 3"):
        r.fire("pre-commit")


def test_config_rejects_unknown_adapter(tmp_path: Path) -> None:
    r = HookRegistry()
    cfg = tmp_path / "c.toml"
    cfg.write_text('[hooks.pre-commit]\nadapters = ["pre-commit"]\n')
    with pytest.raises(pyjutsu.PyjutsuError, match="unknown adapter"):
        r.load_config(cfg)


# ---- pre-commit-parity knobs ------------------------------------------------


def test_command_hook_pass_filenames_false(tmp_path: Path) -> None:
    out, probe, python = _argv_probe(tmp_path)
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[hooks.pre-commit]\n"
        f'commands = [{{ command = ["{python}", "{probe}", "{out}"], pass_filenames = false }}]\n'
    )
    r.load_config(cfg)
    r.fire("pre-commit", paths=["a.py", "b.txt"])
    assert out.read_text().splitlines() == []  # no filenames were appended to argv


def test_command_hook_env(tmp_path: Path) -> None:
    out = tmp_path / "env.txt"
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[hooks.pre-commit]\n"
        f'commands = [{{ command = ["sh", "-c", "echo $PYJUTSU_HOOK_TEST > {out}"], '
        f'env = {{ PYJUTSU_HOOK_TEST = "from-config" }} }}]\n'
    )
    r.load_config(cfg)
    r.fire("pre-commit")
    assert out.read_text().strip() == "from-config"  # the env table reached the child process


def test_config_fail_fast_false_runs_all_then_raises(tmp_path: Path) -> None:
    out, probe, python = _argv_probe(tmp_path)
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "fail_fast = false\n"
        "[hooks.pre-commit]\n"
        f'commands = [{{ command = "false" }}, {{ command = ["{python}", "{probe}", "{out}"] }}]\n'
    )
    r.load_config(cfg)
    with pytest.raises(HookAbort, match="exited 1"):
        r.fire("pre-commit")
    assert out.exists()  # the second (recording) hook DID run despite the first's failure


def test_config_fail_fast_true_stops_at_first_failure(tmp_path: Path) -> None:
    out, probe, python = _argv_probe(tmp_path)
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "fail_fast = true\n"
        "[hooks.pre-commit]\n"
        f'commands = [{{ command = "false" }}, {{ command = ["{python}", "{probe}", "{out}"] }}]\n'
    )
    r.load_config(cfg)
    with pytest.raises(HookAbort, match="exited 1"):
        r.fire("pre-commit")
    assert not out.exists()  # the second hook never ran


def test_config_validates_new_keys(tmp_path: Path) -> None:
    r = HookRegistry()
    cfg = tmp_path / "c.toml"
    cfg.write_text('fail_fast = "yes"\n')
    with pytest.raises(pyjutsu.PyjutsuError, match="fail_fast"):
        r.load_config(cfg)
    cfg.write_text('on_post_failure = "scream"\n')
    with pytest.raises(pyjutsu.PyjutsuError, match="on_post_failure"):
        r.load_config(cfg)
    cfg.write_text('[hooks.pre-commit]\ncommands = [{ command = "true", env = { FOO = 1 } }]\n')
    with pytest.raises(pyjutsu.PyjutsuError, match="env"):
        r.load_config(cfg)
    cfg.write_text('[hooks.pre-commit]\ncommands = [{ command = "true", pass_filenames = "no" }]\n')
    with pytest.raises(pyjutsu.PyjutsuError, match="pass_filenames"):
        r.load_config(cfg)


# ---- post-hook failure policy ------------------------------------------------


def test_post_commit_warn_policy_still_commits(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")

    def bad(operation_id: str, description: str) -> None:
        raise RuntimeError("notify failed")

    ws.hooks.add("post-commit", bad)
    ws.hooks.on_post_failure = "warn"
    with pytest.warns(UserWarning, match="post-commit hook failed") as record:
        with ws.transaction("op that lands", auto_snapshot=False):
            pass
    # The operation landed despite the hook failure; the failure is a warning, not an error.
    assert ws.head_operation()
    assert any("operation" in str(w.message) for w in record)  # the op id is in the warning


def test_config_on_post_failure_warn(scratch_repo: Path, tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        'on_post_failure = "warn"\n'
        "[hooks.post-commit]\n"
        'commands = [{ command = "false" }]\n'
    )
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    ws.load_config_hooks(cfg)
    assert ws.hooks.on_post_failure == "warn"
    with pytest.warns(UserWarning, match="post-commit hook failed"):
        with ws.transaction("lands anyway", auto_snapshot=False):
            pass
    assert ws.head_operation()


def test_post_export_warn_policy(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")

    def bad(operation: pyjutsu.Operation | None) -> None:
        raise RuntimeError("boom")

    ws.hooks.add("post-export", bad)
    ws.hooks.on_post_failure = "warn"
    with pytest.warns(UserWarning, match="post-export hook failed"):
        result = ws.git_export()
    assert result is None or isinstance(result, pyjutsu.Operation)


# ---- hooks_config on init/git_clone ------------------------------------------


def test_init_with_hooks_config(tmp_path: Path) -> None:
    (tmp_path / "r").mkdir()
    cfg = tmp_path / "h.toml"
    cfg.write_text('[hooks.pre-commit]\ncommands = [{ command = "true" }]\n')
    ws = pyjutsu.Workspace.init(str(tmp_path / "r"), hooks_config=str(cfg))
    assert ws.hooks.count() == 1


def test_init_hooks_config_off(tmp_path: Path) -> None:
    (tmp_path / "r").mkdir()
    ws = pyjutsu.Workspace.init(str(tmp_path / "r"), hooks_config="off")
    assert ws.hooks.count() == 0


# ---- performance & threading --------------------------------------------------


def test_fire_with_no_hooks_is_effectively_free() -> None:
    import time

    r = HookRegistry()
    start = time.perf_counter()
    for _ in range(200_000):
        r.fire("pre-commit")
    elapsed = time.perf_counter() - start
    # Typically ~100 ns per fire (a dict lookup over an empty tuple); the 5 µs/fire ceiling
    # leaves 20x+ headroom against slow CI, so this only trips on a real regression (e.g. an
    # accidental per-fire allocation or subprocess in the no-hook path).
    assert elapsed < 1.0


def test_hooks_fire_on_the_worker_thread_inside_to_thread(scratch_repo: Path) -> None:
    import asyncio
    import threading

    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    main_thread = threading.get_ident()
    seen: dict[str, object] = {}

    def post(operation_id: str, description: str) -> None:
        seen["thread"] = threading.get_ident()
        seen["id"] = operation_id

    ws.hooks.add("post-commit", post)

    def commit() -> None:
        with ws.transaction("threaded", auto_snapshot=False):
            pass

    asyncio.run(asyncio.to_thread(commit))
    assert seen["id"] == ws.head_operation()
    assert seen["thread"] != main_thread  # the hook ran on the worker thread, not the event loop


# ---- wiring: pre/post-commit on transactions ---------------------------------


def test_pre_commit_hook_mutates_before_publish(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    calls: list[str] = []
    seen_paths: list[object] = []

    def add_trailer(tx: pyjutsu.Transaction, *, paths: list[str] | None = None) -> None:
        calls.append("pre")
        seen_paths.append(paths)
        tx.describe("@", "hooked")

    ws.hooks.add("pre-commit", add_trailer)
    with ws.transaction("plain describe", auto_snapshot=False) as tx:
        tx.describe("@", "original")
    # The pre-commit hook rewrote the pending commit before publish: the landed description is the
    # hook's, not the body's — the in-process hook mutated the real transaction (no JSON round-trip).
    assert ws.working_copy().description.rstrip("\n") == "hooked"
    assert calls == ["pre"]
    # A description-only pending commit changes no paths: the hook got an empty list.
    assert seen_paths == [[]]


def test_pre_commit_veto_rolls_back_and_workspace_recovers(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    before = ws.head_operation()

    def veto(tx: pyjutsu.Transaction, *, paths: list[str] | None = None) -> None:
        raise HookAbort("no WIP commits")

    with ws.hooks("pre-commit", veto):  # registered for this block only
        with pytest.raises(HookAbort, match="no WIP"):
            with ws.transaction("should not land", auto_snapshot=False) as tx:
                tx.describe("@", "will not land")
    assert ws.head_operation() == before  # nothing published
    assert ws.working_copy().description.rstrip("\n") != "will not land"

    # The rolled-back transaction released the workspace's single-tx slot — and the hook was
    # unregistered when the `with ws.hooks(...)` block exited — so a fresh transaction works.
    with ws.transaction("after", auto_snapshot=False):
        pass
    assert ws.head_operation() != before


def test_pre_commit_hook_bug_is_wrapped_and_rolls_back(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    before = ws.head_operation()

    def buggy(tx: pyjutsu.Transaction, *, paths: list[str] | None = None) -> None:
        raise ValueError("hook bug")

    ws.hooks.add("pre-commit", buggy)
    with pytest.raises(HookAbort, match="pre-commit hook failed"):
        with ws.transaction("nope", auto_snapshot=False):
            pass
    assert ws.head_operation() == before


def test_pre_commit_python_hook_receives_pending_paths(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo, hooks_config="off")
    seen: dict[str, object] = {}

    def capture(tx: pyjutsu.Transaction, *, paths: list[str] | None = None) -> None:
        seen["paths"] = paths

    ws.hooks.add("pre-commit", capture)
    with ws.transaction("restore @ to A", auto_snapshot=False) as tx:
        tx.restore("@", from_="@---")
    # The pending commit's change is {b.txt, c.txt} removed (restore wiped them) — the hook sees
    # those, not the pre-tx state.
    assert sorted(seen["paths"]) == ["b.txt", "c.txt"]  # type: ignore[arg-type]


def test_pre_commit_command_hook_filters_pending_paths(
    linear_repo: Path, tmp_path: Path
) -> None:
    """End to end: __exit__ computes the pending paths, the command hook filters them, and only
    the matching ones reach the process argv."""
    out, probe, python = _argv_probe(tmp_path)
    ws = pyjutsu.Workspace.load(linear_repo, hooks_config="off")
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[hooks.pre-commit]\n"
        f'commands = [{{ command = ["{python}", "{probe}", "{out}"], '
        f'files = \'b\\.txt$\' }}]\n'
    )
    ws.load_config_hooks(cfg)
    with ws.transaction("restore @ to A", auto_snapshot=False) as tx:
        tx.restore("@", from_="@---")
    # Pending paths are [b.txt, c.txt]; the files filter keeps b.txt only.
    assert out.read_text().splitlines() == ["b.txt"]


def test_pre_commit_paths_not_computed_without_hooks(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The zero-cost guarantee: with no pre-commit hooks registered, the pending diff is never
    computed (the wiring short-circuits before calling changed_paths)."""
    import pyjutsu.transaction as transaction_mod

    calls: list[str] = []

    def boom(self: object, commit: str) -> list[str]:
        calls.append(commit)
        raise AssertionError("changed_paths must not run with no pre-commit hooks registered")

    monkeypatch.setattr(transaction_mod.Transaction, "changed_paths", boom)
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    with ws.transaction("no hooks", auto_snapshot=False):
        pass
    assert calls == []


def test_tx_changed_paths_sees_pending_rewrites(linear_repo: Path) -> None:
    """tx.changed_paths reads the OPEN transaction; the read surface (op-log based) cannot."""
    ws = pyjutsu.Workspace.load(linear_repo, hooks_config="off")
    with ws.transaction("restore @ to A", auto_snapshot=False) as tx:
        tx.restore("@", from_="@---")  # @'s tree becomes A's ({a.txt}); parent C has {a,b,c}
        assert sorted(tx.changed_paths("@")) == ["b.txt", "c.txt"]
        # The read surface still resolves @ from the op log: the pending restore is invisible.
        assert [f.path for f in ws.diff("@").files] == []
    # After commit, the read surface agrees with what the transaction predicted.
    assert sorted(f.path for f in ws.diff("@").files) == ["b.txt", "c.txt"]


def test_tx_changed_paths_for_specific_commits(linear_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(linear_repo, hooks_config="off")
    with ws.transaction("look", auto_snapshot=False) as tx:
        assert tx.changed_paths("@") == []  # empty @ introduces nothing
        assert sorted(tx.changed_paths("@-")) == ["c.txt"]  # C added c.txt
        assert sorted(tx.changed_paths("@--")) == ["b.txt"]  # B added b.txt


def test_tx_changed_paths_revset_errors(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    with ws.transaction("bad", auto_snapshot=False) as tx:
        with pytest.raises(pyjutsu.RevsetError):
            tx.changed_paths("nonexistent()")
        with pytest.raises(pyjutsu.RevsetError):
            tx.changed_paths("all()")


def test_post_commit_hook_receives_operation_id(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    seen: dict[str, str] = {}

    def post(operation_id: str, description: str) -> None:
        seen["id"] = operation_id
        seen["description"] = description

    ws.hooks.add("post-commit", post)
    with ws.transaction("hooked op", auto_snapshot=False):
        pass
    assert seen["id"] == ws.head_operation()
    assert seen["description"] == "hooked op"


def test_post_commit_hook_failure_raises_posthookerror_with_published_op(
    scratch_repo: Path,
) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    before = ws.head_operation()

    def bad(operation_id: str, description: str) -> None:
        raise RuntimeError("notify failed")

    ws.hooks.add("post-commit", bad)
    with pytest.raises(PostHookError) as ei:
        with ws.transaction("op that lands", auto_snapshot=False):
            pass
    # The operation WAS published; the error says "hook failed" and carries its id.
    assert ws.head_operation() != before
    assert ei.value.operation_id == ws.head_operation()
    assert "post-commit" in str(ei.value)


# ---- wiring: pre/post-push on git_push ----------------------------------------


def test_pre_push_hook_vetoes_before_any_git_call(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    seen: list[tuple[str, list[str]]] = []

    def veto(remote: str, bookmarks: list[str]) -> None:
        seen.append((remote, bookmarks))
        raise HookAbort("pushing is frozen")

    ws.hooks.add("pre-push", veto)
    # The hook fires before the native push: the veto wins even though the remote doesn't exist.
    with pytest.raises(HookAbort, match="frozen"):
        ws.git_push("origin", "main")
    assert seen == [("origin", ["main"])]


def test_post_push_hook_receives_operation(bookmarked_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(bookmarked_repo, hooks_config="off")
    with ws.transaction("add bookmark", auto_snapshot=False) as tx:
        tx.set_bookmark("newone", "@")
    seen: dict[str, object] = {}

    def post(operation: pyjutsu.Operation | None, remote: str) -> None:
        seen["op"] = operation
        seen["remote"] = remote

    ws.hooks.add("post-push", post)
    ws.git_push("origin", "newone", allow_new=True)
    assert seen["remote"] == "origin"
    assert isinstance(seen["op"], pyjutsu.Operation)
    assert seen["op"].id == ws.head_operation()  # type: ignore[union-attr]


def test_post_push_hook_failure_raises_posthookerror(bookmarked_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(bookmarked_repo, hooks_config="off")
    with ws.transaction("add bookmark", auto_snapshot=False) as tx:
        tx.set_bookmark("second", "@")

    def bad(operation: pyjutsu.Operation | None, remote: str) -> None:
        raise RuntimeError("slack down")

    ws.hooks.add("post-push", bad)
    with pytest.raises(PostHookError, match="post-push hook failed"):
        ws.git_push("origin", "second", allow_new=True)


# ---- wiring: every other mutation verb -------------------------------------


@pytest.mark.parametrize(
    "pre_event, call",
    [
        ("pre-fetch", lambda ws: ws.git_fetch("no-such-remote")),  # veto wins even though the
        # call would fail natively
        ("pre-import", lambda ws: ws.git_import()),
        ("pre-export", lambda ws: ws.git_export()),
        ("pre-sync", lambda ws: ws.sync_colocated()),
        ("pre-snapshot", lambda ws: ws.snapshot()),
        ("pre-untrack", lambda ws: ws.untrack_paths(["x.txt"])),
        ("pre-undo", lambda ws: ws.undo()),
        ("pre-restore", lambda ws: ws.restore_operation("@")),
    ],
)
def test_pre_hooks_veto_every_wired_event(scratch_repo: Path, pre_event: str, call: object) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    before = ws.head_operation()
    fired: list[str] = []

    def veto(*args: object, **kwargs: object) -> None:
        fired.append(pre_event)
        raise HookAbort("vetoed")

    ws.hooks.add(pre_event, veto)
    with pytest.raises(HookAbort, match="vetoed"):
        call(ws)  # type: ignore[operator]
    assert fired == [pre_event]
    assert ws.head_operation() == before  # the veto stopped the native call


@pytest.mark.parametrize(
    "post_event, call",
    [
        ("post-import", lambda ws: ws.git_import()),
        ("post-export", lambda ws: ws.git_export()),
        ("post-sync", lambda ws: ws.sync_colocated()),
        ("post-snapshot", lambda ws: ws.snapshot()),
        ("post-untrack", lambda ws: ws.untrack_paths(["x.txt"])),
        ("post-undo", lambda ws: ws.undo()),
        ("post-restore", lambda ws: ws.restore_operation(ws.head_operation())),
    ],
)
def test_post_hooks_fire_after_every_wired_event(scratch_repo: Path, post_event: str, call: object) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    fired: list[str] = []

    def record(*args: object, **kwargs: object) -> None:
        fired.append(post_event)

    ws.hooks.add(post_event, record)
    call(ws)  # type: ignore[operator]
    assert fired == [post_event]  # every verb fires its post hook even on a no-op (None op)


def test_fetch_hook_payloads(bookmarked_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(bookmarked_repo, hooks_config="off")
    seen: dict[str, object] = {}

    def pre(remote: str, bookmarks: list[str] | None) -> None:
        seen["remote"] = remote
        seen["bookmarks"] = bookmarks

    def post(operation: pyjutsu.Operation | None, remote: str) -> None:
        seen["op"] = operation
        seen["remote2"] = remote

    ws.hooks.add("pre-fetch", pre)
    ws.hooks.add("post-fetch", post)
    ws.git_fetch("origin")
    assert seen["remote"] == "origin"
    assert seen["bookmarks"] is None  # fetch-all default
    assert seen["remote2"] == "origin"
    assert seen["op"] is None or isinstance(seen["op"], pyjutsu.Operation)


def test_post_undo_receives_the_published_operation(scratch_repo: Path) -> None:
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    seen: dict[str, object] = {}

    def post(operation: pyjutsu.Operation) -> None:
        seen["op"] = operation

    ws.hooks.add("post-undo", post)
    ws.undo()
    assert isinstance(seen["op"], pyjutsu.Operation)
    assert seen["op"].id == ws.head_operation()  # type: ignore[union-attr]


def test_config_supports_the_full_event_vocabulary(tmp_path: Path) -> None:
    r = HookRegistry(root=tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[hooks.pre-export]\ncommands = [{ command = \"true\" }]\n"
        "[hooks.post-restore]\ncommands = [{ command = \"true\" }]\n"
        "[hooks.pre-untrack]\ncommands = [{ command = \"true\" }]\n"
    )
    assert r.load_config(cfg) == 3
    r.fire("pre-export")
    r.fire("post-restore")
    r.fire("pre-untrack")


# ---- realistic end-to-end policies -------------------------------------------


def _no_changes_to_generated(tx: object, *, paths: list[str] | None = None) -> None:
    """A config-addressable python hook: veto any pending change touching ``generated/``."""
    if any(p.startswith("generated/") for p in (paths or [])):
        raise HookAbort("edit the source, not generated/ — regenerate instead")


def test_config_python_hook_vetoes_generated_changes(tmp_path: Path, jj: object) -> None:
    """A realistic policy wired declaratively: a python pre-commit hook vetoing hand-edits to
    generated/ — exercised through the full tx lifecycle (pending paths → veto → rollback)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    jj.init_colocated(repo)  # type: ignore[attr-defined]
    (repo / "generated").mkdir()
    (repo / "generated" / "x.txt").write_text("out\n")
    jj(repo, "describe", "-m", "add generated")  # type: ignore[attr-defined]
    jj(repo, "new")  # type: ignore[attr-defined]

    cfg = tmp_path / "h.toml"
    cfg.write_text(f'[hooks.pre-commit]\npython = ["{__name__}:_no_changes_to_generated"]\n')
    ws = pyjutsu.Workspace.load(repo, hooks_config="off")
    ws.load_config_hooks(cfg)
    before = ws.head_operation()

    with pytest.raises(HookAbort, match="generated"):
        with ws.transaction("hand-edit generated", auto_snapshot=False) as tx:
            tx.restore("@", from_="root()")  # pending change = {generated/x.txt}
    assert ws.head_operation() == before  # vetoed → nothing published


def test_realistic_config_policy_end_to_end(tmp_path: Path, jj: object) -> None:
    """One .pyjutsu-hooks.toml, three real behaviors: the .py linter is skipped when no .py file
    changed, runs with exactly the matching path when one did, and the post-commit notifier fires
    every time (with the warn policy armed)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    jj.init_colocated(repo)  # type: ignore[attr-defined]
    (repo / "code.py").write_text("x = 1\n")
    jj(repo, "describe", "-m", "add code")  # type: ignore[attr-defined]  # A = {code.py}
    jj(repo, "new")  # type: ignore[attr-defined]
    (repo / "readme.md").write_text("docs\n")
    jj(repo, "describe", "-m", "add docs")  # type: ignore[attr-defined]  # B = {code.py, readme.md}
    jj(repo, "new")  # type: ignore[attr-defined]  # @ empty on B

    linted = tmp_path / "linted.txt"
    notified = tmp_path / "notified.txt"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('|'.join(sys.argv[2:]))\n"
    )
    cfg = tmp_path / "h.toml"
    cfg.write_text(
        "fail_fast = true\n"
        'on_post_failure = "warn"\n'
        "[hooks.pre-commit]\n"
        f'commands = [{{ command = ["{sys.executable}", "{probe}", "{linted}"], files = \'\.py$\' }}]\n'
        "[hooks.post-commit]\n"
        f'commands = [{{ command = ["{sys.executable}", "{probe}", "{notified}"], pass_filenames = false }}]\n'
    )
    ws = pyjutsu.Workspace.load(repo, hooks_config="off")
    ws.load_config_hooks(cfg)

    # 1. A change touching only docs → the .py linter is SKIPPED; the commit lands and the
    #    post-commit notifier fires.
    with ws.transaction("docs only", auto_snapshot=False) as tx:
        tx.restore("@", from_="@--")  # @ → A={code.py}; pending change = {readme.md}
    assert not linted.exists()
    assert notified.exists()
    assert ws.head_operation()

    # 2. A change touching code.py → the linter runs with exactly that path appended.
    with ws.transaction("code change", auto_snapshot=False) as tx:
        tx.restore("@", from_="root()")  # @ → {}; pending change = {code.py}
    assert linted.read_text().splitlines() == ["code.py"]
    assert notified.exists()


# ---- declarative auto-load on Workspace.load ----------------------------------


def test_workspace_load_auto_loads_config(scratch_repo: Path) -> None:
    (scratch_repo / ".pyjutsu-hooks.toml").write_text(
        f'[hooks.post-commit]\npython = ["{__name__}:_record_operation"]\n'
    )
    ws = pyjutsu.Workspace.load(scratch_repo)  # hooks_config="auto" is the default
    assert ws.hooks.count() == 1
    with ws.transaction("observed", auto_snapshot=False):
        pass
    assert _LAST_OPERATION == ws.head_operation()


def test_workspace_load_hooks_config_off_ignores_config_file(scratch_repo: Path) -> None:
    (scratch_repo / ".pyjutsu-hooks.toml").write_text(
        '[hooks.pre-commit]\ncommands = [{ command = "false" }]\n'
    )
    ws = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    assert ws.hooks.count() == 0


def test_load_config_hooks_explicit_and_hooks_are_per_workspace(
    scratch_repo: Path, tmp_path: Path
) -> None:
    cfg = tmp_path / "h.toml"
    cfg.write_text('[hooks.pre-commit]\ncommands = [{ command = "true" }]\n')
    a = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    b = pyjutsu.Workspace.load(scratch_repo, hooks_config="off")
    assert a.load_config_hooks(cfg) == 1
    assert a.hooks.count() == 1
    assert b.hooks.count() == 0  # registrations never cross workspaces
