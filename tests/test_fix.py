"""C7: fix (lane `003/c7`).

Oracle is the pinned `jj` CLI's `jj fix`. The tools are trivial Python filters written into the
test's tmp dir, so the suite depends on no formatter being installed.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pyjutsu
import pytest

from tests.diff.jj_cli import JjCli

#: A formatter that upper-cases whatever it is given.
_UPPER = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read().upper())"

#: A formatter that upper-cases only the 1-based line ranges named by `--lines=<first>:<last>`.
_UPPER_LINES = (
    "import sys\n"
    'ranges = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--lines=")]\n'
    "lines = sys.stdin.buffer.read().decode().splitlines(keepends=True)\n"
    "for r in ranges:\n"
    '    first, last = (int(x) for x in r.split(":"))\n'
    "    for i in range(first - 1, last):\n"
    "        lines[i] = lines[i].upper()\n"
    'sys.stdout.buffer.write("".join(lines).encode())\n'
)

#: A formatter that always fails. jj only rewrites a file when the tool exits 0.
_FAILING = "import sys; sys.stdin.buffer.read(); sys.exit(3)"


def _tool_toml(
    tmp_path: Path, name: str, script: str, patterns: list[str], **extra: str
) -> str:
    """A `fix.tools.<name>` table running `python <script-file>` over `patterns`.

    The script goes to a file rather than `python -c`: a script embedded in the TOML would have
    to survive TOML string escaping, and these scripts contain quotes and newlines.
    """
    script_file = tmp_path / f"{name}.py"
    script_file.write_text(script)
    command = [sys.executable, str(script_file)]
    rendered = ", ".join(f'"{arg}"' for arg in command)
    pattern_list = ", ".join(f'"{p}"' for p in patterns)
    toml = f"[fix.tools.{name}]\ncommand = [{rendered}]\npatterns = [{pattern_list}]\n"
    for key, value in extra.items():
        toml += f"{key} = {value}\n"
    return toml


def _fix_repo(tmp_path: Path, jj: JjCli, name: str = "fix") -> Path:
    """`A -> B -> @`, where A writes `a.txt`, B writes `b.txt`, and `@` is empty."""
    repo = tmp_path / name
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "a.txt").write_text("alpha\n")
    jj(repo, "describe", "-m", "commit A")
    jj(repo, "new")
    (repo / "b.txt").write_text("beta\n")
    jj(repo, "describe", "-m", "commit B")
    jj(repo, "new")
    return repo


def _contents(jj: JjCli, repo: Path, revset: str, path: str) -> str:
    return jj(repo, "file", "show", "-r", revset, path)


def test_fix_matches_cli(tmp_path: Path, jj: JjCli) -> None:
    """The binding's fix leaves the repo in the same state as `jj fix`."""
    jj.append_config(_tool_toml(tmp_path, "upper", _UPPER, ["glob:'**/*.txt'"]))
    cli_repo = _fix_repo(tmp_path, jj)
    ws_repo = tmp_path / "fix-ws"
    shutil.copytree(cli_repo, ws_repo)

    jj(cli_repo, "fix")

    ws = pyjutsu.Workspace.load(ws_repo)
    with ws.transaction("fix") as tx:
        summary = tx.fix()

    for revset, path in (("@--", "a.txt"), ("@-", "a.txt"), ("@-", "b.txt")):
        assert _contents(jj, ws_repo, revset, path) == _contents(jj, cli_repo, revset, path)
    assert _contents(jj, ws_repo, "@--", "a.txt") == "ALPHA\n"
    assert summary.tools == ["upper"]
    # A, B, and the empty `@`: jj re-fixes a fixed path in every descendant so the fix is
    # never lost to a later rebase.
    assert summary.num_fixed_commits == 3


def test_fix_propagates_into_descendants(tmp_path: Path, jj: JjCli) -> None:
    """Fixing A also updates A's file in every descendant, so the fix is not lost."""
    jj.append_config(_tool_toml(tmp_path, "upper", _UPPER, ["a.txt"]))
    repo = _fix_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("fix") as tx:
        summary = tx.fix("@--")

    assert _contents(jj, repo, "@--", "a.txt") == "ALPHA\n"
    assert _contents(jj, repo, "@-", "a.txt") == "ALPHA\n"
    # `b.txt` does not match the tool's pattern, so it is untouched.
    assert _contents(jj, repo, "@-", "b.txt") == "beta\n"
    assert summary.num_fixed_commits == 3  # A, plus B and `@` carrying the fixed a.txt
    assert all(len(old) == len(new) for old, new in summary.rewrites.items())


def test_fix_paths_narrow_the_run(tmp_path: Path, jj: JjCli) -> None:
    """`paths` restricts which files reach the tool at all, like `jj fix <filesets>`."""
    jj.append_config(_tool_toml(tmp_path, "upper", _UPPER, ["glob:'**/*.txt'"]))
    repo = _fix_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("fix") as tx:
        tx.fix(paths=["b.txt"])

    assert _contents(jj, repo, "@--", "a.txt") == "alpha\n"
    assert _contents(jj, repo, "@-", "b.txt") == "BETA\n"


def test_fix_tools_selects_by_name(tmp_path: Path, jj: JjCli) -> None:
    jj.append_config(_tool_toml(tmp_path, "upper_a", _UPPER, ["a.txt"]))
    jj.append_config(_tool_toml(tmp_path, "upper_b", _UPPER, ["b.txt"]))
    repo = _fix_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("fix") as tx:
        summary = tx.fix(tools=["upper_b"])

    assert summary.tools == ["upper_b"]
    assert _contents(jj, repo, "@--", "a.txt") == "alpha\n"
    assert _contents(jj, repo, "@-", "b.txt") == "BETA\n"


