# Pyjutsu M2 — Slice 6 Implementation Guide (stale working copy: `is_stale` / `update_stale`)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `M2_CONTINUATION_GUIDE.md` (the
> corrected spine for slices 2–11; §1 corrections + §2 conventions + §4 slice plans still hold;
> slice 6 plan at §4) → **this document** (the detailed, verified plan for slice 6 specifically) →
> `M2_IMPLEMENTATION_GUIDE.md` (original plan; error taxonomy + API table — but the continuation
> guide and this doc win where they disagree) → the code it produces.
>
> **Pin unchanged:** `jj-lib = "=0.38.0"`. Slices 0–5 are committed and pushed on `main`
> (slice 5 = `13deb07`); the working tree is clean — start from `main`. Slices 0–1 shipped as pyjutsu
> `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2 bumps to `0.41.0`. API refs below
> are `file:line` into `~/.cargo/registry/.../jj-lib-0.38.0/src/`, **verified against the pinned
> source** while writing this guide.

---

## 0. Where slice 6 starts

Slice 5 landed `Workspace.snapshot()` + auto-snapshot on tx open, and — the foundation this slice
builds on — already calls **`WorkingCopyFreshness::check_stale`** (working_copy.rs:363) inside
`PyWorkspace::snapshot`, mapping `WorkingCopyStale`/`SiblingOperation` → `StaleWorkingCopyError`
(`src/workspace.rs`). So **a stale `@` already refuses to snapshot, and — because auto-snapshot runs
first on `Transaction.__enter__` — a stale `@` already refuses the default mutation path** (the
auto-snapshot raises before `begin_transaction`).

What's still missing is the **explicit, queryable surface** the CLI exposes as `jj workspace
update-stale` and its staleness check:

1. **`Workspace.is_stale() -> bool`** — is the on-disk working copy behind / diverged from the repo's
   current `@` (would a `jj` command complain / auto-reconcile)? A read-only probe.
2. **`Workspace.update_stale() -> Commit | None`** — reconcile a stale working copy by checking out
   the repo's current `@` into it (matches `jj workspace update-stale`), returning the now-current
   `@` :class:`Commit`; **`None`** when the working copy was already fresh (nothing to do).

> **What "stale" means here (verified from `check_stale`, working_copy.rs:359–402).** The working
> copy records the operation it was last written at (`.jj/working_copy`); the repo loads at its head
> operation. `check_stale` compares them:
> - WC op **==** repo op → `Fresh`.
> - WC op is an **ancestor** of repo op (the repo advanced past the WC) **and** the on-disk tree
>   differs from the repo `@`'s tree → `WorkingCopyStale`. (If the trees match — e.g. a bookmark
>   write that moved no commit — it's still `Fresh`: nothing to check out. This is exactly why slice 4
>   needed no checkout.)
> - WC op is a **descendant** of repo op → `Updated(op)` (the repo we loaded is behind; **won't
>   arise here** because we always `load_at_head`).
> - Otherwise (divergent op branches) → `SiblingOperation`.
> `is_stale()` is `true` for `WorkingCopyStale | SiblingOperation`.

**Reproducing staleness for the differential test (verified against the pinned 0.38.0 CLI).** Within
one colocated workspace the binding keeps `@` and the on-disk WC in lockstep (every `@`-rewrite
checks out), so staleness must be induced by an **external** actor. The pinned CLI's
`--ignore-working-copy` advances the repo **without** snapshotting or checking out, which is the
clean trigger:

```
# linear_repo: A(a.txt) → B(a,b) → C(a,b,c) → @(empty); disk has a.txt b.txt c.txt
jj --ignore-working-copy edit <A>     # repo @ now = A (tree: a.txt only); disk untouched (stale)
# → binding: ws.is_stale() is True; ws.snapshot()/mutation raise StaleWorkingCopyError
jj workspace update-stale             # checks out A: removes b.txt/c.txt; WC op → head
# → "Added 0 files, modified 0 files, removed 2 files" / "Updated working copy to fresh commit ..."
# running update-stale again when fresh → "the working copy is not stale" (exit 0, no-op)
```

This was confirmed end-to-end with the devenv-pinned `jj` while writing this guide.

## 1. Verified jj-lib APIs (against the pinned 0.38.0 source)

| What | Signature | Ref |
|---|---|---|
| Freshness check | `WorkingCopyFreshness::check_stale(locked_wc: &dyn LockedWorkingCopy, wc_commit: &Commit, repo: &ReadonlyRepo) -> Result<Self, OpStoreError>` | working_copy.rs:363 |
| Freshness states | `Fresh / Updated(Box<Operation>) / WorkingCopyStale / SiblingOperation` | working_copy.rs:346 |
| Begin WC mutation (lock) | `Workspace::start_working_copy_mutation(&mut self) -> Result<LockedWorkspace<'_>, WorkingCopyStateError>` | workspace.rs:427 |
| Locked WC handle | `LockedWorkspace::locked_wc(&mut self) -> &mut dyn LockedWorkingCopy` | workspace.rs:471 |
| **Forced checkout** (reconcile) | `Workspace::check_out(&mut self, operation_id: OperationId, old_tree: Option<&MergedTree>, commit: &Commit) -> Result<CheckoutStats, CheckoutError>` | workspace.rs:437 |
| Repo head op id | `ReadonlyRepo::op_id(&self) -> &OperationId` / `operation(&self) -> &Operation` | repo.rs:289/293 |
| `@` commit id at head | `repo.view().get_wc_commit_id(&WorkspaceName) -> Option<&CommitId>` | (used in slice 5) |
| Load `@` commit | `repo.store().get_commit(&CommitId) -> BackendResult<Commit>` | store.rs:151 |
| Recovery (missing `@`) | `create_and_check_out_recovery_commit(locked_wc, repo, name, desc) -> Result<(Arc<ReadonlyRepo>, Commit), RecoverWorkspaceError>` | working_copy.rs:425 |

