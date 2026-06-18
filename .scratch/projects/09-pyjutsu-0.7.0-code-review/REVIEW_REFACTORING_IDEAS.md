# Pyjutsu Code Review — Refactoring Ideas & Fix Options

Companion to `CODE_REVIEW.md`. For each finding, this lays out the realistic options, their
trade-offs, and a recommendation. Nothing here has been applied — these are decisions to make.

Ordering follows the review's severity: **H1** (fix first), **M1**, then the **L** / **N** items.

---

## H1 — Transaction slot leak on commit failure

**Problem recap.** `PyTransaction::commit` (`src/transaction.rs:598`) consumes the native
transaction with `self.take()` and only calls `self.release_slot()` *after* the two fallible calls
(`rebase_descendants`, `tx.commit`). If either errors, the slot (`tx_open: AtomicBool`) is never
released, and `Drop` can't help because the transaction `Option` is already `None`. The workspace
then rejects all future transactions.

**Root cause.** "Slot is released" and "native tx is consumed" are coupled to *success* instead of to
*consumption*. Once `take()` succeeds, the tx is gone no matter what, so the slot must be freed on
every exit path.

### Option A — Release immediately after `take()` (smallest change) ✅ Recommended

Move `self.release_slot()` to right after the successful `take()`, before the fallible work:

```rust
fn commit(&self, py: Python<'_>, description: String) -> PyResult<String> {
    let mut tx = self.take()?;   // if this errors, slot was already released by a prior call
    self.release_slot();         // tx is consumed; the "one open tx" invariant is now satisfied
    tx.repo_mut().rebase_descendants().map_err(map_backend_err)?;
    let new_repo = tx.commit(description).map_err(map_backend_err)?;
    // ... post-commit checkout (errors here no longer leak the slot) ...
}
```

- **Pros:** one-line move; correct for all error paths; `rollback` already follows this exact shape
  (`take()?` then `release_slot()`).
- **Cons:** the slot becomes free a few statements earlier, so during the post-commit checkout
  another thread *could* `begin_transaction`. That is already true today on the success path (the
  checkout runs after `release_slot()` in the current code), so this changes nothing about that
  window — see L1 for the concurrency discussion.
- **Apply to:** `commit`. `rollback` is already correct.

### Option B — RAII release guard

Introduce a small guard that releases the slot on `Drop`, armed for the duration of `commit`:

```rust
struct SlotGuard<'a>(&'a AtomicBool);
impl Drop for SlotGuard<'_> {
    fn drop(&mut self) { self.0.store(false, Ordering::Release); }
}
// in commit(): let _slot = SlotGuard(&self.tx_open);  // released on any return
```

- **Pros:** robust against future code added between `take()` and the end; intent is explicit.
- **Cons:** more machinery than the bug warrants; must ensure it doesn't double-release with the
  existing `Drop for PyTransaction` (it won't, since the tx is `None` by then, but it's another
  invariant to hold in your head).

### Option C — Fix in `Drop` only (insufficient — do not do alone)

Make `Drop` release unconditionally. **Rejected:** `Drop` runs at an unpredictable time (whenever the
last Python ref goes away, possibly much later), so the workspace would stay wedged until GC. Option
A frees the slot synchronously, which is what callers expect.

**Recommendation:** **Option A.** It's minimal, matches `rollback`'s existing shape, and fully fixes
the leak.

### Regression test (add regardless of option)

The bug is invisible without a test that makes `commit` fail. Sketch:

```python
def test_failed_commit_releases_transaction_slot(scratch_repo, monkeypatch):
    ws = pyjutsu.Workspace.load(scratch_repo)
    # Force commit to fail. Easiest reliable trigger: make the op-store unwritable, or
    # monkeypatch a hook that raises. If no clean injection point exists, this finding
    # argues for adding a test seam (see "Testability" below).
    with pytest.raises(pyjutsu.PyjutsuError):
        with ws.transaction("will fail") as tx:
            tx.describe("@", "x")
            # ... induce failure at commit ...
    # The slot must be free again:
    with ws.transaction("second") as tx:   # must NOT raise "already open"
        tx.describe("@", "ok")
```

**Testability note.** There is currently no clean way to force `tx.commit()` to fail from Python.
Options: (1) a `#[cfg(test)]`/feature-gated fault-injection hook in the Rust layer; (2) a Python-level
test that revokes write permission on `.jj/repo/op_store` for the duration of the `with` block (OS
dependent, but devenv is Linux-only here); (3) accept a Rust-side `#[test]` that drives
`PyTransaction` directly with a poisoned transaction. (2) is the most faithful end-to-end check.

---

## M1 — Committed-but-checkout-failed transaction reports as not committed

