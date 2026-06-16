# Pyjutsu M2 — Slice 5 kickoff prompt (snapshot + auto-snapshot on tx open)

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_SLICE5_GUIDE.md` (**your spine for this slice — verified, detailed**) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_CONTINUATION_GUIDE.md` (the corrected slices 2–11 spine: §1 corrections, §2 conventions, §4 slice plans) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_IMPLEMENTATION_GUIDE.md` (original plan; build order/model surface/error taxonomy + the exact §3.5 snapshot sequence — but the continuation guide and slice guide win where they disagree) → this prompt (orientation + cadence). **This is an implementation session: the deliverable is shipped, tested code, one vertical slice.**

---

You are continuing **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic +
Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no subprocess
backend, no text parsing. **M0, M1 (read layer, released `0.39.0`), and M2 slices 0–4 are done,
committed, and pushed on `main`** (slice 4 = `9e5ce6d`; working tree clean — start from `main`).
Slices 0–1 shipped as pyjutsu `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2
bumps to **`0.41.0`** (versioned independently of jj; pin stays `jj-lib = "=0.38.0"`).

**Read `M2_SLICE5_GUIDE.md` end to end first** — it is the verified, detailed plan for this slice
(APIs at `file:line` into the pinned 0.38.0 source, the exact Rust `snapshot` body, the GIL split,
the `SnapshotOptions` decision, the Python wiring, the tests). Then skim the slice 2–4 code so you
reuse the established patterns: `src/workspace.rs` (esp. `checkout_wc` — slice 2's off-GIL
`py.allow_threads` checkout, which snapshot mirrors; `begin_transaction`'s load-at-head + the
single-tx guard), `src/transaction.rs` (the mutation template + `commit`'s `rebase_descendants`/
checkout; `set_is_snapshot` lives on the native `Transaction`), `src/convert.rs` (reuse
`OperationData::{build,to_dict}` for the returned op), `src/errors.rs` (`StaleWorkingCopyError`
already exists; `map_workingcopy_err`/`map_backend_err`), `python/pyjutsu/workspace.py` +
`transaction.py` (the `auto_snapshot` flag is already threaded — `__enter__` just needs to call the
new native `snapshot()` first), and `tests/conftest.py` + `tests/diff/jj_cli.py` (the differential
harness; `op_log_ids`, `op_head_description`, `commit_id`, `change_id`).

## What's already done (slices 0–4 — do not redo)

- **Slice 0/1** — identity + tx scaffolding (real stacked `UserSettings`, `Workspace.transaction()`
  ctx mgr, unsendable `PyTransaction`, single-open-tx guard); `tx.describe` with commit-id parity.
- **Slice 2** — `tx.new(parents=None)` **and the reusable post-commit on-disk checkout**
  (`PyWorkspace::checkout_wc`, off the GIL whenever a committed tx moves `@`).
- **Slice 3** — `tx.edit` / `tx.abandon` (`map_edit_err`; root-abandon panic guard).
- **Slice 4** — five bookmark verbs (`create`/`set`/`delete`/`track`/`untrack`); no checkout/rebase
  (bookmark writes rewrite no commit); precondition guards raise the `PyjutsuError` base; differential
  tests filter the colocated `@git` mirror (the CLI git-exports bookmark writes; the thin binding
  does no git interop yet).

## This slice: snapshot + auto-snapshot (per `M2_SLICE5_GUIDE.md`)

**The first off-GIL I/O piece of M2 — the mirror image of slice 2's checkout.** Checkout writes `@`'s
tree *out*; snapshot reads the working copy *in*, records it as a rewrite of `@`, and publishes a
**separate `snapshot working copy` operation** (concept §0.1) — exactly what the pinned CLI does
before every command.

1. **`PyWorkspace::snapshot` (Rust, `src/workspace.rs`)** — the §2 sequence: load repo at head + `@`;
   `start_working_copy_mutation` (WC lock) → `WorkingCopyFreshness::check_stale` → `snapshot(&SnapshotOptions)`
   off the GIL → **if the tree is unchanged, drop the lock and return `None` (no op)** → else, on the
   GIL (the `Transaction` is `!Send`), `rewrite_commit(@).set_tree_id(new).write()` + `rebase_descendants()`
   + `set_is_snapshot(true)` + `commit("snapshot working copy")` → off the GIL, `locked_ws.finish(new_op)`.
   Returns the new operation as a plain dict (reuse `OperationData`), or `None`. Stale/sibling `@` ⇒
   `StaleWorkingCopyError` (the full `is_stale`/`update_stale` surface is **slice 6**, not now).