Notes verified in source:

- **`check_out` with `old_tree = None` is the forced checkout** `update_stale` needs. With
  `Some(old_tree)` it errors `ConcurrentCheckout` when the on-disk tree differs from what the lock
  sees — which is *precisely* the stale case — so the slice-2 helper `checkout_wc` (which passes
  `Some`) **cannot** be reused here. `check_out` internally does
  `start_working_copy_mutation → locked_wc.check_out(commit) → finish(operation_id)`, all under one
  WC lock, all I/O — wrap the call in `py.allow_threads`.
- `check_stale` needs a **`&dyn LockedWorkingCopy`** (for `old_operation_id()` + `old_tree()`), so
  `is_stale()` must take the WC lock, check, and drop it. The lock is cheap and is serialized with
  snapshot/transactions by the workspace `Mutex` (`self.locked()`). There is a non-locking
  `Workspace::working_copy().operation_id()` (working_copy.rs:62), but replicating the ancestor walk
  + tree comparison by hand is fragile — **use the official `check_stale`.**
- `Updated`/`SiblingOperation`/recovery: `update_stale`'s forced `check_out(head_op, None, @)` covers
  `WorkingCopyStale` and `SiblingOperation`. The **missing-`@`** recovery path (the WC commit was
  abandoned, so `get_commit` fails) is what `create_and_check_out_recovery_commit` handles — **flag
  it as a documented refinement; out of scope for this slice** (the test edits to a still-existing
  commit). If `get_commit` fails, let it map to `BackendError` for now.

## 2. Rust: two `#[pymethods]` on `PyWorkspace` (`src/workspace.rs`)

Both follow the slice-5 `snapshot` skeleton (load at head → resolve `@` → lock). Add imports:
`crate::convert::CommitData` (extend the existing `use crate::convert::OperationData;` to
`{CommitData, OperationData}`). `WorkingCopyFreshness`, `StaleWorkingCopyError`, `map_backend_err`,
`map_workingcopy_err`, `Repo` are already imported (slice 5).

