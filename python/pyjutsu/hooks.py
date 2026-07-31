"""Pre-commit-style hooks for pyjutsu — in-process, zero-cost when unused.

Two registration styles feed the same per-workspace :class:`HookRegistry`:

- **Imperative** — plain Python callables, no subprocess, no serialization::

      ws.hooks.add("pre-commit", check_license)     # returns fn, composes with decorators
      @ws.hooks.on("pre-commit")                     # decorator form
      def check_license(tx): ...
      with ws.hooks("pre-commit", check_license):    # registered for the block only
          ...

- **Declarative** — a ``.pyjutsu-hooks.toml`` file (pre-commit-config style, parsed with the
  stdlib :mod:`tomllib` — no YAML dependency)::

      [hooks.pre-commit]
      commands = [{ command = "ruff check --fix", files = "\\.py$" }]
      python = ["myapp.hooks:check_license"]
      adapters = ["run_prek"]

  Load it explicitly with ``ws.load_config_hooks(...)`` or automatically at
  ``Workspace.load(path, hooks_config="auto")`` (the default) — ``<path>/.pyjutsu-hooks.toml``
  is read when present, and a repo without one behaves exactly as before.

Semantics mirror git hooks: a ``pre-*`` hook that raises :class:`HookAbort` (or fails) **vetoes**
the operation — for a transaction it rolls back and publishes nothing; for ``git push``/``fetch``
it aborts before any network I/O. A ``post-*`` failure raises :class:`PostHookError` carrying the
published operation id: the operation landed; only the hook failed. By default every hook runs
and **all** failures are reported before aborting (pre-commit's ``fail_fast=false``);
``on_post_failure = "warn"`` downgrades a failing post-hook to a :class:`UserWarning`.

Performance: with no hooks registered, :meth:`HookRegistry.fire` is one dict lookup over an empty
tuple — no subprocess, no allocation, no serialization. Declarative ``commands`` cost exactly what
the declared command costs and nothing more. Hooks fire synchronously on the calling thread at the
facade's defined points (inside :meth:`asyncio.to_thread` they run in the worker thread, like every
other pyjutsu call).

Per-event call signatures (positional, so hooks are plain typed functions):

- ``pre-commit``: ``fn(tx: Transaction, *, paths: list[str] | None = None)`` — ``paths`` is the
  list of repo-relative paths changed by the pending commit (``tx.changed_paths("@")``); the
  hook may mutate ``tx`` or raise to veto.
- ``post-commit``: ``fn(operation_id: str, description: str)``
- ``pre-push``: ``fn(remote: str, bookmarks: list[str])``
- ``post-push``: ``fn(operation: Operation | None, remote: str)``
- ``pre-fetch``: ``fn(remote: str, bookmarks: list[str] | None)``
- ``post-fetch``: ``fn(operation: Operation | None, remote: str)``
- ``pre-import``: ``fn()`` · ``post-import``: ``fn(operation: Operation | None)``
- ``pre-export``: ``fn()`` · ``post-export``: ``fn(operation: Operation | None)``
- ``pre-sync``: ``fn()`` · ``post-sync``: ``fn(operation: Operation | None)``
- ``pre-snapshot``: ``fn()`` · ``post-snapshot``: ``fn(operation: Operation | None)``
- ``pre-untrack``: ``fn(paths: list[str])`` · ``post-untrack``: ``fn(operation: Operation | None,
  paths: list[str])``
- ``pre-undo``: ``fn(operation: str | None)`` · ``post-undo``: ``fn(operation: Operation)``
- ``pre-restore``: ``fn(operation: str)`` · ``post-restore``: ``fn(operation: Operation)``
"""

from __future__ import annotations

import importlib
import os
import re
import shlex
import subprocess
import tomllib
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import HookAbort, PostHookError, PyjutsuError

__all__ = [
    "CONFIG_FILENAME",
    "CONFIG_EVENTS",
    "HookRegistry",
    "run_prek",
    "run_pre_commit",
    "HookAbort",
    "PostHookError",
]

#: The declarative config file name auto-discovered at ``Workspace.load(..., hooks_config="auto")``.
CONFIG_FILENAME = ".pyjutsu-hooks.toml"

#: Events the declarative config may bind — every event the facade actually fires. A config may
#: never declare a hook that won't fire, so verbs without events (the tag verbs, remotes CRUD,
#: ``add_workspace``/``forget_workspace``, ``update_stale``) are absent by design.
CONFIG_EVENTS = frozenset(
    (
        "pre-commit",
        "post-commit",
        "pre-push",
        "post-push",
        "pre-fetch",
        "post-fetch",
        "pre-import",
        "post-import",
        "pre-export",
        "post-export",
        "pre-sync",
        "post-sync",
        "pre-snapshot",
        "post-snapshot",
        "pre-untrack",
        "post-untrack",
        "pre-undo",
        "post-undo",
        "pre-restore",
        "post-restore",
    )
)


