# Pyjutsu M2 — Slice 6 kickoff prompt (stale working copy: `is_stale` / `update_stale`)

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_SLICE6_GUIDE.md` (**your spine for this slice — verified, detailed**) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_CONTINUATION_GUIDE.md` (the corrected slices 2–11 spine: §1 corrections, §2 conventions, §4 slice plans) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_IMPLEMENTATION_GUIDE.md` (original plan; build order/model surface/error taxonomy — but the continuation guide and slice guide win where they disagree) → this prompt (orientation + cadence). **This is an implementation session: the deliverable is shipped, tested code, one vertical slice.**

---

You are continuing **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic +
Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no subprocess
backend, no text parsing. **M0, M1 (read layer, released `0.39.0`), and M2 slices 0–5 are done,
committed, and pushed on `main`** (slice 5 = `13deb07`; working tree clean — start from `main`).
Slices 0–1 shipped as pyjutsu `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2
bumps to **`0.41.0`** (versioned independently of jj; pin stays `jj-lib = "=0.38.0"`).

**Read `M2_SLICE6_GUIDE.md` end to end first** — it is the verified, detailed plan for this slice
(APIs at `file:line` into the pinned 0.38.0 source, both Rust method bodies, the GIL split, the
forced-checkout decision, the Python wiring, the tests, and the **verified staleness reproduction**).
Then skim the slice 5 code you build directly on: `src/workspace.rs` — especially `PyWorkspace::snapshot`
(it already calls `WorkingCopyFreshness::check_stale` and maps the stale states to
`StaleWorkingCopyError`; you reuse the same load-at-head → resolve `@` → lock skeleton) and
`checkout_wc`/`Workspace::check_out` (the off-GIL checkout; `update_stale` calls `check_out` with
`old_tree = None` to *force* it). Also skim `src/convert.rs` (`CommitData::{build,to_dict}` for the
returned `@`), `src/errors.rs` (`StaleWorkingCopyError` exists; `map_workingcopy_err`/`map_backend_err`),
`python/pyjutsu/workspace.py` (where the two facades go; `Commit` already imported), and
`tests/conftest.py` + `tests/diff/jj_cli.py` + `tests/test_new.py` (the differential harness;
`_copy_repo`, `linear_repo`, `change_ids`, `commit_id`, `op_log_ids`).

## What's already done (slices 0–5 — do not redo)

- **Slice 0/1** — identity + tx scaffolding; `tx.describe` with commit-id parity.
- **Slice 2** — `tx.new(parents=None)` + the reusable post-commit on-disk checkout (`checkout_wc`).
- **Slice 3** — `tx.edit` / `tx.abandon` (`map_edit_err`; root-abandon panic guard).
- **Slice 4** — five bookmark verbs (no checkout/rebase; bookmark writes move no commit).
- **Slice 5** — `Workspace.snapshot() -> Operation | None` + auto-snapshot on `Transaction.__enter__`;
  off-GIL snapshot; **already calls `check_stale` and raises `StaleWorkingCopyError` on a stale `@`**
  (so the default mutation path — which auto-snapshots first — already refuses a stale `@`).

## This slice: stale working copy (per `M2_SLICE6_GUIDE.md`)

**The explicit, queryable staleness surface** the CLI exposes as `jj workspace update-stale` + its
check — building directly on the `check_stale` call slice 5 introduced.

1. **`PyWorkspace::is_stale(&self) -> bool` (Rust, `src/workspace.rs`)** — load at head → resolve `@`
   → take the WC lock → `WorkingCopyFreshness::check_stale` → `true` for
   `WorkingCopyStale | SiblingOperation`, else `false` (drop the lock). Read-only probe.
