# Pyjutsu M2 — Slice 7 Implementation Guide (operation log writes: `undo` / `restore_operation`)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `M2_CONTINUATION_GUIDE.md` (the
> corrected spine for slices 2–11; §1 corrections + §2 conventions + §4 slice plans still hold;
> slice 7 plan at §4) → **this document** (the detailed, verified plan for slice 7 specifically) →
> `M2_IMPLEMENTATION_GUIDE.md` (original plan; error taxonomy + API table — but the continuation
> guide and this doc win where they disagree) → the code it produces.
>
> **Pin unchanged:** `jj-lib = "=0.38.0"`. Slices 0–6 are committed and pushed on `main`
> (slice 6 = `cfbb458`); the working tree is clean — start from `main`. Slices 0–1 shipped as pyjutsu
> `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2 bumps to `0.41.0`. API refs below
> are `file:line` into `~/.cargo/registry/.../jj-lib-0.38.0/src/`, **verified against the pinned
> source** while writing this guide.

---

## 0. Where slice 7 starts

Slices 0–6 built the mutation surface (`describe`/`new`/`edit`/`abandon`/bookmark verbs in a
`Transaction`), `snapshot` + auto-snapshot, and the stale-WC surface (`is_stale`/`update_stale`). The
reusable pieces this slice leans on already exist:

- **`PyWorkspace::snapshot`** (`src/workspace.rs`) — the load-at-head → resolve `@` skeleton, and the
  pattern of running an *internal* transaction (`repo.start_transaction()` … `tx.commit(desc)`)
  inside a `PyWorkspace` method rather than the user-facing `PyTransaction`.
- **`PyWorkspace::checkout_wc`** (`src/workspace.rs:126`) — the post-op on-disk checkout
  (`ws.check_out(op_id, Some(&old_tree), &commit)` off the GIL). **It calls `self.locked()`
  internally**, so it cannot be called while a `MutexGuard` is already held (it would deadlock); see
  §2.1 for the refactor that fixes this.
- **`PyWorkspace::at_operation`** (`src/workspace.rs:202`) — already resolves an op spec via
  `op_walk::resolve_op_for_load(loader, op_str)` and maps a bad spec with `to_py_err`. `undo`/
  `restore_operation` resolve operations the **same way**.
- **`OperationData`** (`src/convert.rs:112`) — the plain op-log row both new methods return.

This slice adds the two **operation-log write** verbs the concept lists under "Operations" (§ surface
v1, `docs/PYJUTSU_CONCEPT.md:133`):

1. **`Workspace.undo(operation=None) -> Operation`** — revert one operation (default: the head op),
   publishing a new operation that applies its reverse. Matches `jj undo` / `jj op undo`.
2. **`Workspace.restore_operation(operation) -> Operation`** — reset the repo state to the view a
   past operation recorded, publishing a new operation. Matches `jj op restore <op>`.

Both are **`Workspace`-level**, not transaction methods (the concept calls them as `ws.undo()` /
`ws.restore_operation(op_id)`, `docs/PYJUTSU_CONCEPT.md:112–114`): each manages its own internal
`Transaction`, exactly like `snapshot`. Each publishes **exactly one** new operation and, if `@`
moved, checks out the working copy to the new `@`.

> **What `undo` does (the `merge` primitive).** To undo operation **X** we want to apply *the reverse
> of X* on top of the current head. jj models this as a 3-way merge of repo views:
> `MutableRepo::merge(base, other)` computes `head + (other − base)`. With **`base = X`'s repo** and
> **`other = X`'s parent's repo**, the applied delta is `(parent − X)` = the reverse of X. (This is
> exactly the continuation guide's "`base = op_repo`, `other = parent_repo`".)
>
> **What `restore_operation` does (the `set_view` primitive).** `MutableRepo::set_view(view)` replaces
> the working view wholesale. With the **target operation's stored view**, committing the transaction
> records "the repo now looks exactly as it did at that op" as a *new* operation on top of head — the
> all-portions form of `jj op restore`.

---

## 1. Verified jj-lib APIs (against the pinned 0.38.0 source)

| What | Signature / fact | Ref |
|---|---|---|
| Resolve an op spec (`@`, `@-`, id, prefix) | `op_walk::resolve_op_for_load(&RepoLoader, &str) -> Result<Operation, OpsetEvaluationError>` | op_walk.rs:89 |
| Op's parents | `Operation::parents(&self) -> impl ExactSizeIterator<Item = OpStoreResult<Operation>>` | operation.rs:109 |
| Op id (hex) | `Operation::id(&self) -> &OperationId`; `.hex()` via `ObjectId` | operation.rs:97 |
| Op's stored view | `Operation::view(&self) -> OpStoreResult<view::View>` → **high-level** `View` (not `op_store::View`) | operation.rs:117 |
| High-level → store view | `View::store_view(&self) -> &op_store::View` (clone it for `set_view`) | view.rs:560 |
| Loader (owned) | `Workspace::repo_loader(&self) -> &RepoLoader`; **`RepoLoader: Clone`** (clone to drop the WS lock) | workspace.rs:414, repo.rs:663 |
| Load a repo at an op | `RepoLoader::load_at(&self, &Operation) -> Result<Arc<ReadonlyRepo>, RepoLoaderError>` | repo.rs:767 |
| Load at head | `RepoLoader::load_at_head(&self) -> Result<Arc<ReadonlyRepo>, RepoLoaderError>` | repo.rs:756 |
| Start internal tx | `ReadonlyRepo::start_transaction(self: &Arc<Self>) -> Transaction` | repo.rs:326 |
| Apply reverse (undo) | `MutableRepo::merge(&mut self, base_repo: &ReadonlyRepo, other_repo: &ReadonlyRepo) -> Result<(), RepoLoaderError>` | repo.rs:1831 |
| Reset view (restore) | `MutableRepo::set_view(&mut self, data: op_store::View)` | repo.rs:1826 |
| `@` commit id at a view | `repo.view().get_wc_commit_id(&WorkspaceName) -> Option<&CommitId>` | (slices 5/6) |
| Forced/normal checkout | `Workspace::check_out(&mut self, OperationId, Option<&MergedTree>, &Commit) -> Result<CheckoutStats, CheckoutError>` | workspace.rs:437 |

Notes verified in source:

- **`merge` argument order** is `(base_repo, other_repo)`; with `base = bad`, `other = parent` the
  net applied delta is the reverse of the bad op (repo.rs:1831–1856 — it merges both indexes, then
  `merge_view(&base.view, &other.view)`). `load_at` returns `Arc<ReadonlyRepo>`; pass `&bad_repo`
  (deref-coerces to `&ReadonlyRepo`).
- **`Operation::view()` returns the high-level `crate::view::View`**, *not* the `op_store::View` that
  `set_view` wants. Bridge with `target_op.view().map_err(map_backend_err)?.store_view().clone()`
  (view.rs:560). **This is the slice-5-lesson trap for this slice — grep before trusting the name.**
- **`view_with_desired_portions_restored` is NOT in jj-lib 0.38** — it lives in jj-**cli**. The pinned
  `jj` CLI applies it (so `jj undo` / `jj op restore` can restore only *some* portions, e.g. leave
  remote-tracking bookmarks alone). For the **all-portions** default and **local-only** histories
  (no git-remote ops between the operations involved) the plain `merge` / `set_view` primitives
  produce a byte-identical resulting view. **Slice-7 tests stay local-only** so this holds; the
  partial-restore portions logic is a **documented refinement, out of scope** (see §4 + §6). If a
  differential test ever drifts on remote-tracking refs, port `view_with_desired_portions_restored`
  from jj-cli — do **not** silently hardcode a subset.
- **`undo` of a 0-parent op** (repo initialization) or a **>1-parent op** (a merge operation) is a
  user error: jj refuses both ("Cannot undo …"). Mirror that with `PyjutsuError` (base) — see §2.2.
- **No `rebase_descendants()` needed.** `merge`/`set_view` register no commit *rewrites*, so
  `Transaction::commit`'s `!has_rewrites()` assert (landmine #1) holds without it. (Contrast the
  `PyTransaction` mutations, which do rewrite and so must rebase.)
- **Operation ids are not deterministic across processes.** Op metadata carries a wall-clock time
  (transaction.rs:73/149/176 read `UserSettings::operation_timestamp()`, defaulting to
  `Timestamp::now()`), plus hostname/username — so the binding's new op id will **not** equal the
  CLI's. **Assert resulting repo *state* (the `@`/commit-graph/bookmark view), not op ids**, across
  the two repos (§4). Pinning `debug.operation-timestamp` in `_CONFIG_TOML` is possible (settings.rs:140)
  but still wouldn't equalize the op description wording, so it buys nothing here — leave it unpinned
  (as slices 0–6 did) and compare state.

---

## 2. Rust: two `#[pymethods]` on `PyWorkspace` (`src/workspace.rs`)

