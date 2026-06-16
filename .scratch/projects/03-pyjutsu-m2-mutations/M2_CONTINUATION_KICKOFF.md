# Pyjutsu M2 — continuation kickoff prompt (slices 2–11)

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_CONTINUATION_GUIDE.md` (**your spine — the corrected plan for the remaining slices**) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_IMPLEMENTATION_GUIDE.md` (original plan; still good for build order, model surface, error taxonomy, and per-slice jj-lib `file:line` refs — **but where it disagrees with the continuation guide, the continuation guide wins**) → this prompt (orientation + cadence). **This is an implementation session: the deliverable is shipped, tested code, built one vertical slice at a time.**

---

You are continuing **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic +
Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no
subprocess backend, no text parsing. **M0 (build spike), M1 (read layer, released `0.39.0`), and
M2 slices 0–1 are done.** Slices 0–1 are **committed and merged to `main`** and **released as
pyjutsu `0.40.0`** (tag `v0.40.0`); the working tree is clean, so start from `main`. The remaining
M2 slices (2–11) continue on their own cadence; the **completed** write layer will bump to
**`0.41.0`** (versioned independently of jj; pin stays `jj-lib = "=0.38.0"`).

**Read `M2_CONTINUATION_GUIDE.md` end to end first** — it has the current code map, the *verified*
corrections to the original guide, the reusable mutation/test templates, and a detailed plan for
slices 2–11. Then skim the slice 0–1 code so you reuse the established patterns:
`src/transaction.rs` (the `PyTransaction` mutation/commit pattern), `src/workspace.rs`
(`load_user_settings`, `begin_transaction`), `python/pyjutsu/transaction.py`,
`tests/test_describe.py` + `tests/conftest.py` + `tests/diff/jj_cli.py` (the differential harness).

## What's already done (slices 0–1 — do not redo)

- **Identity + tx scaffolding.** Real stacked `UserSettings`; `Workspace.transaction()` ctx mgr;
  `PyTransaction` (unsendable) with `commit`/`rollback`; single-open-tx guard; empty-tx / rollback
  op-count invariants green.
- **`describe`.** `tx.describe(commit, message) -> Commit`, differential vs `jj describe` with exact
  **commit-id parity**.

## Non-negotiable constraints (carried from M0/M1 + M2 corrections)

- **jj-lib via PyO3, in-process.** No subprocess/CLI *backend*, no compat shim. Pin `=0.38.0`;
  `Cargo.lock` committed. The pinned `jj` 0.38.0 CLI is used **only** as the differential oracle.
- **Everything in the devenv shell** — `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}`
  (the tasks need `VIRTUAL_ENV`, so run them *inside* `devenv shell`). **Never** bare
  `cargo`/`maturin`/`python`/`pytest`/`jj`.
- **Thin Rust, rich Python.** `_pyjutsu` exposes opaque handles + **plain data only** (dicts/lists/
  strings); **never leak `jj-lib` types to Python.** Models/ergonomics/defaults/public contract live
  in pure-Python `pyjutsu` and validate the plain data (frozen, `extra="forbid"`).
- **`Transaction`/`MutableRepo` are `!Send`** (jj-lib fact — the original guide was wrong). The
  native tx lives in `#[pyclass(unsendable)] PyTransaction`; `PyWorkspace` stays `Send`. In-tx work
  and `tx.commit` run **on the GIL**; release the GIL only around the `Send` snapshot/checkout/git
  I/O. (See continuation guide §1.1.)
- **`rebase_descendants()` before commit** is centralized in `PyTransaction::commit`; rewriting
  mutations also call it inline so the returned `Commit` is faithful. Forgetting it **aborts the
  process** (transaction.rs:136).
- **Differential testing is the correctness net** and needs: identical repos by `copytree`, pinned
  `debug.commit-timestamp`, `JJ_CONFIG` exported in-process (`jj` fixture does this), and jj's
  trailing-newline on descriptions. (Continuation guide §1.3.) Each slice asserts graph + bookmarks
  + op-log effect, plus the "1 tx == 1 op" family of invariants.
- **Faithful, un-opinionated primitives.** No workflow policy (no lanes/frozen trunk). Conflicts
  stay first-class N-sided. Surface divergence and stale `@`; never hide them.
