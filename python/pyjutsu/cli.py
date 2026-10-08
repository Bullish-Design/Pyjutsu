"""The ``pyjutsu`` command line: guarded publication for callers that run a subprocess.

    pyjutsu publish-if --repo PATH --expect-wc FULL_COMMIT_ID --onto FULL_COMMIT_ID -m DESCRIPTION
    pyjutsu recover --repo PATH

Output is plain text on stdout: one ``key=value`` line per fact, first line ``result=<kind>``.
Values hold no newline. Human diagnostics go to stderr and are not a stable interface.

Exit codes:

- ``0`` the operation published (``publish-if``) or recovery finished (``recover``).
- ``1`` stale: the precondition failed and nothing was published (``result=stale``).
- ``2`` infrastructure or configuration failure (``result=error``), or an operation that landed
  or may have landed with a later step failed (``result=incomplete``). Never read ``2`` as a
  clean refusal; read ``result``.
- ``3`` usage failure (a missing argument, or an id that is not a full lowercase hex commit id).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import (
    PublishError,
    PublishIncompleteError,
    StalePublishError,
    Workspace,
)
from .errors import PyjutsuError, WorkspaceError
from .workspace import _FULL_COMMIT_ID

__all__ = ["main"]

EXIT_OK = 0
EXIT_STALE = 1
EXIT_FAILURE = 2
EXIT_USAGE = 3


class _Parser(argparse.ArgumentParser):
    """An argument parser that exits ``3`` on a usage failure, as the exit-code contract says."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _oneline(value: object) -> str:
    return " ".join(str(value).split())


def _emit(result: str, **facts: object) -> None:
    print(f"result={result}")
    for key, value in facts.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        print(f"{key}={_oneline(value)}")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="pyjutsu", description="Guarded jj publication.")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    publish = sub.add_parser(
        "publish-if",
        help="publish a new empty working-copy commit on --onto if nothing moved",
    )
    publish.add_argument("--repo", required=True, help="path inside the jj workspace")
    publish.add_argument("--expect-wc", required=True, help="expected working-copy commit id")
    publish.add_argument("--onto", required=True, help="commit id to build the new @ on")
    publish.add_argument("-m", "--message", required=True, help="operation description")

    recover = sub.add_parser(
        "recover", help="finish a publication that stopped after its operation landed"
    )
    recover.add_argument("--repo", required=True, help="path inside the jj workspace")
    return parser


def _load(repo: str) -> Workspace | None:
    path = Path(repo)
    if not path.is_dir():
        _emit("error", reason="repo-not-found", message=f"not a directory: {repo}")
        return None
    try:
        return Workspace.load(path)
    except WorkspaceError as exc:
        _emit("error", reason="workspace-load-failed", message=str(exc))
        return None


def _publish_if(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    for flag, value in (("--expect-wc", args.expect_wc), ("--onto", args.onto)):
        if not _FULL_COMMIT_ID.match(value):
            parser.error(f"{flag} must be a full lowercase hex commit id, got {value!r}")
    ws = _load(args.repo)
    if ws is None:
        return EXIT_FAILURE
    try:
        result = ws.publish_if(args.expect_wc, args.onto, args.message)
    except StalePublishError as exc:
        _emit(
            "stale",
            reason=exc.reason,
            expected_wc_commit=exc.expected_wc_commit,
            observed_wc_commit=exc.observed_wc_commit,
            onto=exc.onto,
            head_operations=exc.head_operations,
            dirty=int(exc.dirty),
        )
        print(str(exc), file=sys.stderr)
        return EXIT_STALE
    except PublishIncompleteError as exc:
        _emit(
            "incomplete",
            reason=exc.reason,
            stage=exc.stage,
            operation=exc.operation,
            expected_wc_commit=exc.expected_wc_commit,
            onto=exc.onto,
            recovery=f"pyjutsu recover --repo {ws.root}",
        )
        print(str(exc), file=sys.stderr)
        return EXIT_FAILURE
    except PublishError as exc:
        _emit(
            "error",
            reason=exc.reason,
            expected_wc_commit=exc.expected_wc_commit,
            onto=exc.onto,
            message=str(exc),
        )
        print(str(exc), file=sys.stderr)
        return EXIT_FAILURE
    except PyjutsuError as exc:
        _emit("error", reason=type(exc).__name__, message=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    _emit(
        "published",
        operation=result.operation,
        head_operation=result.head_operation,
        wc_commit=result.wc_commit,
        onto=result.onto,
        expected_wc_commit=result.expected_wc_commit,
        git_sync=result.git_sync,
        git_sync_operation=result.git_sync_operation,
    )
    return EXIT_OK


def _recover(args: argparse.Namespace) -> int:
    ws = _load(args.repo)
    if ws is None:
        return EXIT_FAILURE
    try:
        operations = ws.recover()
    except PyjutsuError as exc:
        _emit("error", reason=type(exc).__name__, message=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    _emit("recovered", operations=[op.id for op in operations], stale=int(ws.is_stale()))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return the exit code."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 0 for --help; every other exit is already mapped to 3 by `_Parser.error`.
        return int(exc.code or 0)
    if args.command == "publish-if":
        return _publish_if(args, parser)
    return _recover(args)


if __name__ == "__main__":
    raise SystemExit(main())