### 2.1 First, refactor the checkout so it can run under a held lock

`undo`/`restore_operation` hold the workspace `Mutex` (via `self.locked()`) for the whole sequence —
load → internal tx → commit → checkout — for atomicity (no concurrent tx slips in). But the existing
`checkout_wc(&self, …)` re-acquires that same `Mutex`, which would **deadlock**. Split the body out
into an associated fn that takes `&mut Workspace` directly, and have `checkout_wc` lock then delegate:

```rust
impl PyWorkspace {
    // (existing `locked` unchanged)

    /// Check out `new_commit` into the already-locked `ws`, recording it at `op_id`. The file I/O
    /// runs off the GIL. Shared by `checkout_wc` (which locks first) and the op-log writes
    /// (`undo`/`restore_operation`, which already hold the lock — calling `checkout_wc` there would
    /// re-lock the workspace `Mutex` and deadlock).
    fn checkout_locked(
        py: Python<'_>,
        ws: &mut Workspace,
        op_id: OperationId,
        new_commit: &Commit,
    ) -> PyResult<()> {
        let old_tree = ws
            .working_copy()
            .tree()
            .map_err(map_workingcopy_err)?
            .clone();
        py.allow_threads(move || ws.check_out(op_id, Some(&old_tree), new_commit))
            .map_err(map_workingcopy_err)?;
        Ok(())
    }

    pub(crate) fn checkout_wc(
        &self,
        py: Python<'_>,
        op_id: OperationId,
        new_commit: &Commit,
    ) -> PyResult<()> {
        let mut guard = self.locked()?;
        Self::checkout_locked(py, &mut guard, op_id, new_commit)
    }
}
```

