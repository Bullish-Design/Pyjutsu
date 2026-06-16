# Pyjutsu M2 — Slice 3 kickoff prompt (`edit` / `abandon`)

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_SLICE3_GUIDE.md` (**your spine for this slice — verified, detailed**) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_CONTINUATION_GUIDE.md` (the corrected slices 2–11 spine: §1 corrections, §2 conventions, §4 slice plans) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_IMPLEMENTATION_GUIDE.md` (original plan; build order/model surface/error taxonomy — but the continuation guide wins where they disagree) → this prompt (orientation + cadence). **This is an implementation session: the deliverable is shipped, tested code, one vertical slice.**

---

You are continuing **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic +
Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no subprocess
backend, no text parsing. **M0, M1 (read layer, released `0.39.0`), and M2 slices 0–2 are done and
committed on `main`** (slice 2 = `4466f74`; working tree clean — start from `main`). Slices 0–1
shipped as pyjutsu `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2 bumps to
**`0.41.0`** (versioned independently of jj; pin stays `jj-lib = "=0.38.0"`).

**Read `M2_SLICE3_GUIDE.md` end to end first** — it is the verified, detailed plan for this slice
(APIs, the root-abandon panic guard, the new error mapping, tests). Then skim the slice 2 code so you
reuse the established patterns: `src/transaction.rs` (the `describe`/`new` mutation template +
`commit`'s post-commit checkout), `src/workspace.rs` (`checkout_wc`, `begin_transaction`),
`src/errors.rs` (`map_*` helpers + the taxonomy), `python/pyjutsu/transaction.py`, and
`tests/test_new.py` + `tests/test_describe.py` + `tests/conftest.py` + `tests/diff/jj_cli.py` (the
differential harness).

## What's already done (slices 0–2 — do not redo)

- **Slice 0/1** — identity + tx scaffolding (real stacked `UserSettings`, `Workspace.transaction()`
  ctx mgr, unsendable `PyTransaction`, single-open-tx guard); `tx.describe` with commit-id parity.
- **Slice 2** — `tx.new(parents=None)` **and the reusable post-commit on-disk checkout** in
  `PyTransaction::commit`: when a committed tx moves `@`, `PyWorkspace::checkout_wc` runs
  `Workspace::check_out` **off the GIL**, keeping the working copy in lockstep with the repo head.
  `PyTransaction` now holds `Py<PyWorkspace>` + `starting_wc_commit`. `map_workingcopy_err` added.

## This slice: `edit` / `abandon` (per `M2_SLICE3_GUIDE.md`)

1. **`tx.edit(commit) -> Commit`** — `MutableRepo::edit(name, &commit)` (repo.rs:1526) points `@` at
   an existing commit (no new commit) → `rebase_descendants()` (old `@` may be abandoned if
   discardable) → re-read + return. `@` moved ⇒ `commit`'s checkout fires. Editing root →
   `EditCommitError::RewriteRootCommit` → `ImmutableCommitError`.
2. **`tx.abandon(commit) -> None`** — `record_abandoned_commit(&commit)` (repo.rs:1005) →
   `rebase_descendants()` rebases children onto the parent(s); abandoning `@` advances `@` to a fresh
   empty commit (checkout fires). **Guard root first:** `record_abandoned_commit` `assert_ne!`s on
   root (repo.rs:1006) → **panic**, so check `target.id() == repo.store().root_commit_id()` and raise
   `ImmutableCommitError` yourself (unlike `edit`, which returns a typed error).
3. **Add `map_edit_err`** in `src/errors.rs` (variant-matching: `RewriteRootCommit` →
   `ImmutableCommitError`, else `BackendError`; import `jj_lib::repo::EditCommitError`).
4. **Faithful primitives:** enforce only the **root** (the hard backend rule). Do **not** replicate
   jj's `immutable_heads()` config policy in Rust — that's CLI workflow policy.
5. **Facade + stubs:** add `edit`/`abandon` to `python/pyjutsu/transaction.py` (no `_complete_newline`
   — no message arg) and `python/pyjutsu/_pyjutsu.pyi`. No new Pydantic model (`edit` returns
   `Commit`).
6. **Differential tests** (`tests/test_edit_abandon.py`) vs `jj edit`/`jj abandon`: `@` moves + tree
   checked out on disk (reuse `linear_repo` file-presence checks); abandon-leaf advances `@` to a
   new empty commit; abandon-middle rebases children (change ids stable, survivors' commit ids
   deterministic); **root edit/abandon both raise `ImmutableCommitError`** (proves no panic); 1 op on
   a clean `@`; 0 ops on rollback. `JjCli.__call__` already drives both verbs.

Run `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` green, then **stop and report at the
slice boundary** before slice 4 (bookmark writes). Commit on `main` (`Implement M2 slice 3: edit /
abandon`).

## Non-negotiable constraints (carried)

- jj-lib via PyO3, in-process; pin `=0.38.0`; `Cargo.lock` committed; the pinned `jj` 0.38.0 CLI is
  **only** the differential oracle. Everything through devenv — never bare `cargo`/`maturin`/
  `python`/`pytest`/`jj`.
- Thin Rust, rich Python: `_pyjutsu` returns opaque handles + **plain data only** (dicts/lists/
  strings/`None`); never leak jj-lib types; models/ergonomics/policy live in pure-Python `pyjutsu`.
- `Transaction`/`MutableRepo` are `!Send`: in-tx work + `tx.commit` on the GIL; only the
  snapshot/checkout/git I/O on the `Send` `Workspace` releases the GIL (checkout already wired).
- `rebase_descendants()` before commit is centralized in `PyTransaction::commit`; rewriting mutations
  also call it inline so the returned model is faithful. Forgetting it **aborts the process**.
- Every fallible path maps a jj-lib error to a `PyjutsuError` subclass; only its `Display` crosses
  the FFI. No workflow policy (no lanes/frozen trunk/immutable-heads). **No AI attribution** anywhere.

**Start by reading `M2_SLICE3_GUIDE.md` and skimming the slice 2 code, then implement `tx.edit` +
`tx.abandon` (with the root-abandon panic guard and `map_edit_err`), with their differential tests
green, and stop for review before slice 4.**