**Problem recap.** After `tx.commit()` publishes the operation, a failing post-commit `checkout_wc`
propagates an error, so Python's `__exit__` never records `committed` / `operation_id` — the caller
sees a "failed" transaction that actually landed an operation.

### Option A — Distinct, recoverable error carrying the op id ✅ Recommended

Raise `StaleWorkingCopyError` (already in the hierarchy) from the checkout-failure path, with a
message that says the operation *was* published and the working copy needs reconciliation:

```rust
// after tx.commit() succeeds, if checkout_wc fails:
return Err(StaleWorkingCopyError::new_err(format!(
    "operation {op} was published but the working copy could not be checked out; \
     run update_stale to reconcile", op = new_repo.operation().id().hex()
)));
```

- **Pros:** callers can catch `StaleWorkingCopyError` specifically and call `ws.update_stale()`; the
  error name already means exactly this; the op id is surfaced for logging.
- **Cons:** the Python `Transaction` object still won't have `operation_id` set (the exception fires
  before `__exit__` records it). If that matters, also have `commit` set the op id before attempting
  the checkout (return it via the exception, or restructure so `__exit__` records the id first).

### Option B — Record the op id before checkout, re-raise on checkout failure

Split the native `commit` so it returns the op id as soon as `tx.commit()` succeeds, then performs
the checkout as a *separate* step whose failure is reported without losing the id:

```python
# transaction.py __exit__
op_id = native.commit(self._description)   # returns as soon as op is published
self._operation_id = op_id
self._state = "committed"
native.finish_checkout()                   # may raise StaleWorkingCopyError; tx already committed
```

- **Pros:** `operation_id` is always populated for a published op; cleanest separation of "committed"
  from "working copy reconciled."
- **Cons:** larger change (splits the native method, adds a Python call); two FFI hops on commit.

### Option C — Document only

Leave behavior as-is; document that a raised `commit`/`__exit__` may still have published the
operation and `operation_id` may be `None`.

- **Pros:** zero code change.
- **Cons:** leaves a genuinely confusing failure mode in place.

**Recommendation:** **Option A** for the next release (cheap, uses the right error type), and
consider **Option B** if/when `operation_id`-after-partial-failure becomes important to a consumer.

---

## L1 — Single-transaction guard doesn't cover other mutators

**Problem recap.** `tx_open` only gates `begin_transaction`. `undo`/`snapshot`/`git_*`/remote-CRUD
mutate via their own transactions and can run concurrently with an open `PyTransaction` (e.g. via
`asyncio.to_thread`). Not corruption (jj tolerates concurrent ops), but surprising.

### Option A — Document the concurrency contract ✅ Recommended (first step)

State plainly in the `Workspace`/`Transaction` docstrings and README that workspace-level mutators
are **not** mutually excluded with an open transaction; concurrent mutations become divergent jj
operations that merge. Keep current behavior.

- **Pros:** zero risk; accurately describes jj's model; honest.
- **Cons:** doesn't prevent foot-guns.

### Option B — Extend the `tx_open` guard to all mutators

Have `undo`/`restore_operation`/`snapshot`/`git_*`/remote-CRUD check `tx_open` and raise if a
transaction is open.

- **Pros:** prevents accidental interleaving.
- **Cons:** a behavior change; could break legitimate patterns (e.g. a background fetch while editing
  is *fine* in jj's model). Also asymmetric — reads would still be allowed, which is correct, so the
  rule becomes "no two writers," which is reasonable but must be deliberate. Watch for ordering: the
  guard would need to be checked *and* the mutator would need to not deadlock against a transaction
  that itself never holds the workspace `Mutex`.

### Option C — Make `PyTransaction` hold the workspace `Mutex` for its lifetime

The strongest exclusion: a held transaction blocks all other workspace access.

- **Pros:** truly serializes.
- **Cons:** **likely a non-starter** — the `MutexGuard` is not `Send`/storable in the `unsendable`
  handle cleanly, and it would block *reads* too (and even `repr`/`name`), which is too coarse.
  Rejected.

**Recommendation:** **Option A** now; revisit **Option B** only if a consumer reports a real
foot-gun. Avoid C.

---

## L2 — `Workspace` read conveniences reload the repo at head each call

**Problem recap.** `ws.log()`, `ws.diff_stat()`, etc. each call `self.head()` →
`loader.load_at_head()`, so repeated reads reload the repo N times.

### Option A — Documentation nudge ✅ Recommended (low effort)

Strengthen the `Workspace` docstring: "Each `ws.<read>()` shortcut loads a fresh head view; for
several reads, obtain one view with `view = ws.head()` and reuse it." (Already implied; make it
explicit and prominent.)

### Option B — Cached head view with explicit invalidation