def test_fix_unknown_tool_raises(tmp_path: Path, jj: JjCli) -> None:
    """A typo must not be a silent no-op."""
    jj.append_config(_tool_toml(tmp_path, "upper", _UPPER, ["a.txt"]))
    repo = _fix_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("fix") as tx:
        with pytest.raises(pyjutsu.PyjutsuError, match="no such fix tool"):
            tx.fix(tools=["uppr"])


def test_fix_without_configured_tools_raises(tmp_path: Path, jj: JjCli) -> None:
    repo = _fix_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("fix") as tx:
        with pytest.raises(pyjutsu.PyjutsuError, match="no enabled fix tools"):
            tx.fix()


def test_fix_skips_a_disabled_tool(tmp_path: Path, jj: JjCli) -> None:
    jj.append_config(_tool_toml(tmp_path, "upper", _UPPER, ["a.txt"], enabled="false"))
    repo = _fix_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("fix") as tx:
        with pytest.raises(pyjutsu.PyjutsuError, match="no enabled fix tools"):
            tx.fix()


def test_fix_ignores_a_failing_tool(tmp_path: Path, jj: JjCli) -> None:
    """jj rewrites a file only when the tool exits 0."""
    jj.append_config(_tool_toml(tmp_path, "boom", _FAILING, ["a.txt"]))
    repo = _fix_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("fix") as tx:
        summary = tx.fix()

    assert summary.num_fixed_commits == 0
    assert _contents(jj, repo, "@--", "a.txt") == "alpha\n"


def test_fix_line_ranges_match_cli(tmp_path: Path, jj: JjCli) -> None:
    """`line-range-arg` passes only the modified line ranges, and the CLI agrees."""
    jj.append_config(
        _tool_toml(
            tmp_path,
            "upper_lines",
            _UPPER_LINES,
            ["a.txt"],
            **{"line-range-arg": '"--lines=$first:$last"'},
        )
    )
    cli_repo = tmp_path / "ranges"
    cli_repo.mkdir()
    jj.init_colocated(cli_repo)
    (cli_repo / "a.txt").write_text("one\ntwo\nthree\n")
    jj(cli_repo, "describe", "-m", "commit A")
    jj(cli_repo, "new")
    # Only line 2 changes in commit B, so only line 2 is formatted.
    (cli_repo / "a.txt").write_text("one\ntwo two\nthree\n")
    jj(cli_repo, "describe", "-m", "commit B")

    ws_repo = tmp_path / "ranges-ws"
    shutil.copytree(cli_repo, ws_repo)
    # Fix only `@`, so its base (commit A) stays as written and the ranges are B's own.
    jj(cli_repo, "fix", "-s", "@")

    ws = pyjutsu.Workspace.load(ws_repo)
    with ws.transaction("fix") as tx:
        tx.fix("@")

    assert _contents(jj, ws_repo, "@", "a.txt") == _contents(jj, cli_repo, "@", "a.txt")
    assert _contents(jj, ws_repo, "@", "a.txt") == "one\nTWO TWO\nthree\n"
    assert _contents(jj, ws_repo, "@-", "a.txt") == "one\ntwo\nthree\n"


def test_fix_all_lines_ignores_the_ranges(tmp_path: Path, jj: JjCli) -> None:
    jj.append_config(
        _tool_toml(
            tmp_path,
            "upper_lines",
            _UPPER_LINES,
            ["a.txt"],
            **{"line-range-arg": '"--lines=$first:$last"'},
        )
    )
    repo = tmp_path / "all-lines"
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "a.txt").write_text("one\ntwo\nthree\n")
    jj(repo, "describe", "-m", "commit A")
    jj(repo, "new")
    (repo / "a.txt").write_text("one\ntwo two\nthree\n")
    jj(repo, "describe", "-m", "commit B")

    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("fix") as tx:
        tx.fix("@", all_lines=True)

    # With no `--lines` argument the helper upper-cases nothing, which proves the ranges were
    # dropped rather than passed.
    assert _contents(jj, repo, "@", "a.txt") == "one\ntwo two\nthree\n"


def test_fix_include_unchanged_files(tmp_path: Path, jj: JjCli) -> None:
    """Without the flag, a file unchanged in a commit is not fixed there; with it, it is."""
    jj.append_config(
        _tool_toml(tmp_path, "upper", _UPPER, ["a.txt"], **{"run-tool-if-zero-line-ranges": "true"})
    )
    repo = _fix_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("fix") as tx:
        summary = tx.fix("@-", include_unchanged_files=True)

    # `a.txt` is unchanged in B, but the flag makes B's copy of it eligible.
    assert _contents(jj, repo, "@-", "a.txt") == "ALPHA\n"
    # A itself was not a root, so it keeps the original.
    assert _contents(jj, repo, "@--", "a.txt") == "alpha\n"
    assert summary.num_fixed_commits == 2  # the root B and its descendant `@`


def test_fix_refuses_an_immutable_root(tmp_path: Path, jj: JjCli) -> None:
    jj.append_config(_tool_toml(tmp_path, "upper", _UPPER, ["a.txt"]))
    repo = _fix_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("fix") as tx:
        with pytest.raises(pyjutsu.ImmutableCommitError):
            tx.fix("root()")


def test_fix_empty_revset_raises(tmp_path: Path, jj: JjCli) -> None:
    jj.append_config(_tool_toml(tmp_path, "upper", _UPPER, ["a.txt"]))
    repo = _fix_repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("fix") as tx:
        with pytest.raises(pyjutsu.RevsetError):
            tx.fix("none()")
