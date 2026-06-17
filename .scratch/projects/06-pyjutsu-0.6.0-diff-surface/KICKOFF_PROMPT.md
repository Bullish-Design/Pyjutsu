# Pyjutsu 0.6.0 — diff read surface implementation (kickoff)

You are implementing **Pyjutsu** (`import pyjutsu`): the Pythonic + Pydantic binding to jujutsu's Rust
engine (`jj-lib`) via PyO3/maturin, in-process — no subprocess backend, no text parsing. The surface
through **0.5.0** is feature-complete and green (binds `jj-lib =0.38.0`, `gix =0.78.0`; pyjutsu is on
its own independent semver). Working tree is clean on `main`.

## Your job this session
Implement the **diff read surface** — the canonical "later" item in the concept doc (§5 `diff`
*(later)*, §12 "full diffs/hunks"), built as **three read-only slices** that extend the existing
`diff_stat` machinery (`src/diff_stat.rs`):

1. **`diff(revset)` → name-status** — the changed paths and how each changed (added / modified /
   removed / type_changed), for one commit vs its parent(s).
2. **per-file content hunks** — the line-level changes (unified-diff hunks) for each changed text
   file; binary/symlink/submodule/conflict files are flagged with no hunks.
3. **copy/rename detection** — fold jj's copy records into name-status (`renamed`/`copied` + source).
   **This slice is conditional:** verify the git backend actually emits copy records first; if it
   doesn't, ship slices 1–2 and **flag** copies/renames (the guide says exactly where this is OK).

**All three are read-only** — no transaction, no operation, **no commit-id can move**. This is the
property that makes the milestone low-risk; there is no snapshot/tree fencing like 0.4.0/0.5.0 had.

## Authority order (read before touching code)
1. **`.scratch/projects/06-pyjutsu-0.6.0-diff-surface/IMPLEMENTATION_GUIDE.md`** — the detailed,
   verified plan: per-slice jj-lib APIs (`file:line` into the pinned source), Rust + Python sketches,
   the exact differential tests to add, and the traps (with explicit **VERIFY** markers). **This is
   your primary spec. Read it in full first.**
2. `docs/PYJUTSU_CONCEPT.md` §5 (surface: `diff` *(later)*) + §12 (scope: "full diffs/hunks") — the
   canonical contract this milestone fulfills.
3. The code: **`src/diff_stat.rs`** (the pattern every slice extends — `compute`'s off-GIL
   `block_on` tree-diff walk, `read_text`'s text/binary/symlink rule, `count_line_changes`'s
   `ContentDiff::by_line` hunk walk); `src/repo_view.rs::diff_stat` (the read-binding shape to mirror);
   `python/pyjutsu/repo_view.py` + `models.py` (`DiffStat`/`FileStat` — the model pattern) +
   `_pyjutsu.pyi` + `__init__.py` exports; `tests/test_diff_stat.py` + `tests/conftest.py`
   (`diffstat_repo`, `linear_repo` fixtures) + `tests/diff/jj_cli.py` (`diff_stat_totals`,
   `append_config` — the oracle-helper pattern you'll extend with `diff_summary`/`diff_git`).
4. Memory (`.claude/projects/.../memory/`) — `[[m2-slice5-snapshot]]` (off-GIL tree walk),
   `[[pyjutsu-0-5-0-fidelity-completion]]` / `[[pyjutsu-0-4-0-refinements-plan]]` (the verify-and-flag
   discipline this milestone mirrors).

## How to work
- **One slice at a time, in the guide's order (1 → 2 → 3).** Slice 1 establishes the `diff()` binding,
  the `FileChange`/`Diff` models, and the `JjCli` diff oracle helpers; slice 2 adds hunks (the
  meatiest); slice 3 is the conditional copy/rename slice with its explicit flag-it valve.
- For each slice: implement Rust → Python facade + model + `.pyi` + `__init__` export → add the
  differential tests named in the guide → **build, run the full suite, lint** → commit on `main`. Keep
  the suite green at every commit.
- **Honor the guide's VERIFY markers — confirm against the pinned `jj` CLI before trusting them.**
  Specifically: jj 0.38's `--summary` letters for mode-only / type changes / conflicts (slice 1); the
  assertion granularity for hunks — **decide per-file added/removed *multiset* parity first** so you
  need no context-windowing (slice 2); and **whether the git backend emits copy records at all** via
  `jj diff -r <rev> --summary` on a rename fixture (slice 3). **If an item is more than a few lines
  beyond the guide's estimate, ship the clean half and re-flag the rest** — the guide marks exactly
  where: header-exact `@@` hunk grouping (slice 2), and copy/rename detection entirely (slice 3, if
  the backend returns no records). Don't fake it; jj doesn't do similarity-based rename detection, so
  hand-rolling it would diverge from the oracle.
- After the last slice, **bump `0.5.0 → 0.6.0`** (`__init__.py`, `Cargo.toml`, `pyproject.toml`;
  rebuild so `Cargo.lock`/`uv.lock` refresh; commit lockfiles; tag `v0.6.0` annotated and push `main`
  + tag; leave `JJ_LIB_TARGET = "0.38.0"`). Update memory with what landed (models added, hunk
  strategy, copies shipped-or-flagged).

## Non-negotiable constraints
- **Everything through devenv** — `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}`. **Never**
  bare `cargo`/`maturin`/`python`/`pytest`/`jj`. The pinned `jj` 0.38.0 CLI is the **differential
  oracle only**.
- **Thin Rust, rich Python:** `_pyjutsu` returns opaque dicts / scalars / `None`; **no jj-lib type
  crosses FFI**. Models (`FileChange`, `Diff`, `Hunk`, `HunkLine`) / ergonomics / policy stay in
  pure-Python `pyjutsu`.
- **Read-only + off-GIL:** the whole tree-diff + content-read walk is `Send` async — wrap it in
  `py.allow_threads(|| pollster::block_on(async { … }))` exactly like `diff_stat::compute`. No
  transaction, no op, no `!Send` type. **Reuse `diff_stat`'s `read_text`/classifier so the two reads
  never disagree** about a file's text/binary status.
- **Differential parity is *structured*, not byte-rendering:** assert path→kind maps and per-file
  added/removed line *multisets*; normalize the CLI's `--summary`/`--git` text and the binding's
  structure to the same shape.
- **Pins frozen:** `jj-lib =0.38.0`, `gix =0.78.0`, `JJ_LIB_TARGET =0.38.0`; `Cargo.lock` committed.
- **Independent semver:** this is the `0.6.0` milestone, not a jj-aligned number.
- **No AI attribution** anywhere — commits, PRs, code comments, docs. Omit such trailers entirely.
- **Keep every other flag flagged, not faked:** two-revset `diff(from, to)`, word/inline diff, `--git`
  rendering in Rust, context-line options, streaming/iterator diffs, async — and the whole 0.5.0
  backlog (force-push, `--change` push, tag fetch, `--all-remotes`, interactive squash, sparse/`-r`
  workspace, revset/fileset builders) — all remain out of scope this milestone.

**Start by reading `IMPLEMENTATION_GUIDE.md` in full, then `git status` to confirm a clean `main` (with
0.5.0 landed), then implement slice 1. Land each slice green and committed before starting the next.**