```rust
/// Whether the on-disk working copy is **stale** relative to the repo's current `@` — i.e. the repo
/// advanced past (or diverged from) the operation the working copy was last written at, and the
/// on-disk tree no longer matches `@`. A read-only probe (matches what `jj` checks before each
/// command); mutating or snapshotting a stale `@` raises `StaleWorkingCopyError`.
fn is_stale(&self, py: Python<'_>) -> PyResult<bool> {
    let mut guard = self.locked()?;
    let ws: &mut Workspace = &mut guard;

    let repo = {
        let loader = ws.repo_loader();
        py.allow_threads(|| loader.load_at_head()).map_err(map_backend_err)?
    };
    let name = ws.workspace_name().to_owned();
    let Some(wc_commit_id) = repo.view().get_wc_commit_id(&name).cloned() else {
        return Ok(false); // no `@` in this workspace ⇒ nothing can be stale
    };
    let wc_commit = repo.store().get_commit(&wc_commit_id).map_err(map_backend_err)?;

    // check_stale needs the WC lock (old_operation_id + old_tree); take it, check, drop it.
    let mut locked_ws = ws.start_working_copy_mutation().map_err(map_workingcopy_err)?;
    let freshness = WorkingCopyFreshness::check_stale(locked_ws.locked_wc(), &wc_commit, &repo)
        .map_err(map_backend_err)?;
    Ok(matches!(
        freshness,
        WorkingCopyFreshness::WorkingCopyStale | WorkingCopyFreshness::SiblingOperation
    ))
}

/// Reconcile a stale working copy: check out the repo's current `@` into it (matches
/// `jj workspace update-stale`), returning the now-current `@` as a plain dict — or `None` if the
/// working copy was already fresh (nothing to do). The checkout is I/O and runs **off the GIL**.
fn update_stale<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
    let mut guard = self.locked()?;
    let ws: &mut Workspace = &mut guard;

    let repo = {
        let loader = ws.repo_loader();
        py.allow_threads(|| loader.load_at_head()).map_err(map_backend_err)?
    };
    let name = ws.workspace_name().to_owned();
    let Some(wc_commit_id) = repo.view().get_wc_commit_id(&name).cloned() else {
        return Ok(None);
    };
    let wc_commit = repo.store().get_commit(&wc_commit_id).map_err(map_backend_err)?;

    // 1. Staleness check (own lock scope; dropped before the forced checkout re-locks).
    let stale = {
        let mut locked_ws = ws.start_working_copy_mutation().map_err(map_workingcopy_err)?;
        let freshness = WorkingCopyFreshness::check_stale(locked_ws.locked_wc(), &wc_commit, &repo)
            .map_err(map_backend_err)?;
        matches!(
            freshness,
            WorkingCopyFreshness::WorkingCopyStale | WorkingCopyFreshness::SiblingOperation
        )
    };
    if !stale {
        return Ok(None); // matches the CLI's "the working copy is not stale" no-op
    }

    // 2. Forced checkout of `@` at head. `old_tree = None` bypasses the ConcurrentCheckout guard —
    //    which would otherwise trip on exactly the stale on-disk tree we mean to overwrite.
    let op_id = repo.operation().id().clone();
    py.allow_threads(|| ws.check_out(op_id, None, &wc_commit))
        .map_err(map_workingcopy_err)?;

    // 3. Return the reconciled `@` (build off the GIL — `is_empty` touches the backend).
    let data = py.allow_threads(|| CommitData::build(&*repo, &wc_commit))?;
    Ok(Some(data.to_dict(py)?))
}
```