This is a pure extraction — `PyTransaction::commit`'s call site (`…borrow().checkout_wc(py, …)`) is
unchanged and stays green.

### 2.2 `undo` and `restore_operation`

Add imports: `crate::convert::OperationData` is already imported; add `crate::errors::to_py_err`
(extend the existing `use crate::errors::{…}` group) and `jj_lib::op_walk` is already imported.
`Repo`, `ObjectId`, `OperationId`, `PyjutsuError`, `map_backend_err`, `map_workingcopy_err` are all
already in scope. The shared tail (compare `@` before/after, checkout if it moved, build the op dict)
is factored into a small closure/helper so both methods read cleanly:

```rust
/// Revert one operation, publishing a new operation that applies its reverse — matches `jj undo`.
/// `operation` is an op spec (id, prefix, or expression like `@`/`@-`); `None` undoes the head op.
/// Reverting the repo-initialization op (no parent) or a merge op (>1 parent) is a user error.
/// If the reverse moves `@`, the on-disk working copy is checked out to the new `@` (off the GIL).
#[pyo3(signature = (operation=None))]
fn undo<'py>(
    &self,
    py: Python<'py>,
    operation: Option<&str>,
) -> PyResult<Bound<'py, PyDict>> {
    let mut guard = self.locked()?;
    let ws: &mut Workspace = &mut guard;
    let name = ws.workspace_name().to_owned();
    let op_spec = operation.unwrap_or("@").to_owned();

    // Load head + the to-undo op's repo and its single parent's repo (all backend I/O → off GIL).
    let (repo, bad_repo, parent_repo, bad_op_hex) = {
        let loader = ws.repo_loader();
        py.allow_threads(|| -> PyResult<_> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            // A bad/ambiguous op spec is user input → PyjutsuError base (matches `at_operation`).
            let bad_op = op_walk::resolve_op_for_load(loader, &op_spec).map_err(to_py_err)?;
            let mut parents = bad_op.parents();
            let Some(parent) = parents.next() else {
                return Err(PyjutsuError::new_err(
                    "cannot undo the repo-initialization operation (it has no parent)",
                ));
            };
            let parent_op = parent.map_err(map_backend_err)?;
            if parents.next().is_some() {
                return Err(PyjutsuError::new_err("cannot undo a merge operation"));
            }
            let bad_repo = loader.load_at(&bad_op).map_err(map_backend_err)?;
            let parent_repo = loader.load_at(&parent_op).map_err(map_backend_err)?;
            Ok((repo, bad_repo, parent_repo, bad_op.id().hex()))
        })?
    };

    // Build the reverse op on the GIL (Transaction is !Send). merge(base = bad, other = parent)
    // applies (parent − bad) onto head = the reverse of the bad op. No rewrites ⇒ no rebase needed.
    let mut tx = repo.start_transaction();
    tx.repo_mut()
        .merge(&bad_repo, &parent_repo)
        .map_err(map_backend_err)?;
    let new_repo = tx
        .commit(format!("undo operation {bad_op_hex}"))
        .map_err(map_backend_err)?;

    self.finish_op(py, ws, &name, &repo, &new_repo)
}

/// Reset the repo to the view a past operation recorded, publishing a new operation — matches
/// `jj op restore <op>` (all portions). `operation` is an op spec (id, prefix, or `@`/`@-`).
/// If the restored view moves `@`, the on-disk working copy is checked out to it (off the GIL).
fn restore_operation<'py>(
    &self,
    py: Python<'py>,
    operation: &str,
) -> PyResult<Bound<'py, PyDict>> {
    let mut guard = self.locked()?;
    let ws: &mut Workspace = &mut guard;
    let name = ws.workspace_name().to_owned();
    let op_spec = operation.to_owned();

    let (repo, target_view) = {
        let loader = ws.repo_loader();
        py.allow_threads(|| -> PyResult<_> {
            let repo = loader.load_at_head().map_err(map_backend_err)?;
            let target_op = op_walk::resolve_op_for_load(loader, &op_spec).map_err(to_py_err)?;
            // Operation::view() is the high-level View; set_view wants op_store::View.
            let view = target_op.view().map_err(map_backend_err)?.store_view().clone();
            Ok((repo, view))
        })?
    };

    let mut tx = repo.start_transaction();
    tx.repo_mut().set_view(target_view);
    let new_repo = tx
        .commit(format!("restore to operation {op_spec}"))
        .map_err(map_backend_err)?;

    self.finish_op(py, ws, &name, &repo, &new_repo)
}
```

