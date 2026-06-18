# Pyjutsu — Deep Code Review (v0.7.0)

**Reviewed commit:** `75b941e` (Bump pyjutsu 0.6.0 -> 0.7.0, power-user surface milestone)
**Scope reviewed:** the full library — `src/*.rs` (3,312 lines), `python/pyjutsu/*.py` +
`_pyjutsu.pyi` (1,694 lines), the test harness (`tests/`, 195 tests), and `docs/PYJUTSU_CONCEPT.md`.
**Bound engine:** `jj-lib = "=0.38.0"` (hard pin).

---

## 1. What it is

**Pyjutsu is an in-process Python binding to Jujutsu's Rust engine (`jj-lib`), exposed through
PyO3/maturin as a compiled wheel.** Jujutsu (`jj`) is a Git-compatible VCS whose distinguishing
features are first-class conflicts (stored, N-sided, never blocking), an operation log (undo for
*every* repo-state change, not just commits), revsets (a query language over the commit graph), and
multiple working copies ("workspaces") sharing one store.

Today, most Python code that drives `jj` shells out to the `jj` CLI and parses template/text output
(this is what gitman's `jj.py` and the *previous*, CLI-wrapper Pyjutsu do). That approach has a hard
ceiling, enumerated in concept §1–2:

- process-spawn + repo-load + working-copy-snapshot cost on **every** invocation;
- brittle text/template parsing against an intentionally unstable CLI surface;
- no native cross-call transaction (it must be simulated via op-id capture + `jj op restore`);
- `json()` template limitations that force hand-built, scalar-only JSON.

Pyjutsu removes that ceiling by binding `jj-lib` **in-process**: read the commit graph, op log,
working copy, revsets, and conflicts as native Rust data, and perform mutations inside `jj-lib`'s
real `Transaction` (one atomic operation). The price is a Rust build and tracking jj-lib's unstable
API — which the project tames by **pinning the jj version** and **differential-testing against the
pinned `jj` CLI binary**.

## 2. What it does

The public surface (`python/pyjutsu/`) is organized around a `Workspace` facade (one handle per
working-copy path; the repo behind it is shared):

- **Reads** (`RepoView`): `working_copy`, `resolve` (single-revision revset), `log`, `iter_log`
  (lazy/streaming), `bookmarks`, `operations` (the op log), `conflicts`, `diff_stat` (per-file +
  total line counts), and `diff` (name-status + content hunks). All return **frozen Pydantic
  models**, never mutate the repo, and never snapshot the working copy.
- **Time travel:** `at_operation(op)` returns a historical read view; `undo` / `restore_operation`
  publish *new* operations that reverse / reset to past state.
- **Mutations** (`Transaction`, used as a `with`-block): `describe`, `new`, `edit`, `abandon`,
  `rebase` (modes `source`/`revision`/`branch` = jj's `-s`/`-r`/`-b`), `squash`, `restore`, plus
  bookmark CRUD (`create`/`set`/`delete`) and `track`/`untrack`. A clean block exit publishes
  **exactly one** jj operation; any exception rolls the whole transaction back.
- **Git interop** (on the `Workspace`): `git_fetch` / `git_push` / `git_import` / `git_export` /
  `git_clone`, remote CRUD (`remotes`/`add_remote`/`remove_remote`/`rename_remote`/`set_remote_url`),
  `snapshot`, and stale-working-copy handling (`is_stale`/`update_stale`).
- **0.7.0 additions:**
  - a typed, composable **`Revset` builder** that *renders to* jj revset strings (it evaluates
    nothing — it is sugar over the existing string path, with escaping that mirrors jj's own
    `escape_string`), accepted anywhere a revset string is, with `R.raw(...)` as the escape hatch;
  - a **streaming `iter_log`** (eager id evaluation, then one `CommitData` built per `__next__`);
  - **`run_jj`**, a deliberately-labeled escape hatch that runs the *external* `jj` binary and
    returns raw stdout/stderr/exit (parsing nothing into models).

## 3. How it does it — architecture

The single most important design decision (concept §4) is a **strict three-layer split**:

```
┌─ pyjutsu (pure Python, public)  — Pydantic models, ergonomic facade, docs, typing
│      Workspace / RepoView / Transaction / Revset facades
│      converts native plain data → Pydantic models; owns ALL ergonomics + validation
├─ _pyjutsu (Rust, PyO3 native ext)  — THIN
│      opaque handles: PyWorkspace, PyRepoView, PyCommitStream, PyTransaction
│      returns plain data (dicts / lists / primitives); NO jj-lib type crosses the FFI; no logic
├─ jj-lib (Rust crate, pinned =0.38.0)  — the engine
└──────────────────────────────────────────────────────────────────────────────────────────
```

Mechanics that make this work, and that the code gets right:

- **Off-GIL data computation.** Each read evaluates jj-lib into plain Rust `*Data` structs
  (`src/convert.rs`) inside `Python::allow_threads`, then converts to `PyDict`s after re-acquiring
  the GIL. The Python layer validates those dicts into frozen models with `extra="forbid"` — which
  doubles as a **drift tripwire**: if jj-lib changes a field shape, an unexpected key fails loudly
  rather than silently corrupting data. `tests/golden/model_fields.json` pins the model shape.
- **The `!Send` transaction problem.** `jj_lib::Transaction` owns a `MutableRepo`, which holds a
  `Box<dyn MutableIndex>`, and `MutableIndex: Any` carries **no** `Send` bound (verified in jj-lib
  0.38). So the transaction is pinned to the thread that started it and cannot cross
  `allow_threads`. The code isolates this in an `unsendable` `PyTransaction`
  (`src/transaction.rs:46`) while keeping `PyWorkspace` `Send` (behind a `Mutex<Workspace>`), with a
  separate `Arc<AtomicBool>` single-transaction guard (`tx_open`). In-transaction graph work runs
  *on* the GIL (it's light); the genuinely heavy I/O (snapshot, checkout, git network) is structured
  so the `Send` parts release the GIL around the `!Send` parts.
- **Commit-id fidelity with the pinned CLI.** Because the binding authors real commits, it must
  produce **byte-identical** commit ids to the CLI for differential tests to pass. Two pieces of
  CLI-only policy are deliberately reproduced in Rust: config stacking (`load_user_settings`,
  `src/workspace.rs:104` — built-in defaults → `JJ_CONFIG`/platform dir → repo `config.toml`) and
  the trailing-newline description convention (`_complete_newline`, `transaction.py:24`).
- **The `rebase_descendants()` landmine.** `Transaction::commit` asserts `!has_rewrites()` and will
  **panic (abort the process)** if a rewrite is left pending. Every mutation that records a rewrite
  calls `rebase_descendants()` before reading results back, and `commit` re-runs it idempotently as
  a final safety net. This is called out repeatedly in the comments as "landmine #1."
- **Error taxonomy** (`src/errors.rs`) lives in Rust so the native layer raises the precise subclass
  (`RevsetError`, `ConflictError`, `BackendError ⊃ GitError`, `WorkspaceError`,
  `WorkingCopyError ⊃ StaleWorkingCopyError`, `ImmutableCommitError`). Most map by `Display` only
  (no jj-lib type crosses the FFI); `map_edit_err` variant-matches so rewriting the root surfaces as
  `ImmutableCommitError` rather than a generic backend error. `JjCliError` is the one pure-Python
  exception, raised only by `run_jj`.

## 4. Who it's for

Python tool authors who drive `jj` programmatically and have hit the CLI ceiling. The concept names
gitman as one *consumer* but is explicit (§3) that Pyjutsu must stand alone and be useful to any
Python tool. It requires Python ≥3.13, a Rust toolchain to build, and (for development) a devenv
shell that pins the toolchain, maturin, and the matching `jj` 0.38.0 CLI. It is a **power-user,
fidelity-first** library — not a beginner-friendly convenience wrapper.

---

## 5. Overall assessment

This is **unusually high-quality code.** The layering discipline is real and consistently enforced —
no jj-lib type leaks across the FFI, and all ergonomics genuinely live in Python. The comments are
exceptional: they explain *why* (the `!Send`/`!Sync` constraints, jj-lib API surprises where the
implementation guide was wrong, scope decisions, the rebase landmine) rather than restating *what*.
Scope boundaries are documented honestly throughout ("flagged, not faked"). The differential testing
strategy is exactly right for binding an intentionally-unstable engine, and it is executed
thoroughly.

I found **one concrete latent bug** (H1), a handful of smaller correctness/consistency nits, and
several design caveats worth recording. None of them undermine the architecture; H1 is the only one
that warrants a fix before the next tag.

---

## 6. Findings

Severity legend: 🔴 High (fix before next release) · 🟠 Medium · 🟡 Low · 🟢 Nit.

### 🔴 H1 — Transaction slot leak on commit failure permanently wedges the workspace

**Location:** `src/transaction.rs:598` (`PyTransaction::commit`), `:629` (`Drop`),
`python/pyjutsu/transaction.py:92` (`__exit__`).

```rust
// src/transaction.rs
fn commit(&self, py: Python<'_>, description: String) -> PyResult<String> {
    let mut tx = self.take()?;                                       // (0) tx Option -> None
    tx.repo_mut().rebase_descendants().map_err(map_backend_err)?;    // (a) fallible
    let new_repo = tx.commit(description).map_err(map_backend_err)?; // (b) fallible
    self.release_slot();                                             // (c) ONLY reached on success
    // ... post-commit checkout ...
}
```

`take()` (`:91`) consumes the native transaction — it `Option::take()`s the `RefCell`, leaving it
`None`. If either fallible call **(a)** or **(b)** returns `Err`, the function exits **before**
`release_slot()` at (c). The `Drop` guard cannot compensate:

```rust
// src/transaction.rs:629
impl Drop for PyTransaction {
    fn drop(&mut self) {
        if self.tx.get_mut().is_some() {   // tx is already None  -> false
            self.release_slot();           // never runs
        }
    }
}
```

Once this happens, the workspace's `tx_open: Arc<AtomicBool>` stays `true` forever, so
`begin_transaction` (`src/workspace.rs:1326`) rejects **every** subsequent transaction with
`"a transaction is already open on this workspace"`. There is no recovery short of dropping and
reloading the `Workspace` handle.

**Why it is reachable.** (b) `Transaction::commit` performs the op-store write — a disk/IO operation
that can genuinely fail (full disk, permission change, a concurrent op-store writer). (a)
`rebase_descendants` is *usually* an idempotent no-op at this point (each mutation already ran it),
but it is still typed fallible and can error on a corrupt/unexpected graph state. Either is enough.

**Python side reinforces the trap.** In `transaction.py:__exit__`:

```python
native, self._native = self._native, None     # self._native now None
if exc_type is not None:
    native.rollback(); ...
self._operation_id = native.commit(self._description)   # raises here
self._state = "committed"                                # never reached
```

When `native.commit()` raises, `self._state` stays `"open"` and `self._native` is already `None`.
The only surviving reference to the `PyTransaction` is the `__exit__` local `native`, which is
dropped when the frame unwinds → `Drop` runs → sees `None` → no release. The slot is gone.

**Recommended fix:** release the slot whenever the native transaction is *consumed*, regardless of
the subsequent outcome. The cleanest is to release immediately after a successful `take()` (the
transaction object is already gone at that point, so the "one open tx" invariant is satisfied), or
use an RAII guard that releases on every return path. See `REVIEW_REFACTORING_IDEAS.md` §H1 for
concrete options. A regression test should force `commit` to fail and then assert that a *new*
transaction can still be opened.

---

### 🟠 M1 — A committed-but-checkout-failed transaction reports as *not* committed

**Location:** `src/transaction.rs:606-616` (the post-commit checkout in `commit`).

After `tx.commit()` succeeds, the operation is **already published to the op log**. The function then
checks whether `@` moved and, if so, checks out the new `@` on disk via `checkout_wc`:

```rust
self.release_slot();                          // slot correctly freed here (so NOT an H1 leak)
let new_wc_commit = new_repo.view().get_wc_commit_id(&self.workspace_name).cloned();
if new_wc_commit != self.starting_wc_commit && let Some(new_id) = new_wc_commit {
    let new_commit = new_repo.store().get_commit(&new_id).map_err(map_backend_err)?;
    let op_id = new_repo.operation().id().clone();
    self.workspace.bind(py).borrow().checkout_wc(py, op_id, &new_commit)?;  // can fail
}
Ok(new_repo.operation().id().hex())
```

If `checkout_wc` fails (working-copy lock contention, a concurrent on-disk checkout, IO error), the
`PyResult` error propagates. Python's `__exit__` then never sets `self._state = "committed"` or
`self._operation_id` — so the **caller sees an exception from a transaction that actually
committed** (the operation is in the log; only the on-disk working-copy update failed).

This mirrors jj's own reality (a CLI command can commit and then leave the working copy stale, which
`jj workspace update-stale` reconciles), so it is *defensible* — but it is surprising and currently
undocumented. The slot is correctly released here (release happens before the checkout), so this is a
**reporting/semantics** issue, not a leak.

**Recommended:** either (a) raise a distinct, recognizable error on this path (e.g.
`StaleWorkingCopyError` carrying the published op id, signaling "operation landed; run
`update_stale`"), or (b) document that a raised `Transaction.__exit__`/`commit` may still have
published the operation, and that `operation_id` will be `None` even so.

---

### 🟡 L1 — The single-transaction guard doesn't cover the other mutators

**Location:** `tx_open` is only consulted in `begin_transaction` (`src/workspace.rs:1323`).

`undo`, `restore_operation`, `snapshot`, `git_import/export/fetch/push`, and the remote-CRUD verbs
all mutate the repo through their *own* internal transactions without checking `tx_open`. Within a
single thread the GIL serializes everything, so this is invisible. But an open `PyTransaction` does
**not** hold the workspace `Mutex` — `begin_transaction` releases the guard before returning the
handle. Therefore, in an asyncio app:

```python
with ws.transaction("edit") as tx:
    tx.describe("@", "msg")
    await asyncio.to_thread(ws.undo)   # runs concurrently with the open tx
```

`ws.undo()` (on the worker thread) locks the workspace `Mutex`, loads at head, opens its *own*
internal transaction, and commits — all while the outer `PyTransaction` is still live. jj's op-store
tolerates concurrent operations (they become divergent ops that later merge via
`op_walk`/`rebase_descendants`), so this is **not corruption** — but it is a surprising concurrency
model for a library that otherwise serializes workspace-level methods on the `Mutex`.

**Recommended:** either document the concurrency contract explicitly (workspace-level mutators are
*not* mutually excluded with an open transaction), or extend the `tx_open` check to the other
mutators so they refuse while a transaction is open. The latter is a behavior change and should be a
deliberate decision (see ideas doc §L1).

---

### 🟡 L2 — Each `Workspace` read convenience reloads the repo at head

**Location:** `python/pyjutsu/workspace.py` — every read convenience calls `self.head()`
(`:341/:346`), which calls `self._handle.head_view()` → `loader.load_at_head()`
(`src/workspace.rs:318`).

`ws.log(...)`, `ws.diff_stat(...)`, `ws.bookmarks()`, `ws.conflicts(...)`, etc. each construct a
*fresh* `RepoView` and reload the repo at head. A caller doing several reads in a row pays N repo
loads. This is slightly at odds with the project's headline performance thesis ("load repo once,
reuse"; concept §1–2).

It *is* documented — the docstrings and README point callers to `view = ws.head()` for reuse — so
this is a known trade-off favoring API ergonomics (the `ws.foo()` shortcuts always observe the
latest op, like the CLI). Worth either memoizing a head view with explicit invalidation, or making
the cost more prominent in the `Workspace` class docstring.

---

### 🟡 L3 — `evaluate_ids` classifies iteration errors differently from `evaluate`

**Location:** `src/revset.rs:103` vs `:84`.

```rust
// evaluate (eager log path)
commits.push(commit.map_err(map_backend_err)?);   // backend error
// evaluate_ids (streaming iter_log path)
ids.push(id.map_err(map_revset_err)?);            // revset error
```

The streaming `iter_log` path raises `RevsetError` where the eager `log` path raises `BackendError`
for what is effectively the same kind of underlying failure during revset iteration. Cosmetic, but
the two should agree so callers can write one `except` clause regardless of which path they used.
`map_backend_err` is the better fit for an iteration/store error.

---

### 🟢 N1 — `HunkLine.kind` Literal includes `"context"`, which is never emitted

**Location:** `python/pyjutsu/models.py:97` vs `src/diff.rs` (`content_hunks`).

The model allows `kind: Literal["context", "added", "removed"]`, but the Rust diff builder only ever
produces `"added"` / `"removed"` — by design, the binding emits one hunk per changed span with **no
surrounding context** (documented at `diff.rs:141` and on the `Hunk` model). Harmless, but the type
advertises a variant that cannot occur. Either drop `"context"` or add a comment that it is reserved
for a future context-windowed diff mode.

---

### 🟢 N2 — Secondary-workspace settings may diverge from the CLI (commit-id fidelity gap)

**Location:** `load_user_settings`, `src/workspace.rs:126`.

The repo config layer is only loaded when `.jj/repo/config.toml` is a regular file. For a **secondary
workspace**, `.jj/repo` is a pointer file (not a directory), so `repo_config.is_file()` is `false`
and the repo config layer is silently skipped. The comment acknowledges this. The unflagged
consequence: a commit authored *in a secondary workspace* could use different settings (and therefore
a different commit id) than the CLI would produce — which quietly weakens the commit-id parity
guarantee that is one of the project's selling points. Worth an explicit caveat in the docs (and, if
feasible, resolving the pointer file to load the real repo config).

---

### 🟢 N3 — `Commit.bookmarks` is documented "sorted" without an explicit sort at the boundary

**Location:** `python/pyjutsu/models.py:64` ("sorted") vs `src/convert.rs:79`
(`local_bookmarks_for_commit(...).collect()`).

The ordering relies on jj-lib's `local_bookmarks_for_commit` iteration order (backed by an ordered
map, so almost certainly name-sorted in practice). The documented guarantee rests on jj-lib internals
rather than an explicit sort at the FFI boundary. Either sort explicitly in `CommitData::build` /
`BookmarkData`, or soften the docstring to "in jj's bookmark order."

---

### 🟢 N4 — `run_jj` passes the live `os.environ` mapping

**Location:** `python/pyjutsu/workspace.py:443`.

```python
proc = subprocess.run([binary, *argv], cwd=self.root, env=os.environ, ...)
```

`subprocess` copies the mapping, so this is safe. Passing `{**os.environ}` (or `dict(os.environ)`)
makes the snapshot-at-call-time intent explicit and removes any doubt for a reader about mutation
during the call.

---

## 7. Testing assessment

The differential strategy is the right one and is executed well:

- **195 tests**, each with an isolated `JJ_CONFIG` (`conftest.py:jj`) exported into *this* process so
  the in-process binding and the CLI subprocess share one identity + pinned timestamp — without it,
  binding-authored commits would have different ids than the CLI's.
- **Realistic fixtures:** `linear_repo`, `bookmarked_repo` (local + `feature@git` + `feature@origin`),
  `conflict_repo` (a real merge conflict), `diffstat_repo` (precise +/- counts).
- **Golden shape guard** (`test_golden.py`) pins each model's field set, forcing a deliberate update
  on drift.
- **Invariant test**: "reads never mutate the op log" asserts op-log length + head are unchanged
  after running every read.
- **Rust unit tests** cover line-counting edge cases (`diff_stat.rs`) and the version pin (`lib.rs`).

**Gaps worth closing:**

1. **No test exercises the transaction failure path (H1).** A test that forces `commit` to fail and
   then asserts a *subsequent* `ws.transaction(...)` can still open would have caught the slot leak.
   This is the highest-value test to add.
2. **No concurrency test** around L1 (open transaction + concurrent `ws.undo()` via `to_thread`),
   nor around M1 (commit succeeds, checkout fails).
3. **`run_jj` error branches** (binary-not-found, `check=False` non-zero exit, `OSError` on launch)
   are pure Python and need no real `jj` — quick to cover and currently thin.

---

## 8. Bottom line

A genuinely well-engineered binding: disciplined architecture, honest scope, excellent commentary,
and a test strategy matched to the problem. The one issue that should be fixed before the next tag is
**H1** — a recoverable-only-by-reload transaction-slot leak when `commit` fails. **M1** is a
semantics question worth a deliberate decision. Everything else is polish: small correctness/
consistency nits (L3, N1–N4) and design caveats to document or tighten (L1, L2, N2).

See `REVIEW_REFACTORING_IDEAS.md` for concrete fix options for each finding.