- **Panic safety + error mapping:** every fallible path maps a jj-lib error to a `PyjutsuError`
  subclass; only its `Display` crosses the FFI. Add `map_*` helpers per slice as first used.
- **No AI-generated attribution** anywhere (commits/PRs/docs/comments).

## Start here: Slice 2 — `new` + `@` advance + post-rewrite checkout

Slice 2 is the linchpin: `new` is small, but it forces the **on-disk working-copy checkout** that
**every** `@`-rewriting slice reuses. Implement, per continuation guide §3:

1. `tx.new(parents=None) -> Commit` — `merge_commit_trees` (rewrite.rs:57) → `new_commit` (repo.rs:948)
   `.write()` → `edit(name, &new)` (repo.rs:1526) → `rebase_descendants()` → return the full `Commit`.
2. **Add the `Py<PyWorkspace>` back-reference to `PyTransaction`** (deferred in slices 0–1): switch
   `begin_transaction` to `slf: Bound<'_, Self>`, store `Py<PyWorkspace>`, thread it through
   `PyTransaction::new`. Capture the starting `@` commit id at begin.
3. **In `PyTransaction::commit`, after `tx.commit`,** if `@`'s commit changed, run the §3.2 checkout:
   `Workspace::check_out(new_op_id, Some(&old_tree), &new_commit)` (workspace.rs:437) — **off the
   GIL** (it's on the `Send` `Workspace`). Map `CheckoutError::*` → `WorkingCopyError`.
4. Differential test vs `jj new` (parents + `@` + on-disk checkout; 1 op on clean `@`).
5. **Backfill the slice-1 describe test** to also run `jj` against the Pyjutsu-mutated repo now that
   the WC stays in lockstep.

Run `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` green, then **stop and report at
the slice boundary** before slice 3.

## How to work (cadence)

- Build the vertical slices **in order**: `2` new+checkout → `3` edit/abandon → `4` bookmarks →
  `5` snapshot+auto-snapshot → `6` stale-WC → `7` undo/restore → `8` rebase/squash/restore →
  `9` workspace mgmt → `10` git import/export+remotes → `11` git fetch/push.
- **Per slice:** thin Rust (mutation template, continuation guide §2.1) + Python facade + extend the
  `JjCli` driver with the write verb it lacks + a differential test (recipe §2.3). Green
  build/test/lint, then **stop and report** so the work stays reviewable; the milestone can release
  incrementally.
- **When citing jj-lib API, read the pinned source** at `~/.cargo/registry/.../jj-lib-0.38.0/src/`.
  The refs are verified, but **confirm any signature marked "verify at slice N"** before wiring —
  especially `rebase` (`move_commits` field names, rewrite.rs:525–600, slice 8) and `undo`
  (`merge`-based reverse vs the CLI's view-restore, slice 7).
- **On completion of all slices:** bump to **`0.41.0`** (in `python/pyjutsu/__init__.py`,
  `pyproject.toml`, `Cargo.toml`; rebuild to refresh `Cargo.lock`/`uv.lock`) — `0.40.0` already
  shipped slices 0–1. Update concept §3/§5 status to "M2 implemented", regenerate goldens
  (`WorkspaceInfo`/`Remote`), confirm the `JJ_VERSION == JJ_LIB_TARGET` tripwire is green.

## Guardrails

- Don't add a CLI/subprocess *backend*, a migration shim, or old-Pyjutsu compatibility.
- Don't leak `jj-lib` types across the FFI or put business/workflow logic in Rust.
- Don't bake workflow policy (lanes, frozen trunk) into Pyjutsu — faithful primitives only.
- Don't silently operate on a stale `@`; don't hide operation divergence.
- Don't forget `rebase_descendants()` before commit (process-abort landmine).
- Don't try to move a `Transaction`/`MutableRepo` across `allow_threads` (it's `!Send`).
- Don't run bare host tooling — everything through devenv. No AI attribution.
- Don't tie the pyjutsu version to jj (slices 0–1 shipped `0.40.0`; completed M2 → `0.41.0`).

**Start by reading `M2_CONTINUATION_GUIDE.md` and skimming the slice 0–1 code, then implement
Slice 2 (`tx.new` + the `PyTransaction`→`PyWorkspace` back-reference + the post-commit on-disk
checkout in `PyTransaction::commit`, reused by all `@`-rewriting slices), with its differential
test green and the slice-1 describe test backfilled, and stop for review before Slice 3.**