Shared tail (private helper on the same `impl PyWorkspace` block — **not** a `#[pymethods]` fn):

```rust
impl PyWorkspace {
    /// After an op-log write commits: if `@` moved between `old_repo` and `new_repo`, check out the
    /// new `@` on disk (reusing the held lock), then return the published operation as a plain dict.
    fn finish_op<'py>(
        &self,
        py: Python<'py>,
        ws: &mut Workspace,
        name: &jj_lib::ref_name::WorkspaceName,
        old_repo: &jj_lib::repo::ReadonlyRepo,
        new_repo: &jj_lib::repo::ReadonlyRepo,
    ) -> PyResult<Bound<'py, PyDict>> {
        let old_wc = old_repo.view().get_wc_commit_id(name).cloned();
        let new_wc = new_repo.view().get_wc_commit_id(name).cloned();
        if new_wc != old_wc
            && let Some(new_id) = new_wc
        {
            let new_commit = new_repo
                .store()
                .get_commit(&new_id)
                .map_err(map_backend_err)?;
            let op_id = new_repo.operation().id().clone();
            Self::checkout_locked(py, ws, op_id, &new_commit)?;
        }
        let data = OperationData::build(new_repo.operation());
        data.to_dict(py)
    }
}
```

> **Imports to add (verify each resolves):** `WorkspaceName` lives at `jj_lib::ref_name::WorkspaceName`
> (the `workspace_name()` accessor returns `&WorkspaceName`; `.to_owned()` gives `WorkspaceNameBuf`,
> which derefs to `&WorkspaceName` for `get_wc_commit_id`). `ReadonlyRepo` is `jj_lib::repo::ReadonlyRepo`.
> Prefer adding `use jj_lib::repo::ReadonlyRepo;` and `use jj_lib::ref_name::WorkspaceName;` (or
> reference them by path as above) — **grep `src/` for how `WorkspaceNameBuf`/`ReadonlyRepo` are
> already named before adding a duplicate import.**