@dataclass(frozen=True)
class _Hook:
    event: str
    name: str
    fn: Callable[..., None]


class HookRegistry:
    """A per-workspace registry of hook callbacks keyed by event name.

    Obtain one from :attr:`Workspace.hooks` — each workspace owns its registry, there is no global
    state, and registrations never cross workspaces. All methods are safe to call from any thread;
    ``fire`` runs the hooks on the calling thread at the facade's defined points.
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        fail_fast: bool = False,
        on_post_failure: str = "raise",
    ) -> None:
        # The working-copy root; the default cwd for declarative command hooks.
        self._root = root
        self._hooks: dict[str, list[_Hook]] = defaultdict(list)
        #: Run every hook even after one fails, then report all failures (pre-commit's default);
        #: True stops at the first failure. Config: top-level ``fail_fast`` in the hook config.
        self.fail_fast = fail_fast
        #: What a failing ``post-*`` hook does: ``"raise"`` (a :class:`PostHookError` carrying the
        #: published operation id) or ``"warn"`` (a :class:`UserWarning`; the operation's result is
        #: returned normally). Config: top-level ``on_post_failure``.
        self.on_post_failure = on_post_failure

    # -- registration -----------------------------------------------------

    def add(
        self, event: str, fn: Callable[..., None], *, name: str | None = None
    ) -> Callable[..., None]:
        """Register ``fn`` to fire on ``event``; returns ``fn`` (so it composes with decorators).

        ``name`` is only used in error messages (defaults to ``fn.__name__``).
        """
        if not callable(fn):
            raise TypeError(
                f"hook for {event!r} must be callable, got {type(fn).__name__}"
            )
        self._hooks[event].append(_Hook(event, name or getattr(fn, "__name__", "hook"), fn))
        return fn

    def remove(self, event: str, fn: Callable[..., None]) -> bool:
        """Remove ``fn`` (by identity) from ``event``; returns whether it was present."""
        hooks = self._hooks.get(event)
        if not hooks:
            return False
        for i, hook in enumerate(hooks):
            if hook.fn is fn:
                del hooks[i]
                return True
        return False

    def on(self, event: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
        """Decorator form of :meth:`add` — ``@ws.hooks.on("pre-commit")`` registers the function."""

        def decorator(fn: Callable[..., None]) -> Callable[..., None]:
            return self.add(event, fn)

        return decorator

    def __call__(
        self, event: str, fn: Callable[..., None], *, name: str | None = None
    ) -> Iterator[None]:
        """Context-manager form: ``with ws.hooks("pre-commit", fn):`` registers for the block."""
        return _temporary(self, event, fn, name)

    def clear(self, event: str | None = None) -> None:
        """Drop every hook, or just those on ``event`` when given."""
        if event is None:
            self._hooks.clear()
        else:
            self._hooks.pop(event, None)

    # -- firing -----------------------------------------------------------

    def fire(
        self,
        event: str,
        *args: object,
        fail_fast: bool | None = None,
        **kwargs: object,
    ) -> None:
        """Call every hook registered for ``event``, in registration order.

        With ``fail_fast=True`` (or the registry's ``fail_fast``), the first hook exception
        propagates immediately. With ``fail_fast=False`` (the default, pre-commit parity) every
        hook runs, all failures are collected, and the first is re-raised with the others attached
        as ``Exception.__notes__`` — so you see every problem, not just the first. The facade's
        wiring converts the raised error into :class:`HookAbort` / wrapped :class:`PostHookError`
        per event. Zero hooks ⇒ one dict lookup over an empty tuple — the fast path.
        """
        if fail_fast is None:
            fail_fast = self.fail_fast
        hooks = tuple(self._hooks.get(event, ()))
        if fail_fast:
            for hook in hooks:
                hook.fn(*args, **kwargs)
            return
        failures: list[tuple[str, Exception]] = []
        for hook in hooks:
            try:
                hook.fn(*args, **kwargs)
            except Exception as e:  # a buggy hook must not hide its siblings' results
                failures.append((hook.name, e))
        if failures:
            _name, first = failures[0]
            for other_name, e in failures[1:]:
                first.add_note(f"hook {other_name!r} also failed: {e}")
            raise first

    # -- introspection ----------------------------------------------------

    def events(self) -> list[str]:
        """Event names that currently have at least one hook."""
        return sorted(self._hooks)

    def count(self, event: str | None = None) -> int:
        """Number of registered hooks, or just on ``event`` when given."""
        if event is None:
            return sum(len(hooks) for hooks in self._hooks.values())
        return len(self._hooks.get(event, ()))

    # -- declarative config ------------------------------------------------

    def load_config(self, path: str | os.PathLike[str]) -> int:
        """Parse a ``.pyjutsu-hooks.toml`` file and register its hooks; returns how many.

        Schema (stdlib :mod:`tomllib`)::

            fail_fast = false           # optional: run every hook even after one fails (default)
            on_post_failure = "raise"   # optional: "raise" (default) or "warn"

            [hooks.pre-commit]
            commands = [
              { command = "ruff check --fix", files = "\\.py$", timeout = 120 },
              { command = ["pytest", "-q"], pass_filenames = false },
              { command = "deploy", env = { DEPLOY_KEY = "..." } },
            ]
            python = ["myapp.hooks:check_license"]
            adapters = ["run_prek"]

            [hooks.post-commit]
            commands = [{ command = "echo committed" }]

        - ``fail_fast`` (top level, default ``false``): run every hook even after one fails, then
          raise the first failure with the rest attached as notes — pre-commit's run-all behavior.
        - ``on_post_failure`` (top level, default ``"raise"``): ``"warn"`` downgrades a failing
          ``post-*`` hook to a :class:`UserWarning` instead of raising a :class:`PostHookError`.

        - ``commands``: external programs. ``command`` is a string (shlex-split, no shell) or an
          argv list; a non-zero exit vetoes a ``pre-*`` event (or fails a ``post-*`` one). Optional
          ``files``/``exclude`` regexes filter the path list the event provides — matching paths
          are appended to the command, and the hook is **skipped** when the filters exclude every
          path (pre-commit semantics; today only ``pre-commit`` passes a path list, so other events
          always run the command). ``pass_filenames = false`` skips the appending; ``env`` adds
          environment variables to the child process; ``timeout`` seconds and ``cwd`` override the
          process defaults.
        - ``python``: dotted paths to in-process callables (``module:attr`` or ``module.attr``),
          called with the event's positional args plus its keyword payload (e.g. ``paths`` for
          ``pre-commit``) — zero subprocess.
        - ``adapters``: built-in pre-commit-compatible adapters — ``run_prek`` / ``run_pre_commit``
          — which shell to that binary (see those functions for the flags they build).

        Validation is strict: an unknown event, unknown key, bad command, or unparseable regex is
        an error — a typo must fail loudly, not silently no-op.
        """
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise PyjutsuError(f"cannot read hook config {path}: {e}") from e
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise PyjutsuError(f"{path}: invalid TOML: {e}") from e
        if set(data) - {"hooks", "fail_fast", "on_post_failure"}:
            raise PyjutsuError(
                f"{path}: unknown top-level key(s) {sorted(set(data) - {'hooks', 'fail_fast', 'on_post_failure'})}; "
                f"expected [hooks], fail_fast, or on_post_failure"
            )
        fail_fast = data.get("fail_fast", False)
        if not isinstance(fail_fast, bool):
            raise PyjutsuError(f"{path}: 'fail_fast' must be true or false")
        self.fail_fast = fail_fast
        on_post_failure = data.get("on_post_failure", "raise")
        if on_post_failure not in ("raise", "warn"):
            raise PyjutsuError(f"{path}: 'on_post_failure' must be 'raise' or 'warn'")
        self.on_post_failure = on_post_failure
        hooks_table = data.get("hooks", {})
        if not isinstance(hooks_table, dict):
            raise PyjutsuError(f"{path}: [hooks] must be a table")
        count = 0
        for event, spec in hooks_table.items():
            if event not in CONFIG_EVENTS:
                raise PyjutsuError(
                    f"{path}: unknown hook event {event!r}; supported: {sorted(CONFIG_EVENTS)}"
                )
            if not isinstance(spec, dict):
                raise PyjutsuError(f"{path}: [hooks.{event}] must be a table")
            unknown = set(spec) - {"commands", "python", "adapters"}
            if unknown:
                raise PyjutsuError(
                    f"{path}: [hooks.{event}] unknown key(s) {sorted(unknown)}"
                )
            for entry in spec.get("commands", []):
                hook = self._command_hook(event, entry)
                self.add(event, hook, name=hook.name)
                count += 1
            for dotted in spec.get("python", []):
                self.add(event, _resolve_callable(dotted), name=dotted)
                count += 1
            for adapter in spec.get("adapters", []):
                argv = _adapter_argv(event, adapter)
                hook = self._command_hook(event, {"command": argv})
                self.add(event, hook, name=hook.name)
                count += 1
        return count

    def _command_hook(self, event: str, entry: object) -> "_CommandHook":
        if not isinstance(entry, dict):
            raise PyjutsuError(
                f"{event} hook entry must be a table, got {type(entry).__name__}"
            )
        unknown = set(entry) - {"command", "files", "exclude", "timeout", "cwd", "env", "pass_filenames"}
        if unknown:
            raise PyjutsuError(f"{event} hook entry unknown key(s) {sorted(unknown)}")
        files = _compile_pattern(entry.get("files"), "files")
        exclude = _compile_pattern(entry.get("exclude"), "exclude")
        command = _command_argv(entry.get("command"))
        if not command:
            raise PyjutsuError(f"{event} hook entry needs a non-empty 'command'")
        timeout = entry.get("timeout")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        ):
            raise PyjutsuError(
                f"'timeout' must be a number of seconds, got {timeout!r}"
            )
        env = entry.get("env")
        if env is not None and (
            not isinstance(env, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
        ):
            raise PyjutsuError(f"'env' must be a table of strings, got {env!r}")
        pass_filenames = entry.get("pass_filenames", True)
        if not isinstance(pass_filenames, bool):
            raise PyjutsuError(
                f"'pass_filenames' must be true or false, got {pass_filenames!r}"
            )
        cwd = Path(entry["cwd"]) if "cwd" in entry else self._root
        return _CommandHook(
            command=command,
            name=command[0],
            files=files,
            exclude=exclude,
            timeout=timeout,
            cwd=cwd,
            env=env,
            pass_filenames=pass_filenames,
        )


def _command_argv(command: object) -> list[str]:
    """Normalize a config ``command`` (string → shlex-split, or an argv list) to an argv list."""
    if isinstance(command, str):
        return shlex.split(command)
    if isinstance(command, list) and all(isinstance(p, str) for p in command):
        return command
    raise PyjutsuError(
        f"'command' must be a string or a list of strings, got {type(command).__name__}"
    )


def _compile_pattern(spec: object, key: str) -> re.Pattern[str] | None:
    if spec is None:
        return None
    if not isinstance(spec, str):
        raise PyjutsuError(f"'{key}' must be a regex string, got {type(spec).__name__}")
    try:
        return re.compile(spec)
    except re.error as e:
        raise PyjutsuError(f"bad '{key}' regex {spec!r}: {e}") from e


class _CommandHook:
    """Run an external program; a non-zero exit raises :class:`HookAbort`.

    Optionally filters the event's path list through ``files``/``exclude`` regexes, appends the
    matching paths to the command (pre-commit style), and **skips** the command entirely when the
    filters exclude every path. The facade's post-* wiring converts the ``HookAbort`` into a
    :class:`PostHookError`; the program itself doesn't care which side it is.
    """

    def __init__(
        self,
        *,
        command: list[str],
        name: str,
        files: re.Pattern[str] | None = None,
        exclude: re.Pattern[str] | None = None,
        timeout: int | float | None = None,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        pass_filenames: bool = True,
    ) -> None:
        self.command = command
        self.name = name
        self.files = files
        self.exclude = exclude
        self.timeout = timeout
        self.cwd = cwd
        self.env = env
        self.pass_filenames = pass_filenames

    def __call__(self, *args: object, paths: object = None, **kwargs: object) -> None:
        del args, kwargs  # commands don't consume the event payload, only its path list
        argv = self._argv(paths)
        if argv is None:
            return  # the filters excluded every path → the hook is skipped (pre-commit semantics)
        try:
            proc = subprocess.run(
                argv,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=None if self.env is None else {**os.environ, **self.env},
            )
        except FileNotFoundError as e:
            raise HookAbort(
                f"hook {self.name!r}: executable not found: {argv[0]}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise HookAbort(
                f"hook {self.name!r} timed out after {self.timeout}s"
            ) from e
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise HookAbort(
                f"hook {self.name!r} (`{' '.join(argv)}`) exited {proc.returncode}"
                + (f": {detail}" if detail else "")
            )

    def _argv(self, paths: object) -> list[str] | None:
        """The argv to run, or ``None`` to skip the hook.

        - No path list from the event (or ``pass_filenames = false``): run the command as
          configured.
        - No ``files``/``exclude`` filters: pass every path.
        - Filters configured: pass the matching paths — and **skip** when none match (pre-commit's
          "the hook only runs when it has files to check" behavior).
        """
        if not self.pass_filenames:
            return list(self.command)
        if not isinstance(paths, (list, tuple)):
            return list(self.command)  # the event provides no path list → run as configured
        if self.files is None and self.exclude is None:
            return [*self.command, *(str(p) for p in paths)]
        selected = [str(p) for p in paths if self._match(str(p))]
        if not selected:
            return None
        return [*self.command, *selected]

    def _match(self, path: str) -> bool:
        if self.files is not None and not self.files.search(path):
            return False
        if self.exclude is not None and self.exclude.search(path):
            return False
        return True


def _resolve_callable(dotted: str) -> Callable[..., None]:
    """Resolve ``module:attr`` or ``module.attr`` to the callable, importing the module."""
    if ":" in dotted:
        module_name, _, attr = dotted.partition(":")
    else:
        module_name, _, attr = dotted.rpartition(".")
    if not module_name or not attr:
        raise PyjutsuError(
            f"bad hook reference {dotted!r}; expected 'module:attr' or 'module.attr'"
        )
    try:
        obj = importlib.import_module(module_name)
    except ImportError as e:
        raise PyjutsuError(f"cannot import {dotted!r}: {e}") from e
    for part in attr.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as e:
            raise PyjutsuError(f"cannot resolve {dotted!r}: {e}") from e
    if not callable(obj):
        raise PyjutsuError(
            f"{dotted!r} resolves to {type(obj).__name__}, which is not callable"
        )
    return obj


#: pre-commit's hook stages that map onto pyjutsu's wired events. `pre-commit` is the default
#: stage (no `--hook-stage` flag needed); `post-push` has no pre-commit analog and is rejected.
_PRECOMMIT_STAGES = {"pre-commit": "pre-commit", "post-commit": "post-commit", "pre-push": "pre-push"}


def _precommit_argv(binary: str, event: str, mode: str | None) -> list[str]:
    stage = _PRECOMMIT_STAGES.get(event)
    if stage is None:
        raise PyjutsuError(
            f"the pre-commit ecosystem has no {event!r} stage; adapters support "
            f"{sorted(_PRECOMMIT_STAGES)}"
        )
    argv = [binary, "run"]
    # jj has no git index, so "staged files" (the default selection) is ill-defined; default to
    # `--all-files`. `mode="staged"` passes through for colocated repos that keep a git index.
    if (mode or "all-files") == "all-files":
        argv.append("--all-files")
    if stage != "pre-commit":
        argv += ["--hook-stage", stage]
    return argv


def run_prek(event: str, *, binary: str = "prek", mode: str | None = None) -> list[str]:
    """Build the argv that runs the ``prek`` binary for ``event`` (pre-commit-compatible hooks).

    e.g. ``run_prek("pre-commit")`` → ``["prek", "run", "--all-files"]``; ``run_prek("post-commit")``
    → ``["prek", "run", "--all-files", "--hook-stage", "post-commit"]``. ``binary`` overrides the
    executable (a full path or a wrapper); ``mode="staged"`` drops ``--all-files``. Use the result
    as a declarative ``commands`` entry or wrap it in your own hook.
    """
    return _precommit_argv(binary, event, mode)


def run_pre_commit(event: str, *, binary: str = "pre-commit", mode: str | None = None) -> list[str]:
    """Build the argv that runs the ``pre-commit`` binary for ``event`` — same shape as
    :func:`run_prek`, with ``pre-commit`` as the default binary."""
    return _precommit_argv(binary, event, mode)


def _adapter_argv(event: str, adapter: object) -> list[str]:
    """Resolve a config ``adapters`` entry (``"run_prek"`` or ``{ name = ..., mode = ... }``)."""
    if isinstance(adapter, str):
        name, kwargs = adapter, {}
    elif isinstance(adapter, dict):
        name = adapter.get("name")
        unknown = set(adapter) - {"name", "binary", "mode"}
        if unknown:
            raise PyjutsuError(f"adapter entry unknown key(s) {sorted(unknown)}")
        kwargs = {k: v for k, v in adapter.items() if k != "name"}
    else:
        raise PyjutsuError(
            f"adapter must be a string or a table, got {type(adapter).__name__}"
        )
    if not isinstance(name, str):
        raise PyjutsuError(f"adapter needs a string 'name', got {adapter!r}")
    if name == "run_prek":
        return run_prek(event, **kwargs)
    if name == "run_pre_commit":
        return run_pre_commit(event, **kwargs)
    raise PyjutsuError(f"unknown adapter {name!r}; supported: run_prek, run_pre_commit")


@contextmanager
def _temporary(
    registry: HookRegistry, event: str, fn: Callable[..., None], name: str | None
) -> Iterator[None]:
    registry.add(event, fn, name=name)
    try:
        yield
    finally:
        registry.remove(event, fn)
