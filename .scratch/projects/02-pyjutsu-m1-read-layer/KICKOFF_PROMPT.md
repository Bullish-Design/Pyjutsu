# Pyjutsu M1 — implementation kickoff prompt

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → this project's
> `M1_IMPLEMENTATION_GUIDE.md` (the concrete, jj-lib-0.38-grounded plan) → this prompt
> (orientation + first moves). Read the guide and the concept before writing code.

---

You are implementing **M1 (the read layer)** of **Pyjutsu** (`import pyjutsu`): a general-purpose,
Pythonic + Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process,
no subprocess, no text parsing. M0 (the build spike + `Workspace.load`/`working_copy`) is **done
and on `main`**. Your job is the read surface.

## One-sentence thesis

Expose jj's reads — `resolve`, `log`, `bookmarks`, `operations`, `diff_stat`, `conflicts` (plus
`at_operation`/`head_operation`) — as **pure, side-effect-free** methods that return **Pydantic
models**, evaluated through `jj-lib` in-process and **differential-tested against the pinned `jj`
0.38 CLI**.

## Two architecture decisions are already made (approved — do not relitigate)

1. **RepoView split.** All reads live on a new `RepoView` backed by `Arc<ReadonlyRepo>` (an
   immutable repo at one operation). `Workspace` keeps path identity + (future M2) mutation; its
   read conveniences delegate to a fresh head view. `ws.at_operation(op)` returns a historical
   `RepoView`. This mirrors jj-lib's own model, needs no `Mutex`, and lets you release the GIL.
2. **Reads never mutate (M1).** Reads operate at the chosen operation **without snapshotting**
   (jj's `--ignore-working-copy` is the default and only mode). Explicit `snapshot()` and all
   mutations are **M2** — out of scope here.

## Non-negotiable constraints (carried from M0)

- **jj-lib via PyO3, in-process. No subprocess/CLI backend, no compat shim.** Pin stays
  `jj-lib = "=0.38.0"`; `Cargo.lock` committed.
- **Everything runs inside the `devenv.sh` shell** — `devenv shell -- <cmd>`. Never bare
  `cargo`/`maturin`/`python`/`pytest`/`jj`. The devenv pins Rust, maturin, and the **matching
  `jj` 0.38.0 CLI** used for differential tests.
- **Thin Rust, rich Python.** `_pyjutsu` exposes opaque handles (`PyWorkspace`, `PyRepoView`) +
  **plain data only** (dicts/lists/strings); **never leak `jj-lib` types to Python**. All models,
  ergonomics, defaults, and the public contract live in pure-Python `pyjutsu`.
- **Differential testing against the pinned `jj` CLI is the primary correctness + drift net.**
- **Faithful, un-opinionated primitives.** No workflow policy. Mirror jj. Conflicts are
  first-class N-sided `Merge`, never a bool.
- **No AI-generated attribution** in commits/PRs/docs.

## What's already in place (M0)

- `Cargo.toml`/`pyproject.toml` (maturin mixed, `pyo3 0.24` abi3-py313), `src/lib.rs`
  (`version`, `PyjutsuError`, `PyWorkspace { Mutex<Workspace> }` with `load`/`name`/
  `workspace_root`/`working_copy`), `python/pyjutsu/` (`Workspace`, `Commit`, `ChangeId`/
  `CommitId`, errors, `__init__`, `_pyjutsu.pyi`, `py.typed`).
- `tests/diff/jj_cli.py` (pinned-`jj` driver), `conftest.py` (`scratch_repo`), `test_build.py`,
  `test_workspace_load.py`. devenv + `nix/pyjutsu.nix` tasks.
- Established: change_id via `ChangeId::reverse_hex()` (z-k form); commit_id via `hex()`.
  `Arc<ReadonlyRepo>` is `Send + Sync` (no `Mutex`, GIL-releasable); `Workspace` is `Send`-only.

## How to work

1. **Read first**: `docs/PYJUTSU_CONCEPT.md`, then `M1_IMPLEMENTATION_GUIDE.md` (it contains a
   **verified jj-lib 0.38 API reference** with file:line refs — the revset pipeline, bookmarks,
   op-log, conflicts, diff, signatures — so you don't re-derive them). Skim the existing M0 code.
2. **Build vertically, differential-test each slice before the next** (order in the guide §3):
   plumbing (errors→Rust, module split, `PyRepoView` shell, `Workspace.head()`) →
   `resolve` (brings up the revset helper) → `log` (enriches `Commit`) → `operations` +
   `at_operation` → `bookmarks` → `conflicts` → `diff_stat` (the risk item — last) → golden
   fixtures + sweep.
3. **Off-GIL compute**: each read evaluates jj-lib into plain Rust intermediate structs inside
   `Python::allow_threads`, then converts to dicts after re-acquiring the GIL.
4. **Validate at the boundary**: Python feeds plain dicts to `Model.model_validate(...)`; models
   are `frozen`, `extra="forbid"` (drift tripwire).

## First moves

- Confirm the env is healthy: `devenv shell -- devenv tasks run pyjutsu:build` then `pyjutsu:test`
  (M0 should be green).
- Split the Rust ext into modules and move the exception hierarchy into Rust
  (`PyjutsuError` + `RevsetError`/`ConflictError`/`BackendError`/`WorkspaceError`), with
  `errors.py` re-exporting them. Stand up `PyRepoView` + `PyWorkspace.head_view()` and move
  `working_copy()` onto the view; add Python `RepoView` + `Workspace.head()`. Keep M0 tests green.
- Then implement `resolve` to bring up the revset pipeline (guide §4), with the first
  differential test, and proceed down the build order.

## Guardrails

- Don't add a CLI/subprocess backend, a migration shim, or old-Pyjutsu compatibility.
- Don't leak `jj-lib` types across the FFI or put business logic in Rust.
- Don't make reads mutate; don't implement `snapshot()`/mutations (that's M2).
- Don't run bare host tooling — everything through devenv. No AI attribution in commits/docs.

**Start by reading `docs/PYJUTSU_CONCEPT.md` and `M1_IMPLEMENTATION_GUIDE.md`, verify M0 is green
in devenv, then build the read layer vertically — `resolve` first — differential-testing each
slice against the pinned `jj`.**