Memoize `self._head` and invalidate it after any mutation (transaction commit, `undo`, `snapshot`,
`git_*`).

- **Pros:** matches the "load once, reuse" thesis; faster bulk reads.
- **Cons:** invalidation is error-prone — every mutation path (including ones that publish ops out
  from under a cached view) must reset it, or reads silently observe a stale operation. The CLI-like
  "always observe latest op" semantics would also change subtly (a cached view pins an op). This is a
  real semantics shift, not just an optimization.

### Option C — Let the native layer cache the loaded repo

Cache `Arc<ReadonlyRepo>` at head inside `PyWorkspace`, refreshed when the on-disk op head advances.

- **Pros:** transparent; reads stay current by checking the head op id (cheap) before reusing.
- **Cons:** most complex; needs a cheap "has the head op changed?" probe to stay correct.

**Recommendation:** **Option A** for now. Pursue **Option C** only if profiling shows read-heavy
workloads are dominated by `load_at_head`. Avoid **Option B**'s footgun-prone Python-level cache.

---

## L3 — `evaluate_ids` vs `evaluate` error-class mismatch

**Fix (trivial):** in `src/revset.rs:103`, change `id.map_err(map_revset_err)` to
`id.map_err(map_backend_err)` so the streaming `iter_log` path classifies iteration/store failures
the same as the eager `log` path. No options to weigh — just align them. Add a one-line comment that
parse/resolve already happened in `evaluate_revset` (which uses `map_revset_err`), so the *iteration*
error is a backend error.

---

## N1 — `HunkLine.kind` advertises an unreachable `"context"` variant

### Option A — Drop `"context"` from the Literal ✅ Recommended

`kind: Literal["added", "removed"]`. Matches what the Rust actually emits; regenerate the golden if
the field set is affected (it isn't — this is a value constraint, not a field).

### Option B — Keep it, add a reserved-for-future comment

If a context-windowed diff mode is genuinely planned (it's flagged out of scope in §12), keep
`"context"` and comment that it is reserved. Lower churn if the variant will return.

**Recommendation:** **Option A** unless context-windowed diffs are imminent.

---

## N2 — Secondary-workspace settings may diverge from the CLI

### Option A — Document the caveat ✅ Recommended (first step)

Add to the fidelity notes: "commit-id parity with the CLI is guaranteed for the **default** workspace;
a secondary workspace skips the repo `config.toml` layer (its `.jj/repo` is a pointer file), so
settings — and thus authored commit ids — may differ."

### Option B — Resolve the pointer file and load the real repo config

In `load_user_settings`, when `.jj/repo` is a pointer file, read it to find the real repo dir and
load `<real-repo>/config.toml` as the repo layer.

- **Pros:** restores full fidelity for secondary workspaces.
- **Cons:** must replicate jj's pointer-file format/semantics exactly; another place that tracks
  jj-lib internals. Verify against jj-lib's own resolution (there may be a helper to reuse).

**Recommendation:** **Option A** now; **Option B** if secondary-workspace authoring becomes a
supported, fidelity-tested use case (add a differential test for it).

---

## N3 — `Commit.bookmarks` "sorted" guarantee rests on jj-lib internals

### Option A — Sort explicitly at the boundary ✅ Recommended

In `CommitData::build` (`src/convert.rs:79`), `sort()` the collected bookmark names before storing.
Makes the documented guarantee true regardless of jj-lib's iteration order.

### Option B — Soften the docstring

Change "(sorted)" to "(in jj's bookmark order)". Zero code, but a weaker contract.

**Recommendation:** **Option A** — a one-line `.sort()` is cheaper than a caveat and gives a stable
contract.

---

## N4 — `run_jj` passes the live `os.environ`

**Fix (trivial):** `env={**os.environ}` (or `dict(os.environ)`) at `workspace.py:443` to make the
snapshot-at-call-time explicit. No behavior change.

---

## Suggested sequencing

1. **H1** + its regression test (blocking for next tag).
2. **L3, N3, N4** — trivial, bundle into the same change.
3. **M1** (Option A) — small, meaningful behavior clarification.
4. **N1, N2(A)** — doc/type polish.
5. **L1(A), L2(A)** — documentation of the concurrency + reload contracts.
6. Revisit **L1(B)**, **L2(C)**, **M1(B)**, **N2(B)** only if a consumer need or profiling justifies
   the larger changes.

## Testing to add alongside

- Transaction-slot-release-after-failed-commit (H1) — highest value.
- `run_jj` error branches: binary-not-found, `check=False` non-zero, `OSError` on launch.
- (If pursued) concurrency test for L1; commit-succeeds-checkout-fails test for M1.
- (If N2(B)) a differential commit-id test authored in a secondary workspace.