**Verify while implementing (don't assume):**
- `repo.operation().id().clone()` is an `OperationId` (the type `check_out` wants) — the slice-5
  `snapshot` and `PyTransaction::commit` already use exactly this.
- `&*repo` (an `&ReadonlyRepo`) coerces to the `&dyn Repo` that `CommitData::build` takes; it is
  `Send`/Ungil, so the `allow_threads` build compiles. (If the borrow checker fights the
  `allow_threads` build, building on the GIL is acceptable — it's one small commit read — but prefer
  off-GIL per `convert.rs`'s contract.)
- The two sequential WC locks in `update_stale` (the check, then `check_out`'s own lock) are fine:
  the first `locked_ws` drops at the end of its block before `check_out` re-locks.

> **Documented refinements (call out; don't silently hardcode):** (a) the **missing-`@` recovery**
> path (`create_and_check_out_recovery_commit`) when the working-copy commit was abandoned — out of
> scope here; (b) `is_stale()`/`update_stale()` treat `Updated` as "fresh" because we always load at
> head, so it cannot arise in-process; a multi-process binding would revisit this.

## 3. Python facade + stubs

### `python/pyjutsu/workspace.py`

```python
def is_stale(self) -> bool:
    """Whether the on-disk working copy is stale relative to the repo's current ``@``.

    The repo advanced past (or diverged from) the operation the working copy was last written at,
    and the on-disk tree no longer matches ``@`` — a ``jj`` command would auto-reconcile (or
    refuse). Mutating or snapshotting a stale ``@`` raises
    :class:`~pyjutsu.errors.StaleWorkingCopyError`; call :meth:`update_stale` to reconcile.
    """
    return self._handle.is_stale()

def update_stale(self) -> Commit | None:
    """Reconcile a stale working copy by checking out the repo's current ``@`` → that
    :class:`Commit`, or ``None`` if the working copy was already fresh (nothing to do).

    Matches ``jj workspace update-stale``. The on-disk files are updated to ``@``'s tree and the
    working copy's recorded operation is advanced to the repo head.
    """
    row = self._handle.update_stale()
    return Commit.model_validate(row) if row is not None else None
```

`Commit` is already imported in `workspace.py`.

### `python/pyjutsu/_pyjutsu.pyi`

Add to `PyWorkspace` (next to `snapshot`):

```python
    def is_stale(self) -> bool: ...
    def update_stale(self) -> dict[str, object] | None: ...
```

## 4. Differential tests (`tests/test_stale.py`)

Reuse the slice-2/5 harness (`_copy_repo`, the `jj`/`linear_repo` fixtures). Induce staleness with
`jj --ignore-working-copy edit <A>` (verified above). `StaleWorkingCopyError` is exported from
`pyjutsu` (re-exported via `python/pyjutsu/errors.py`).

Helper for the oldest non-root change in `linear_repo` (commit **A**, tree = `a.txt` only):
`a_change = jj.change_ids(linear_repo, "::@ ~ root()")[-1]` (newest-first, so last == oldest), as
`tests/test_new.py` already does.

- **`test_is_stale_true_after_external_edit`**: `jj(linear, "--ignore-working-copy", "edit", a_change)`;
  `ws = Workspace.load(linear)`; assert `ws.is_stale() is True`. (Sanity: `b.txt`/`c.txt` still on
  disk before reconciling.)
- **`test_is_stale_false_when_fresh`**: untouched `linear_repo`; `ws.is_stale() is False`. Also on a
  freshly-snapshotted clean `scratch_repo`.
- **`test_update_stale_reconciles_matches_cli`** (the headline): `other = _copy_repo(linear)`; on both,
  `jj(repo, "--ignore-working-copy", "edit", a_change)`. Binding: `c = ws.update_stale()`. CLI:
  `jj(other, "workspace", "update-stale")`. Assert: `c is not None` and `c.commit_id ==
  jj.commit_id(linear, "@")`; on **both** repos `a.txt` exists and `b.txt`/`c.txt` are gone; the
  on-disk `@` commit ids match (`jj.commit_id(linear, "@") == jj.commit_id(other, "@")`); and
  `ws.is_stale() is False` afterward.
- **`test_update_stale_noop_when_fresh`**: untouched `linear_repo`; `ws.update_stale() is None`; op
  count unchanged; `is_stale()` still `False`. (Mirrors the CLI's "not stale" no-op; a forced
  checkout is *not* performed.)
- **`test_mutation_on_stale_raises`**: stale via `--ignore-working-copy edit`; `with
  pytest.raises(StaleWorkingCopyError): with ws.transaction("x") as tx: tx.describe("@", "m")` — the
  **auto-snapshot** on `__enter__` raises before any mutation (locks in slice 5's guard). Confirm the
  op log did **not** grow (nothing was published).
- **`test_snapshot_on_stale_raises`**: stale; `with pytest.raises(StaleWorkingCopyError):
  ws.snapshot()`.
- **`test_update_stale_then_mutate`** *(integration)*: stale → `ws.update_stale()` → now `with
  ws.transaction("describe") as tx: tx.describe("@", "m")` succeeds (no raise), proving reconcile
  clears the block.

Re-run the **whole** suite — these methods are additive (no change to existing call paths), so all
prior tests must stay green. Confirm, don't assume.

## 5. Build / verify / report

In the devenv shell:

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint
```

All green (pytest + `cargo test` + clippy `-D warnings` + ruff), then **stop and report at the slice
boundary** before slice 7 (`undo` / `restore_operation` — the `Transaction::merge`/`set_view`
building blocks; consider pinning `debug.operation-timestamp` if op-id equality is asserted there).
Commit on `main` (`Implement M2 slice 6: stale working copy`); **no AI attribution** anywhere.

## 6. Guardrails (carried)

- **Thin Rust, rich Python:** `is_stale` returns a plain `bool`; `update_stale` returns a plain dict
  (or `None`). Never leak jj-lib types; the `Commit` model is built in Python.
- **GIL discipline:** the WC lock + the forced `check_out` (all I/O) run under `py.allow_threads`;
  hold the workspace `Mutex` for the whole sequence. ([[m2-transaction-not-send]],
  [[m2-slice2-new-checkout]], [[m2-slice5-snapshot]].)
- **Faithful primitive:** `update_stale` is a *no-op returning `None`* on a fresh WC (matches the
  CLI); it reconciles only a genuinely stale one, by checking out the recorded `@`. Missing-`@`
  recovery is a flagged refinement. Mutating/snapshotting a stale `@` raises `StaleWorkingCopyError`
  (already true via slice 5's snapshot guard + auto-snapshot).
- **Pin stays `=0.38.0`; `Cargo.lock` committed; everything through devenv** — never bare
  `cargo`/`maturin`/`pytest`/`jj`. The pinned `jj` 0.38.0 CLI is the differential oracle only.

> **Note for whoever writes slice 7's guide:** slice 5's *original* guide table mislabeled two APIs
> (there is **no** `Commit::tree_id()` and **no** `CommitBuilder::set_tree_id` in 0.38 — the shipped
> code uses `commit.tree_ids()` and `CommitBuilder::set_tree(MergedTree)`). Lesson: **grep the pinned
> source for every API named in a guide before trusting the name.** See [[m2-slice5-snapshot]].