**Verify while implementing (don't assume):**
- `RepoLoader: Clone` (repo.rs:663) — but you don't need to clone it here: the loader borrow is
  scoped inside the `{ … }` block and ends before `tx`/checkout need `&mut ws`, exactly as `snapshot`
  scopes its loader borrow. Confirm the borrow checker is happy; if it fights, `let loader =
  ws.repo_loader().clone();` outside the block is the escape hatch.
- `tx.commit(...)` returns `Arc<ReadonlyRepo>`; `&new_repo`/`&repo` deref-coerce to `&ReadonlyRepo`
  for `finish_op`.
- The two methods publish exactly one op each (`tx.commit` once) and never call
  `rebase_descendants` (no rewrites) — confirm `cargo test`/clippy stay clean.

---

## 3. Python facade + stubs

### `python/pyjutsu/workspace.py`

`Operation` is already imported. Add (e.g. next to `snapshot`/`is_stale`):

```python
def undo(self, operation: str | None = None) -> Operation:
    """Revert one operation, publishing a new operation that applies its reverse → that
    :class:`Operation`. With ``operation=None`` (the default) the **head** operation is undone;
    otherwise pass an op id, prefix, or expression (``"@"``, ``"@-"``, …).

    Matches ``jj undo``. Undoing the repo-initialization operation (it has no parent) or a merge
    operation raises :class:`~pyjutsu.errors.PyjutsuError`. If the reverse moves ``@``, the on-disk
    working copy is checked out to the new ``@``.
    """
    return Operation.model_validate(self._handle.undo(operation))

def restore_operation(self, operation: str) -> Operation:
    """Reset the repo to the state a past operation recorded, publishing a new operation → that
    :class:`Operation`. ``operation`` is an op id, prefix, or expression (``"@-"``, …).

    Matches ``jj op restore``. If the restored state moves ``@``, the on-disk working copy is
    checked out to it.
    """
    return Operation.model_validate(self._handle.restore_operation(operation))
```

### `python/pyjutsu/_pyjutsu.pyi`

Add to `PyWorkspace` (next to `update_stale`):

```python
    def undo(self, operation: str | None = ...) -> dict[str, object]: ...
    def restore_operation(self, operation: str) -> dict[str, object]: ...
```

---

## 4. Differential tests (`tests/test_undo.py`)

Reuse the harness (`_copy_repo`, the `jj`/`linear_repo`/`scratch_repo` fixtures, `jj.op_log_ids`,
`jj.commit_id`, `jj.change_id`, `jj.local_bookmarks`). **The contract is resulting-state parity, not
op-id parity** (§1): apply the same op-log write to two byte-identical copies (binding vs CLI) and
assert the repos end up describing the same `@`/graph/bookmarks. **Keep every scenario local-only**
(no git remote between the operations) so plain `merge`/`set_view` == the CLI (§1, §6).

Two harness conveniences worth noting:
- Count ops via the binding (`len(ws.operations())`) where convenient; `jj.op_log_ids` is fine here
  because these tests keep `@` fresh (unlike the stale tests, the CLI won't refuse `op log`).
- To name "the op that did X", capture `ws.head_operation()` (or `jj.op_head_id`) right after X.

Suggested cases:

- **`test_undo_describe_matches_cli`** *(headline)*: `other = _copy_repo(scratch_repo)`. On the
  binding, `with ws.transaction("d") as tx: tx.describe("@", "v2")`, then `op = ws.undo()`. On the
  CLI, `jj(other, "describe", "-m", "v2")` then `jj(other, "undo")`. Assert: `op.description`
  starts with `"undo operation "`; the `@` description is back to the fixture's
  `WC_DESCRIPTION` on both (`jj.template(repo, "@", "description")`); `jj.commit_id(scratch, "@") ==
  jj.commit_id(other, "@")`; one extra op on each side beyond the describe.
- **`test_undo_new_restores_at_and_checks_out`**: on `linear_repo`, `with ws.transaction("n") as tx:
  tx.new()` (a new empty `@` on top of old `@`), then `ws.undo()`. Assert `@`'s change id is back to
  the pre-`new` `@` (compare against a `change_id` captured before), and on disk the tree matches the
  restored `@` (e.g. the files present match `jj`'s `@` on the CLI copy that did the same
  describe-free `new`+`undo`). Confirms `finish_op`'s checkout fires when undo moves `@`.
- **`test_undo_specific_operation`**: do two ops (e.g. `describe` then `new`); capture the describe's
  op id; `ws.undo(<that op id>)` undoes the *older* one specifically; assert the resulting state
  matches `jj(other, "undo", <same op id>)`. (Exercises the non-default `operation` arg + parity.)
- **`test_undo_root_operation_raises`**: `with pytest.raises(PyjutsuError): ws.undo(<root/init op>)`
  — resolve the oldest op via `jj.op_log_ids(repo)[-1]`. (The init op has no parent.)
- **`test_undo_merge_operation_raises`** *(optional, if a 2-parent op is easy to produce)*: otherwise
  cover the >1-parent guard by code inspection and skip — note it in the test module docstring.
- **`test_restore_operation_matches_cli`** *(headline)*: on `linear_repo`, capture an earlier op id
  `op0 = jj.op_log_ids(repo)[k]` (e.g. the op right after commit **A** was described). Do a couple
  more mutations through the binding, then `ws.restore_operation(op0)`. On the CLI copy run the same
  mutations then `jj(other, "op", "restore", op0)`. Assert the resulting `@` commit id, the log graph
  (`jj.change_ids(repo, "::@")`), and local bookmarks all match across the two repos, and that
  `restore_operation` returned an `Operation` whose description starts with `"restore to operation "`.
- **`test_restore_to_head_is_state_noop`**: `ws.restore_operation(ws.head_operation())` returns an
  `Operation` and leaves the `@`/graph unchanged (a new op may be published, but the *state* is
  identical) — documents the all-portions set_view semantics.
- **`test_invalid_operation_raises`**: `with pytest.raises(PyjutsuError): ws.undo("deadbeefnotanop")`
  and likewise `ws.restore_operation("deadbeefnotanop")` (mirrors `test_at_operation`).

Re-run the **whole** suite — these methods are additive, and the §2.1 `checkout_wc` refactor is a pure
extraction, so all prior tests (incl. `test_new`'s checkout assertions and `test_snapshot`) must stay
green. Confirm, don't assume.

---

## 5. Build / verify / report

In the devenv shell:

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint
```

All green (pytest + `cargo test` + clippy `-D warnings` + ruff), then **stop and report at the slice
boundary** before slice 8 (`rebase` / `squash` / `restore` — **verify the rewrite.rs field names at
that slice**, per continuation guide §4). Commit on `main` (`Implement M2 slice 7: undo /
restore_operation`); **no AI attribution** anywhere.

---

## 6. Guardrails (carried)

- **Thin Rust, rich Python:** both methods return a plain op dict (via `OperationData`); the
  `Operation` model is built in Python. Never leak jj-lib types.
- **GIL discipline:** the op-store reads (`load_at_head`/`load_at`/`resolve_op_for_load`) and the
  checkout I/O run under `py.allow_threads`; the `!Send` `Transaction` (merge/set_view/commit) runs
  on the GIL between those spans. The workspace `Mutex` is held for the whole sequence (atomicity);
  that is precisely why the checkout must go through `checkout_locked`, not the re-locking
  `checkout_wc` ([[m2-transaction-not-send]], [[m2-slice2-new-checkout]], [[m2-slice5-snapshot]]).
- **Faithful primitive:** `undo` = `merge(base = bad, other = parent)`; `restore_operation` =
  `set_view(target.view)`. Both publish exactly one op and check out `@` if it moved. **Partial-
  restore portions (`view_with_desired_portions_restored`) and the remote-tracking-ref nuance are a
  documented refinement, out of scope** — tests stay local-only where the plain primitives equal the
  CLI. Undoing the init op / a merge op is a `PyjutsuError`.
- **State parity, not op-id parity:** op ids embed wall-clock time + hostname, so assert resulting
  repo state across binding vs CLI, never op-id equality (§1).
- **Pin stays `=0.38.0`; `Cargo.lock` committed; everything through devenv** — never bare
  `cargo`/`maturin`/`pytest`/`jj`. The pinned `jj` 0.38.0 CLI is the differential oracle only.

> **Slice-5 lesson applied here:** the trap this slice is `Operation::view()` returning the
> high-level `view::View` (needs `.store_view().clone()` for `set_view`), and
> `view_with_desired_portions_restored` *not* existing in jj-lib 0.38 (it's CLI-only). Both were
> grepped against the pinned source while writing this guide; **re-grep any API a future reader
> doubts.** See [[m2-slice6-stale]].
