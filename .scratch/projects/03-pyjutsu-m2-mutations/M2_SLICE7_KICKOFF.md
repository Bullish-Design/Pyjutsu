# Pyjutsu M2 — Slice 7 kickoff prompt (operation log writes: `undo` / `restore_operation`)

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_SLICE7_GUIDE.md` (**your spine for this slice — verified, detailed**) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_CONTINUATION_GUIDE.md` (the corrected slices 2–11 spine: §1 corrections, §2 conventions, §4 slice plans) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_IMPLEMENTATION_GUIDE.md` (original plan; build order/model surface/error taxonomy — but the continuation guide and slice guide win where they disagree) → this prompt (orientation + cadence). **This is an implementation session: the deliverable is shipped, tested code, one vertical slice.**

---

You are continuing **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic +
Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no subprocess
backend, no text parsing. **M0, M1 (read layer, released `0.39.0`), and M2 slices 0–6 are done,
committed, and pushed on `main`** (slice 6 = `cfbb458`; working tree clean — start from `main`).
Slices 0–1 shipped as pyjutsu `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2
bumps to **`0.41.0`** (versioned independently of jj; pin stays `jj-lib = "=0.38.0"`).

**Read `M2_SLICE7_GUIDE.md` end to end first** — it is the verified, detailed plan for this slice
(APIs at `file:line` into the pinned 0.38.0 source, both Rust method bodies + the shared tail, the
`checkout_wc` refactor, the GIL split, the Python wiring, and the differential tests). Then skim the
code you build directly on in `src/workspace.rs`: `PyWorkspace::snapshot` (the internal-transaction
pattern — `repo.start_transaction()` … `tx.commit(desc)` inside a `PyWorkspace` method, holding the
workspace `Mutex` throughout), `checkout_wc` (`src/workspace.rs:126` — the post-op on-disk checkout
you must refactor so it can run under a held lock without deadlocking), and `at_operation`
(`src/workspace.rs:202` — already resolves op specs via `op_walk::resolve_op_for_load` + `to_py_err`,
which `undo`/`restore_operation` reuse). Also skim `src/convert.rs` (`OperationData::{build,to_dict}`,
the op row both methods return), `src/transaction.rs` (`PyTransaction::commit`'s checkout call site —
unchanged by the refactor), `python/pyjutsu/workspace.py` (where the two facades go; `Operation`
already imported), and `tests/test_at_operation.py` + `tests/test_new.py` + `tests/conftest.py` (the
differential harness: `_copy_repo`, `linear_repo`/`scratch_repo`, `jj.op_log_ids`, `jj.commit_id`,
`jj.change_ids`).

## What's already done (slices 0–6 — do not redo)

- **Slice 0/1** — identity + tx scaffolding; `tx.describe` with commit-id parity.
- **Slice 2** — `tx.new(parents=None)` + the reusable post-commit on-disk checkout (`checkout_wc`).
- **Slice 3** — `tx.edit` / `tx.abandon` (`map_edit_err`; root-abandon panic guard).
- **Slice 4** — five bookmark verbs (no checkout/rebase; bookmark writes move no commit).
- **Slice 5** — `Workspace.snapshot() -> Operation | None` + auto-snapshot on `Transaction.__enter__`.
- **Slice 6** — `Workspace.is_stale()` / `update_stale()`; mutating/snapshotting a stale `@` raises
  `StaleWorkingCopyError`.

## This slice: operation-log writes (per `M2_SLICE7_GUIDE.md`)

The two **`Workspace`-level** op-log verbs the concept lists (`docs/PYJUTSU_CONCEPT.md:112–114,133`),
each managing its own internal `Transaction` like `snapshot` (not `PyTransaction` methods), each
publishing exactly one operation and checking out `@` if it moved:

1. **Refactor `checkout_wc` first (`src/workspace.rs`)** — extract the body into an associated
   `checkout_locked(py, ws: &mut Workspace, op_id, commit)`; have `checkout_wc` lock then delegate.
   `undo`/`restore_operation` hold the workspace `Mutex` for the whole sequence, so they call
   `checkout_locked` directly — calling `checkout_wc` (which re-locks) would **deadlock**. Pure
   extraction; `PyTransaction::commit`'s call site is unchanged.
2. **`PyWorkspace::undo(operation=None) -> dict`** — resolve the op (default `@`) + its **single**
   parent (off GIL); refuse a 0-parent (init) or >1-parent (merge) op with `PyjutsuError`;
   `tx.repo_mut().merge(base = bad_repo, other = parent_repo)` applies the reverse; commit
   `"undo operation <hex>"`; checkout `@` if it moved; return the op dict. No `rebase_descendants`
   (merge registers no rewrites).