2. **Explicit `Workspace.snapshot() -> Operation | None`** (Python facade) — validate the dict → the
   `Operation` model (already imported), or `None`.
3. **Auto-snapshot on tx open** — in `Transaction.__enter__`, if `self._auto_snapshot`, call
   `self._handle.snapshot()` **before** `begin_transaction`. So **dirty `@` + mutation ⇒ 2 ops**
   (snapshot then mutation); **clean ⇒ 1**; **`auto_snapshot=False` ⇒ the mutation sees `@` as-is**.
4. **Stub:** add `def snapshot(self) -> dict[str, object] | None: ...` to `PyWorkspace` in `_pyjutsu.pyi`.

- **`SnapshotOptions` (the one open review item):** no `from_settings` in jj-lib 0.38, so build it
  directly — `base_ignores = GitIgnoreFile::empty()`, `start_tracking_matcher = &EverythingMatcher`,
  `force_tracking_matcher = &NothingMatcher`, `max_new_file_size = 1 << 20` (the CLI's 1 MiB default).
  This is exact for the fixtures (no `.gitignore`, no huge files; `.jj`/`.git` are excluded internally
  by the snapshotter). **Flag full gitignore/auto-track-from-settings fidelity as a documented
  refinement — don't silently hardcode without calling it out.**
- **Verify, don't assume:** the `Commit` tree-id getter + `CommitBuilder::set_tree_id(MergedTreeId)`
  (grep `commit.rs`/`commit_builder.rs`); `start_transaction()` on the loaded `Arc<ReadonlyRepo>`; the
  `block_on` pattern (`pollster::block_on(...)`, as `transaction.rs` already uses) wrapped in
  `py.allow_threads`.
- **Differential tests (`tests/test_snapshot.py`)** vs the CLI's implicit snapshot (force it with
  `jj status`/`jj log` on the copy): dirty `@` ⇒ op published, `is_snapshot True`, description
  `"snapshot working copy"`, **`@` commit id matches the CLI's** (preserved change id + pinned
  timestamp + identical tree); clean `@` ⇒ `None`/0 ops; **auto-snapshot ⇒ 2 ops** on a dirty `@` +
  mutation, **1 op** on a clean one; `auto_snapshot=False` ⇒ 1 op and the edit not captured. **Re-run
  the whole suite** — wiring auto-snapshot touches every `with ws.transaction(...)` (clean fixtures
  snapshot to nothing, so existing op counts hold; confirm).

Run `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` green, then **stop and report at the
slice boundary** before slice 6 (stale working copy). Commit on `main` (`Implement M2 slice 5:
snapshot + auto-snapshot`).

## Non-negotiable constraints (carried)

- jj-lib via PyO3, in-process; pin `=0.38.0`; `Cargo.lock` committed; the pinned `jj` 0.38.0 CLI is
  **only** the differential oracle. Everything through devenv — never bare `cargo`/`maturin`/
  `python`/`pytest`/`jj`.
- Thin Rust, rich Python: `_pyjutsu` returns opaque handles + **plain data only** (dicts/lists/
  strings/`None`); never leak jj-lib types; models/ergonomics/policy live in pure-Python `pyjutsu`.
- **GIL discipline:** the `Send`, I/O-heavy work (WC lock, disk walk, tree write, `finish`) runs under
  `py.allow_threads`; the `!Send` recording `Transaction` span stays on the GIL, sandwiched between
  the off-GIL spans; the workspace `Mutex` is held for the whole sequence.
- **A snapshot is a separate operation** — never folded into a mutation tx; a clean WC publishes **no**
  operation; `set_is_snapshot(true)` flags it like the CLI. Faithful primitive: no auto-track
  heuristics; `auto_snapshot=False` honored literally. Every fallible path maps a jj-lib error to a
  `PyjutsuError` subclass; only its `Display` crosses FFI. **No AI attribution** anywhere.

**Start by reading `M2_SLICE5_GUIDE.md` and skimming `checkout_wc` + `begin_transaction` in
`src/workspace.rs` (the off-GIL pattern snapshot mirrors), then implement `PyWorkspace::snapshot`, the
`Workspace.snapshot()` facade, and the `__enter__` auto-snapshot wiring, with their differential tests
green, and stop for review before slice 6.**
