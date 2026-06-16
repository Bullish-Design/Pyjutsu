# Pyjutsu M2 — implementation kickoff prompt

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_IMPLEMENTATION_GUIDE.md` (the approved, jj-lib-0.38-grounded plan — **this is your spine**) → this prompt (orientation + cadence). **This is an implementation session: the deliverable is shipped, tested code, built one vertical slice at a time.**

---

You are implementing **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic +
Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no
subprocess backend, no text parsing. **M0 (build spike) and M1 (read layer) are done, released as
`pyjutsu 0.39.0`, on `main`.** M2 was scoped and the design **approved**; the plan is
`M2_IMPLEMENTATION_GUIDE.md` in this folder. **Read it first — it has verified `file:line` API
refs, the architecture, the slice-by-slice build order, the facade/model surface, the error
taxonomy, and the testing strategy. Do not re-derive what it already settled.** M2 ships as
pyjutsu **`0.40.0`** (versioned independently of jj; pin stays `jj-lib = "=0.38.0"`).

## What's already decided (from the guide — do not relitigate)

- **Snapshot policy:** auto-snapshot `@` on transaction open (CLI parity) + explicit `ws.snapshot()`.
- **Mutation return shape:** `tx` methods return **full `Commit` models immediately**, read back
  from the open `MutableRepo` (it impls `Repo`).
- **Error taxonomy:** add `WorkingCopyError`, `StaleWorkingCopyError` (⊂ `WorkingCopyError`),
  `ImmutableCommitError`; reuse `RevsetError`/`ConflictError`/`BackendError`/`WorkspaceError`.
- **Scope:** the full v1 write surface — mutations + bookmarks + snapshot/checkout + undo/restore +
  stale-WC + **workspace mgmt** (init/add_workspace/forget) + **git interop** (fetch/push/import/export).
- **Transaction lives in Rust** owned inside `PyWorkspace` (behind its `Mutex`); the Python `tx` is
  a thin token. One open transaction per workspace.

## Non-negotiable constraints (carried from M0/M1)

- **jj-lib via PyO3, in-process.** No subprocess/CLI *backend*, no compat shim. Pin stays
  `=0.38.0`; `Cargo.lock` committed. (The pinned `jj` CLI is used **only** as the differential oracle.)
- **Everything runs in the devenv shell** — `devenv tasks run pyjutsu:{build,test,lint}`, or
  `devenv shell -- <cmd>`. **Never** bare `cargo`/`maturin`/`python`/`pytest`/`jj`.
- **Thin Rust, rich Python.** `_pyjutsu` exposes opaque handles + **plain data only** (dicts/lists/
  strings); **never leak `jj-lib` types to Python.** Models, ergonomics, defaults, public contract
  live in pure-Python `pyjutsu` and validate the plain data (frozen, `extra="forbid"`).
- **Off the GIL + plain data** (M1 pattern): compute jj-lib work inside `Python::allow_threads`,
  convert to dicts on the GIL, validate into Pydantic in Python.
- **Differential testing against the pinned `jj` 0.38.0 CLI is the primary correctness net.** Each
  slice asserts equivalence of **change graph + bookmarks + op-log effect**.
- **Faithful, un-opinionated primitives.** No workflow policy (no lanes/frozen trunk). Conflicts
  stay first-class N-sided. Surface divergence and stale `@`; never hide them.
- **Panic safety + error mapping:** every fallible path maps a jj-lib error to a `PyjutsuError`
  subclass; no jj-lib error type crosses the FFI (only its `Display`).
- **No AI-generated attribution** anywhere (commits/PRs/docs/comments).

## Landmines the guide flagged (internalize before slice 0)

1. **`Transaction::commit` asserts `!has_rewrites()` (transaction.rs:136) — a violation ABORTS the
   process, not a catchable error.** Every rewriting mutation must end with `rebase_descendants()`.
   Centralize: `commit_transaction` runs `rebase_descendants()` once before `commit`. Cover with a
   multi-rewrite panic-safety test.
2. **Auto-snapshot is a *separate preceding* operation, not folded.** Verified empirically: a
   mutation on a **dirty** `@` ⇒ **2** ops (`snapshot working copy`, then the command op); on a
   **clean** `@` ⇒ **1**; a tx whose body raises ⇒ **0**. "1 tx == 1 op" describes the mutation tx.
3. **Real identity is required.** M1's `load` used empty `UserSettings::from_config(with_defaults())`.
   M2 must load the real stacked (user + repo) config so `CommitBuilder` author/committer match the
   CLI's commit ids. **Slice 0 verifies the public stacked-config loader in jj-lib 0.38**
   (`config.rs`/`config_resolver.rs`); if only manual layering is public, replicate the CLI's.

## Two open items to verify during implementation (not pre-solved)

- **`rebase`** (slice 8): confirm exact `move_commits` / `MoveCommitsTarget` / `MoveCommitsLocation`
  field names against rewrite.rs:525–600 before wiring.
- **`undo`** (slice 7): `merge`-based reverse (repo.rs:1831) is the building block; the differential
  test vs `jj undo` is the contract. If a partial-restore case drifts, replicate the CLI's
  view-portion restore.

## How to work

1. **Read `M2_IMPLEMENTATION_GUIDE.md` end to end**, then skim the shipped M1 Rust (`src/`:
   `lib.rs`, `errors.rs`, `convert.rs`, `repo_view.rs`, `workspace.rs`) + Python
   (`python/pyjutsu/`) + tests (`tests/`, `tests/diff/jj_cli.py`, `tests/conftest.py`) so you reuse
   the established off-GIL + plain-data + differential patterns rather than reinventing them.
2. **Build the vertical slices in order (§4 of the guide), one at a time:**
   `0` identity + tx scaffolding → `1` describe → `2` new → `3` edit/abandon → `4` bookmarks →
   `5` snapshot + auto-snapshot → `6` stale-WC → `7` undo/restore → `8` rebase/squash/restore →
   `9` workspace mgmt → `10` git import/export + remotes → `11` git fetch/push.
3. **Per slice:** thin Rust + Python facade + extend the `JjCli` driver with the write verbs it
   lacks + a differential test asserting **graph + bookmarks + op-log effect**, plus the
   "1 tx == 1 op" (and snapshot/rollback op-count) invariants. **Run `devenv tasks run
   pyjutsu:{build,test,lint}` and get it green before moving to the next slice.** Stop and report
   at each slice boundary so the work stays reviewable; the milestone can release incrementally.
4. **When citing jj-lib API, read the pinned source** at
   `~/.cargo/registry/src/index.crates.io-*/jj-lib-0.38.0/src/` — the guide's `file:line` refs are
   verified, but confirm before relying on any signature the guide marked "verify at slice N".
5. **On completion of all slices:** bump `__version__ = "0.40.0"`, update concept §3/§5 status to
   "M2 implemented", regenerate goldens, confirm the `JJ_VERSION == JJ_LIB_TARGET` tripwire is green.

## Guardrails

- Don't add a CLI/subprocess *backend*, a migration shim, or old-Pyjutsu compatibility.
- Don't leak `jj-lib` types across the FFI or put business/workflow logic in Rust.
- Don't bake workflow policy (lanes, frozen trunk) into Pyjutsu — faithful primitives only.
- Don't silently operate on a stale `@`; don't hide operation divergence.
- Don't forget `rebase_descendants()` before commit (process-abort landmine #1).
- Don't run bare host tooling — everything through devenv. No AI attribution.
- Don't tie the pyjutsu version to jj (M2 is the independent bump `→ 0.40.0`).

**Start by reading `M2_IMPLEMENTATION_GUIDE.md` and skimming the M1 code, then implement Slice 0
(real `UserSettings` + the `PyWorkspace` transaction scaffolding + the Python `Workspace.transaction()`
context manager and `Transaction` token), with its differential test green, and stop for review
before Slice 1.**
