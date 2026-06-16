# Pyjutsu M2 — Continuation Guide (slices 2–11)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `M2_IMPLEMENTATION_GUIDE.md` (the
> original approved plan; still the spine for the build order, model surface, error taxonomy, and
> the per-slice jj-lib refs) → **this document** (corrections + the detailed plan for the *remaining*
> slices, grounded in what slices 0–1 actually proved) → the code it produces.
>
> **Read this together with `M2_IMPLEMENTATION_GUIDE.md`.** Where the two disagree, **this file
> wins** — it records facts verified against the pinned source and against a green build/test, and
> the original guide got two things materially wrong (see §1).
>
> **Pin unchanged:** `jj-lib = "=0.38.0"`. Pyjutsu still targets `0.40.0` on completion. API refs
> are `file:line` into `~/.cargo/registry/.../jj-lib-0.38.0/src/`.

---

## 0. Where we are

**Slices 0 and 1 are implemented and green** (build + pytest + `cargo test` + clippy `-D warnings`
+ ruff, all via devenv). They are in the **working tree** on `main` (commit them before/at the
start of the next session, or just continue from the working tree).

- **Slice 0 — identity + tx scaffolding.** Real stacked `UserSettings` (user + repo config, with
  the CLI's `JJ_CONFIG` env policy replicated); `Workspace.transaction()` context manager; the
  native transaction handle; single-open-tx guard; empty-tx / rollback differential invariants.
- **Slice 1 — `describe`.** `tx.describe(commit, message) -> Commit`, differential vs `jj describe`
  with **exact commit-id parity**.

### 0.1 Current code map (what exists now)

```
src/
  lib.rs          registers PyWorkspace, PyRepoView, PyTransaction + the error types
  errors.rs       + WorkingCopyError, StaleWorkingCopyError(⊂WC), ImmutableCommitError (registered;
                  map_* helpers added per-slice as used)
  workspace.rs    PyWorkspace (Send): load_user_settings() (stacked config), begin_transaction()
                  -> PyTransaction, tx_open: Arc<AtomicBool> single-open guard
  transaction.rs  PyTransaction (#[pyclass(unsendable)]): RefCell<Option<Transaction>>, tx_open,
                  workspace_name/root/user_email (revset ctx); resolve_single(); describe();
                  commit() [centralizes rebase_descendants]; rollback(); Drop releases the slot
  repo_view.rs, convert.rs, revset.rs, diff_stat.rs   (M1, unchanged)
python/pyjutsu/
  transaction.py  Transaction token (ctx mgr): __enter__→begin, __exit__→commit/rollback;
                  _complete_newline(); describe(); _require_open()
  workspace.py    Workspace.transaction(description, *, auto_snapshot=True)
  errors.py, __init__.py   re-export the 3 new error types
  _pyjutsu.pyi    stubs for PyTransaction + begin_transaction
tests/
  conftest.py     `jj` fixture now monkeypatches JJ_CONFIG into the process env (see §2.3)
  diff/jj_cli.py  _CONFIG_TOML pins debug.commit-timestamp; + op_head_description()
  test_transaction.py (slice 0), test_describe.py (slice 1)
```

---

## 1. Corrections to the original guide (these override it — internalize first)

### 1.1 ⛔ `jj_lib::Transaction` / `MutableRepo` are **`!Send`** (original guide §0.1/§2.2 are wrong)

`MutableRepo` holds `Box<dyn MutableIndex>`, and **`MutableIndex: Any`** has no `Send` bound
(`index.rs:175`; contrast `ReadonlyIndex: Any + Send + Sync`). So `MutableRepo` and `Transaction`
are **not `Send`**. The original guide's claim that "Transaction is Send" and can live in the
`Send` `PyWorkspace` and cross `Python::allow_threads` is **false** and does not compile.

**The implemented design instead:**
- The native transaction lives in a **separate `#[pyclass(unsendable)] PyTransaction`**
  (`src/transaction.rs`), holding `RefCell<Option<Transaction>>`.
- **`PyWorkspace` stays `Send`** (preserves concept §8.4 + M1's off-GIL reads). It holds only an
  `Arc<AtomicBool> tx_open`, shared with the live `PyTransaction`, to enforce one-open-tx.
  `begin_transaction(&self)` claims the slot, reloads at head, returns a `PyTransaction`;
  `commit`/`rollback`/`Drop` release the slot.

**Consequence — GIL discipline (revised):** the in-transaction graph work **and** `Transaction::
commit` necessarily run **on the GIL** (a `!Send` value can't enter `allow_threads`). This is
unavoidable and *not* a defect. Release the GIL only around the genuinely heavy I/O that is
expressible on **`Send`** types separate from the transaction:
- **snapshot** via `LockedWorkingCopy` (slice 5),
- **checkout** via `Workspace::check_out` (slices 2+, see §3),
- **git fetch/push/import/export** via the `Send` `Workspace`/git calls (slices 10–11).

The op-store write inside `tx.commit` is light; holding the GIL there is fine.

### 1.2 `rebase_descendants()` is centralized in `PyTransaction::commit`

Per landmine #1 (`Transaction::commit` asserts `!has_rewrites()`, transaction.rs:136 — a violation
**aborts the process**), `PyTransaction::commit` calls `tx.repo_mut().rebase_descendants()` once
**before** `tx.commit(...)`. It is a harmless no-op when nothing was rewritten.

**Additionally**, the implemented `describe` calls `rebase_descendants()` *inside* the mutation,
right after `write()`, so the **returned `Commit` reflects moved bookmarks / rebased descendants**
(decision 2 wants a faithful full model back). This is belt-and-suspenders with the commit-time
call (both are idempotent). **Follow this pattern for every rewriting mutation** (abandon, rebase,
squash, restore): rewrite → `rebase_descendants()` → read back → return; commit re-runs it safely.

### 1.3 Differential commit-id parity requires three things (all wired in slice 1)

See `tests/test_describe.py` and `tests/conftest.py` / `tests/diff/jj_cli.py`:
1. **Identical starting repos by `shutil.copytree`** — change ids are random and cannot be
   reproduced by replay; seeding `debug.randomness-seed` *collides* across the separate `jj`
   processes the harness spawns (divergent change ids). Copy the repo dir instead.
2. **Pinned `debug.commit-timestamp`** in `_CONFIG_TOML` (already set) so the committer timestamp
   is fixed, not "now". **Do not** pin `randomness-seed`.
3. **`JJ_CONFIG` exported in the pytest process**, not just the CLI subprocess — the binding reads
   `JJ_CONFIG` from its own process env in `load_user_settings`. The `jj` fixture now
   `monkeypatch.setenv`s it. Without this, the binding authors with different settings than the CLI.

Plus: **commit descriptions get jj's trailing-newline** (`transaction.py::_complete_newline`), or
commit ids won't match. Apply it to every message-taking facade method (`new`, `squash`, `restore`).

### 1.4 The differential pattern for any `@`-rewriting mutation

After a Pyjutsu mutation that rewrites `@`, **the on-disk working copy must be updated** (§3), or a
later `jj` command on the same repo will react to the stale working copy. Slice 1's test sidesteps
this by reading the Pyjutsu-mutated repo **only via Pyjutsu** (pure reads). Once slice 2 lands the
post-commit checkout, mutation tests may freely run `jj` against the Pyjutsu-mutated repo.

---

## 2. Carry-forward conventions (reuse, don't reinvent)

### 2.1 Adding a mutation method (the slice-1 template)

In `src/transaction.rs`, inside `#[pymethods] impl PyTransaction`:

```rust
fn <verb><'py>(&self, py: Python<'py>, /* args */) -> PyResult<Bound<'py, PyDict>> {
    let mut guard = self.tx.borrow_mut();
    let tx = guard.as_mut().ok_or_else(|| PyjutsuError::new_err("transaction is already closed"))?;
    let repo = tx.repo_mut();                                  // &mut MutableRepo (on the GIL)
    let target = self.resolve_single(&*repo, revset_str)?;     // immutable reborrow ends here
    // ... rewrite_commit/new_commit/record_abandoned_commit/... ...
    repo.rebase_descendants().map_err(map_backend_err)?;       // keep return value faithful
    let data = CommitData::build(&*repo, &new_commit)?;        // decision 2: full Commit back
    data.to_dict(py)
}
```

- Use `self.resolve_single(&*repo, revset)` for single-revision args; for list args (`new` parents,
  `rebase --onto`), evaluate each revset and collect (write a `resolve_all` sibling if needed).
- Returns **plain dicts** only; the Python facade validates into Pydantic. Never leak jj-lib types.
- For methods that return nothing (`abandon`, `delete_bookmark`), return `()` / `None`.

In `python/pyjutsu/transaction.py`: add the facade method, guard with `self._require_open()`,
normalize messages with `_complete_newline`, and `Model.model_validate(...)` the returned dict.
Update `_pyjutsu.pyi` and (if a new model) `models.py` + the golden.

### 2.2 Error mapping (add `map_*` helpers as each slice first needs them)

Mirror M1's `map_*` style in `errors.rs`; map and **do not** leak the jj-lib error (only its
`Display`). The taxonomy classes already exist; wire the mappings:
- `RewriteRootCommit` (repo.rs:2028) and `EditCommitError::RewriteRootCommit` → `ImmutableCommitError`
- `CheckoutError::{ConcurrentCheckout, Other}` (working_copy.rs:278) → `WorkingCopyError`
- `WorkingCopyFreshness::{WorkingCopyStale, SiblingOperation}` → `StaleWorkingCopyError`
- `TransactionCommitError` (transaction.rs:45) → `BackendError`
- git error enums → `BackendError` (or a new `GitError ⊂ BackendError`, decide at slice 10)

### 2.3 Testing recipe (per slice)

1. Build a fixture repo (reuse `scratch_repo` / `linear_repo` / `bookmarked_repo`); `copytree` it.
2. Apply the mutation via Pyjutsu on one copy, via `JjCli` on the other; extend `JjCli` with the
   write verb it lacks (it currently drives `describe`, `new`, `bookmark`, `git`, …).
3. Assert: **change graph** (`jj.change_ids("::@")` + parents), **bookmarks** (`JjCli.bookmarks`),
   **op-log effect** (`op_log_ids` count delta; `op_head_description`/tags/`is_snapshot` *only* where
   meaningful — do **not** compare the op description to the CLI's, it's user-chosen here).
4. Assert the **"1 tx == 1 op"** invariant (clean `@`); after slice 5, **"+1 snapshot op"** on a
   dirty `@`; and **"0 ops on rollback"**.
5. Where commit ids are compared, rely on the copytree + pinned-timestamp determinism.

---

## 3. Slice 2 — `new` + `@` advance + **post-rewrite checkout** (do this first; it's the linchpin)

`new` is small; the **checkout** it forces is the reusable piece every `@`-rewriting slice needs.

### 3.1 The mutation: `tx.new(parents=None) -> Commit`

Default parent is `@`. Verified APIs:
- `merge_commit_trees(repo, &parent_commits) -> BackendResult<MergedTree>` (async, rewrite.rs:57) —
  for one parent it's just `parent.tree()`; for many it merges. Drive with `.block_on()` (on GIL).
- `MutableRepo::new_commit(parent_ids, tree) -> CommitBuilder<'_>` (repo.rs:948) → `.write()`.
- `MutableRepo::edit(name, &new_commit) -> Result<(), EditCommitError>` (repo.rs:1526) — points `@`
  at the new commit and **abandons the old `@` if discardable** (this can register a rewrite, hence
  the centralized `rebase_descendants()` in commit covers it).
- Convenience: `MutableRepo::check_out(name, &commit)` (repo.rs:1514) is exactly
  `new_commit(vec![commit], commit.tree()).write()` + `edit(name, …)` — use it for the common
  single-parent `new`; hand-roll `new_commit` + `edit` for multi-parent.

Sequence inside the method: resolve parents → build tree → `new_commit(ids, tree).write()` →
`edit(name, &new)` → `rebase_descendants()` → build `CommitData` from `&*repo` → return dict.
(`new` itself records no rewrite of the new commit, but `edit`'s abandon-old-`@` might; the
`rebase_descendants()` handles it.)

### 3.2 The checkout: update the on-disk working copy after commit (§3.6 of the original guide)

**`PyTransaction` needs a back-reference to its `PyWorkspace`** (deferred in slices 0–1, add it now):
change `begin_transaction` to `slf: Bound<'_, Self>` and store `Py<PyWorkspace>` on the
`PyTransaction`; thread it through `PyTransaction::new`. (`Py<PyWorkspace>` is `Send`; the pyclass is
already `unsendable`, so this is free. No reference cycle: `PyWorkspace` only holds an `AtomicBool`.)

In `PyTransaction::commit`, **after** `tx.commit(description) -> new_repo`, if `@`'s working-copy
commit changed, update the disk:

```text
1. capture, at begin_transaction, the starting wc commit id (from the base repo's view) → store on
   PyTransaction. (Or compare to the workspace's recorded WC commit.)
2. new_wc_id = new_repo.view().get_wc_commit_id(&name)            // Option (could be None)
3. if new_wc_id != starting id:
4.   new_commit = new_repo.store().get_commit(new_wc_id)          // off-GIL ok (Arc repo is Send)
5.   lock PyWorkspace.inner (Mutex<Workspace>, Send); old_tree = ws.working_copy().tree()?.clone()
6.   py.allow_threads(|| ws.check_out(new_repo.op_id().clone(), Some(&old_tree), &new_commit))?
        // Workspace::check_out (workspace.rs:437): locks WC, guards ConcurrentCheckout, checks out,
        // finish()es at the new op id — keeps the WC's recorded op in lockstep with the repo head.
```

- `Workspace::check_out` operates on the **`Send`** `Workspace`, so steps 4/6 run **off the GIL**.
- Map `CheckoutError::{ConcurrentCheckout, Other}` → `WorkingCopyError`.
- For a `new` whose new `@` has the same tree as the old (e.g. `new` on top of current `@`), the
  checkout writes no files but still `finish`es at the new op — correct and matches the CLI.

> This block lives in `commit` so **every** `@`-rewriting slice (describe, edit, abandon, rebase,
> squash, undo/restore) gets the on-disk update for free. After it lands, **backfill the slice-1
> describe test** to also run `jj` against the Pyjutsu-mutated repo (no spurious snapshot op).

### 3.3 Differential assertion (slice 2)

New commit's parents + `@` pointer match `jj new` (and `jj new A B` for a merge); the on-disk `@` is
checked out (assert a sentinel file from a sibling tree appears/disappears for `new <other>`);
exactly 1 op on a clean `@`. Change graph + bookmarks unchanged elsewhere.

---

## 4. Slices 3–11 — concise plans (original guide §3/§4 refs still hold, with §1 corrections)

Each slice: thin Rust mutation (template §2.1) + Python facade + extend `JjCli` + a differential
test (recipe §2.3). Ship at each boundary. **All `@`-rewriting slices reuse the §3.2 checkout.**

### Slice 3 — `edit` / `abandon`
- `edit`: `MutableRepo::edit(name, &commit)` (repo.rs:1526) → `@` points at an existing commit
  (no new commit). `rebase_descendants()` (old `@` may be abandoned if discardable). Checkout §3.2.
- `abandon`: `record_abandoned_commit(&old)` (repo.rs:1005) → `rebase_descendants()` rebases
  children onto the abandoned commit's parents. `record_abandoned_commit_with_parents` (repo.rs:1019)
  for explicit re-parenting.
- **Map** `RewriteRootCommit` / `EditCommitError::RewriteRootCommit` → `ImmutableCommitError`
  (editing/abandoning the root). Add `map_immutable_err` + `map_workingcopy_err` here.
- Diff: graph after abandon == `jj abandon`; `@` after edit == `jj edit`; root edit → `ImmutableCommitError`.

### Slice 4 — bookmark writes (no `@` rewrite → no checkout)
- `set_local_bookmark_target(name: &RefName, RefTarget)` (repo.rs:1676); `RefTarget::normal(id)` to
  set/move, `RefTarget::absent()` to delete. `create` = error-if-exists guard in the facade.
- Remote track/untrack: `track_remote_bookmark` / `untrack_remote_bookmark` (repo.rs ~1700+).
- `RefName`/`RemoteRefSymbol` in `ref_name.rs`. Return the `Bookmark` model (reuse M1 `BookmarkData`).
- Diff: reuse `JjCli.bookmarks`; 1 op each; conflicted bookmark stays N-target (faithful).

### Slice 5 — `snapshot` + **auto-snapshot on tx open**
- Exact sequence in original guide §3.5 (verified there): `start_working_copy_mutation` (workspace.rs:427)
  → `WorkingCopyFreshness::check_stale` (working_copy.rs:363) → `snapshot(&SnapshotOptions)`
  (working_copy.rs:118, async) → if tree unchanged, **drop lock, no op** → else start a tx,
  `rewrite_commit(@).set_tree(new).write()` → `rebase_descendants()` → `set_is_snapshot(true)` →
  `commit("snapshot working copy")` → `locked_ws.finish(new_op)`.
- All WC I/O is on the **`Send`** `LockedWorkingCopy`/`Workspace` → **off the GIL**.
- Wire `auto_snapshot` (already threaded through the Python `Transaction`): on `__enter__`, if
  `auto_snapshot` and `@` is dirty, run `ws.snapshot()` **before** `begin_transaction` — it is a
  **separate preceding operation** (concept §0.1). Confirm `SnapshotOptions` defaults against the
  CLI (`max_new_file_size`, base ignores).
- Diff (the headline invariant): dirty `@` + mutation ⇒ **2 ops** (`snapshot working copy` then the
  op); clean ⇒ **1**; clean-WC `ws.snapshot()` ⇒ **None / 0 ops**; snapshot tree id == CLI's.

### Slice 6 — stale working copy
- `WorkingCopyFreshness::check_stale` (working_copy.rs:363) → `Fresh / Updated(op) / WorkingCopyStale
  / SiblingOperation`. `Workspace.is_stale()`; mutating/snapshotting a stale `@` raises
  `StaleWorkingCopyError` (map `WorkingCopyStale`/`SiblingOperation`). `update_stale()` = checkout `@`
  to its recorded commit (matches `jj workspace update-stale`).
- Test: two `Workspace` handles on one repo; rewrite base from one ⇒ other `is_stale()` true,
  refuses to mutate, then `update_stale()` reconciles.

### Slice 7 — `undo` / `restore_operation`
- `undo`: start tx at head; load the to-undo op's repo and its parent's repo; `tx.repo_mut().merge(
  base = op_repo, other = parent_repo)` (repo.rs:1831) applies the reverse; commit `"undo operation
  <id>"`. **Building block is `merge`; the contract is the differential test vs `jj undo`** — assert
  identical resulting view. If partial-restore drifts, replicate the CLI's
  `view_with_desired_portions_restored` (flagged, not pre-solved).
- `restore_operation`: `tx.repo_mut().set_view(target_view)` (repo.rs:1827) from the target op; commit.
- After either, run §3.2 checkout if `@` changed. **Consider pinning `debug.operation-timestamp`** in
  `_CONFIG_TOML` if op-id equality is asserted here (verify it doesn't collide; it was left unpinned
  in slices 0–1).

### Slice 8 — `rebase` / `squash` / `restore`
- `rebase`: **verify field names at this slice** against rewrite.rs:525–600 — `move_commits` /
  `compute_move_commits` (585/593), `MoveCommitsTarget::Commits([...])`, `MoveCommitsLocation`
  (525/532) — then `rebase_descendants()`.
- `squash`: `squash_commits(repo, &[CommitWithSelection], destination, keep_emptied)` (rewrite.rs:1268)
  → `SquashedCommit { commit_builder, abandoned_commits }` (1258): set description, `write()`,
  `rebase_descendants()`. Fully-selected sources are abandoned.
- `restore`: `restore_tree(from_tree, to_tree, …, matcher)` (rewrite.rs:119) →
  `rewrite_commit(target).set_tree(new).write()` + `rebase_descendants()`. `EverythingMatcher` for
  whole-commit; `FilesMatcher` (matchers.rs) for path-scoped.
- Diff: graph + trees == `jj rebase`/`jj squash`/`jj restore`; conflicts stay first-class N-sided.

### Slice 9 — workspace management
- `Workspace::init_internal_git` / `init_colocated_git` / `init_external_git` (workspace.rs:205/221/253);
  `Workspace.init(path, *, colocate=False)` classmethod.
- `add_workspace` (**eager**, concept §11): `init_workspace_with_existing_repo` (workspace.rs:358) +
  allocate the new `WorkspaceId` and set its `@` in the shared view (`set_wc_commit`/`check_out`,
  repo.rs:1458/1514) **then** check out files at the new path. Error if the target path is non-empty.
- `forget_workspace`: `remove_wc_commit` (repo.rs:1470). `rename_workspace` (repo.rs:1506).
- New model **`WorkspaceInfo`** `{name, path, wc_commit_id}` from the view's `wc_commit_ids`
  (regen golden). Diff: new workspace `@` + record == `jj workspace add`; forget == `jj workspace forget`.

### Slice 10 — git import/export + remotes
- `import_refs(mut_repo, options)` (git.rs:529), `import_head` (983), `export_refs(mut_repo)` (1103).
- Remotes CRUD: `get_all_remote_names` / `add_remote` / `remove_remote` / `rename_remote` /
  `set_remote_urls` (git.rs:2098/2116/2173/2230/2332). New model **`Remote`** `{name, url}`.
- **Decide here:** keep git errors → `BackendError`, or introduce `GitError ⊂ BackendError`.
- Diff: refs after import/export == `jj git import`/`export`; remotes == `jj git remote list`.

### Slice 11 — git fetch / push (network; subprocess via `gix`)
- `GitFetch::new(mut_repo, GitSubprocessOptions, &GitImportOptions)` → `.fetch(remote, refspecs,
  callback, depth, tags)` → `.import_refs()` (git.rs:2756/2779/2883).
- `push_branches` / `push_updates(mut_repo, …, &[GitRefUpdate])` → `GitPushStats` (git.rs:2945/3011/170).
- Subprocess + network: **release the GIL for all of it**; gate tests behind the local bare-repo
  fixture (extend `bookmarked_repo`); no real remotes. Land last; keep error mapping + GIL isolated.

---

## 5. Definition of done (unchanged from original §8, with §1 corrections)

- Slices 0–11 green (graph + bookmarks + op-log differential per slice).
- Invariants: "1 tx == 1 op" (clean), "+1 snapshot op" (dirty), "0 ops on rollback".
- New error subclasses raised on their failure modes, with tests.
- `Cargo.lock` committed; pin `=0.38.0`; `JJ_VERSION == JJ_LIB_TARGET` tripwire green.
- Concept §3/§5 status → "M2 implemented"; `__version__ = "0.40.0"`; goldens regenerated for
  `WorkspaceInfo`/`Remote`.
- No subprocess/CLI **backend**, no compat shim, no workflow policy, **no AI attribution**.

## 6. Cadence

Build slices **in order, one at a time**. Per slice: thin Rust + Python facade + `JjCli` verb +
differential test; `devenv tasks run pyjutsu:{build,test,lint}` green; **stop and report at the
slice boundary** so the work stays reviewable (the milestone can release incrementally). Verify any
signature the plan marked "verify at slice N" against the pinned source before wiring it.