2. **`PyWorkspace::update_stale(&self) -> Option<dict>`** — same skeleton; if **not** stale return
   `None` (matches the CLI's "not stale" no-op); else **forced checkout** of `@` at head via
   `ws.check_out(op_id, None, &wc_commit)` off the GIL (`old_tree = None` bypasses the
   `ConcurrentCheckout` guard that `checkout_wc`'s `Some(old_tree)` would trip on the stale tree),
   then return the reconciled `@` as a plain dict (reuse `CommitData`).
3. **Python facades** — `Workspace.is_stale() -> bool` and `Workspace.update_stale() -> Commit | None`.
4. **Stubs** — add `def is_stale(self) -> bool: ...` and
   `def update_stale(self) -> dict[str, object] | None: ...` to `PyWorkspace` in `_pyjutsu.pyi`.

- **Reproduce staleness for the tests (verified with the pinned CLI):** within one workspace the
  binding keeps `@`/WC in lockstep, so induce it externally — `jj --ignore-working-copy edit <A>`
  advances the repo `@` without snapshot/checkout, leaving the on-disk tree behind. Then the binding
  reports `is_stale()`, refuses to mutate/snapshot, and `update_stale()` reconciles (diff vs
  `jj workspace update-stale`). The guide §0 has the exact, confirmed sequence on `linear_repo`.
- **Verify, don't assume:** `repo.operation().id().clone()` is the `OperationId` `check_out` wants
  (as `snapshot`/`commit` already use); `&*repo` coerces to the `&dyn Repo` `CommitData::build` takes;
  the two sequential WC locks (check, then `check_out`'s own) are fine because the first drops first.
  **Heed the slice-5 lesson: grep the pinned source for every API a guide names before trusting it**
  (slice 5's original guide mislabeled `set_tree_id`/`tree_id`).
- **Documented refinements (flag, don't silently hardcode):** the **missing-`@` recovery** path
  (`create_and_check_out_recovery_commit`, working_copy.rs:425) when the WC commit was abandoned is
  out of scope; `Updated` is treated as fresh because we always load at head (can't arise in-process).
- **Differential tests (`tests/test_stale.py`)** per guide §4: `is_stale` true after external edit /
  false when fresh; `update_stale` reconciles + matches the CLI's checked-out `@` (commit id + on-disk
  files) and clears staleness; `update_stale` is `None`/no-op when fresh; mutation **and** snapshot on
  a stale `@` raise `StaleWorkingCopyError` (op log unchanged); update-then-mutate succeeds. **Re-run
  the whole suite** (these methods are additive — all prior tests must stay green; confirm).

Run `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` green, then **stop and report at the
slice boundary** before slice 7 (`undo` / `restore_operation`). Commit on `main` (`Implement M2 slice
6: stale working copy`).

## Non-negotiable constraints (carried)

- jj-lib via PyO3, in-process; pin `=0.38.0`; `Cargo.lock` committed; the pinned `jj` 0.38.0 CLI is
  **only** the differential oracle. Everything through devenv — never bare `cargo`/`maturin`/
  `python`/`pytest`/`jj`.
- Thin Rust, rich Python: `_pyjutsu` returns opaque handles + **plain data only** (dicts/lists/
  strings/bools/`None`); never leak jj-lib types; models/ergonomics/policy live in pure-Python `pyjutsu`.
- **GIL discipline:** the WC lock + the forced `check_out` (all I/O) run under `py.allow_threads`; the
  workspace `Mutex` is held for the whole sequence.
- **Faithful primitive:** `update_stale` is a no-op returning `None` on a fresh WC (matches the CLI);
  it reconciles only a genuinely stale one by checking out the recorded `@`. Mutating/snapshotting a
  stale `@` raises `StaleWorkingCopyError`. Every fallible path maps a jj-lib error to a
  `PyjutsuError` subclass; only its `Display` crosses FFI. **No AI attribution** anywhere.

**Start by reading `M2_SLICE6_GUIDE.md` and skimming `PyWorkspace::snapshot` + `Workspace::check_out`
in `src/workspace.rs` (the skeleton + the forced-checkout primitive this slice reuses), then implement
`is_stale` and `update_stale`, their facades + stubs, and the `tests/test_stale.py` differential
tests, with the whole suite green, and stop for review before slice 7.**