3. **`PyWorkspace::restore_operation(operation) -> dict`** — resolve the target op (off GIL);
   `tx.repo_mut().set_view(target_op.view()?.store_view().clone())` (the **high-level `View` → trap**:
   `Operation::view()` is *not* the `op_store::View` `set_view` wants — bridge with `.store_view()`);
   commit `"restore to operation <spec>"`; checkout `@` if it moved; return the op dict.
4. **Python facades** — `Workspace.undo(operation=None) -> Operation`,
   `Workspace.restore_operation(operation) -> Operation`.
5. **Stubs** — add `def undo(self, operation: str | None = ...) -> dict[str, object]: ...` and
   `def restore_operation(self, operation: str) -> dict[str, object]: ...` to `PyWorkspace` in
   `_pyjutsu.pyi`.

- **Verify, don't assume (slice-5 lesson):** grep the pinned source for every API the guide names.
  The two traps this slice: (a) `Operation::view()` returns the high-level `crate::view::View`, so
  use `.store_view().clone()` for `set_view` (view.rs:560); (b) **`view_with_desired_portions_restored`
  is NOT in jj-lib 0.38** — it's jj-cli-only, so the CLI can restore only *some* portions (e.g. leave
  remote-tracking refs). For the **all-portions default on local-only histories** the plain
  `merge`/`set_view` primitives produce a byte-identical view — **keep every test local-only** (no git
  remote between the operations). Partial-restore portions are a **documented refinement, out of
  scope**; flag it, don't hardcode a subset.
- **State parity, not op-id parity:** op ids embed wall-clock time + hostname (transaction.rs:73/149),
  so the binding's new op id won't equal the CLI's. **Assert resulting repo state** (the `@` commit
  id, the `::@` graph, bookmarks) across two byte-identical copies (binding vs CLI), never op-id
  equality. Leave `debug.operation-timestamp` unpinned (it wouldn't equalize the op description
  wording anyway).

- **Differential tests (`tests/test_undo.py`)** per guide §4: undo a `describe`/`new` matches `jj
  undo` (state parity + checkout fires when `@` moves); undo a **specific** op id; undoing the
  init/root op raises `PyjutsuError`; `restore_operation` to an earlier op matches `jj op restore`
  (state parity); restore-to-head is a state no-op; an invalid op spec raises `PyjutsuError`. **Re-run
  the whole suite** — these are additive and the `checkout_wc` refactor is a pure extraction, so all
  prior tests (incl. `test_new`'s checkout asserts + `test_snapshot`) must stay green; confirm.

Run `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` green, then **stop and report at the
slice boundary** before slice 8 (`rebase` / `squash` / `restore` — **verify the rewrite.rs field
names at that slice**). Commit on `main` (`Implement M2 slice 7: undo / restore_operation`).

## Non-negotiable constraints (carried)

- jj-lib via PyO3, in-process; pin `=0.38.0`; `Cargo.lock` committed; the pinned `jj` 0.38.0 CLI is
  **only** the differential oracle. Everything through devenv — never bare `cargo`/`maturin`/
  `python`/`pytest`/`jj`.
- Thin Rust, rich Python: `_pyjutsu` returns opaque handles + **plain data only** (dicts/lists/
  strings/bools/`None`); never leak jj-lib types; models/ergonomics/policy live in pure-Python `pyjutsu`.
- **GIL discipline:** the op-store reads + the checkout I/O run under `py.allow_threads`; the `!Send`
  `Transaction` (merge/set_view/commit) runs on the GIL between those spans; the workspace `Mutex` is
  held for the whole sequence (so the checkout must go through `checkout_locked`, not `checkout_wc`).
- **Faithful primitive:** `undo` = `merge(base = bad, other = parent)`; `restore_operation` =
  `set_view(target.view)`. Each publishes exactly one op and checks out `@` if it moved. Undoing the
  init/merge op raises `PyjutsuError`. Every fallible path maps a jj-lib error to a `PyjutsuError`
  subclass; only its `Display` crosses FFI. **No AI attribution** anywhere.

**Start by reading `M2_SLICE7_GUIDE.md` and skimming `PyWorkspace::snapshot` + `checkout_wc` +
`at_operation` in `src/workspace.rs` (the internal-tx pattern, the checkout to refactor, the op-spec
resolution this slice reuses), then refactor `checkout_wc`, implement `undo` and `restore_operation`,
their facades + stubs, and the `tests/test_undo.py` differential tests, with the whole suite green,
and stop for review before slice 8.**
