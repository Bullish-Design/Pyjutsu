# Pyjutsu M2 — Slice 5 Implementation Guide (snapshot + auto-snapshot on tx open)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `M2_CONTINUATION_GUIDE.md` (the
> corrected spine for slices 2–11; §1 corrections + §2 conventions + §4 slice plans still hold;
> slice 5 plan at §4) → **this document** (the detailed, verified plan for slice 5 specifically) →
> `M2_IMPLEMENTATION_GUIDE.md` (original plan; the exact §3.5 snapshot sequence + §3.4 API table —
> but the continuation guide and this doc win where they disagree) → the code it produces.
>
> **Pin unchanged:** `jj-lib = "=0.38.0"`. Slices 0–4 are committed and pushed on `main` (slice 4 =
> `9e5ce6d`); the working tree is clean — start from `main`. Slices 0–1 shipped as pyjutsu `0.40.0`;
> slices 2+ ride under `0.40.0` until the **completed** M2 bumps to `0.41.0`. API refs below are
> `file:line` into `~/.cargo/registry/.../jj-lib-0.38.0/src/`, **verified against the pinned source**
> while writing this guide.

---

## 0. Where slice 5 starts

Slices 0–4 landed every **in-memory** mutation (`describe`/`new`/`edit`/`abandon` + the five bookmark
verbs) plus the reusable post-commit on-disk **checkout** (`PyWorkspace::checkout_wc`, workspace.rs:120)
that fires whenever a committed tx moved `@`. Slice 5 is the **mirror image** of checkout and the
first genuinely **off-GIL I/O** piece of M2:

- **Checkout** (done) writes the repo's `@` tree *out* to disk after a mutation.
- **Snapshot** (this slice) reads the working copy's on-disk state *in*, records it as a rewrite of
  `@`, and publishes a **separate `snapshot working copy` operation** (concept §0.1). It is what the
  pinned `jj` CLI does automatically before every command.

Two deliverables, one native primitive:

1. **`Workspace.snapshot() -> Operation | None`** — explicit: snapshot a dirty `@`, returning the new
   `snapshot working copy` operation; **`None`** (and **zero** ops) if `@` was already clean.
2. **Auto-snapshot on tx open** — wire the already-threaded `auto_snapshot` flag: `Transaction.__enter__`
   snapshots a dirty `@` (via the same native primitive) **before** `begin_transaction`, so a dirty `@`
   + a mutation publishes **two** operations (`snapshot working copy`, then the mutation op).

**The headline invariant becomes:** clean `@` + mutation ⇒ **1 op**; dirty `@` + mutation ⇒ **2 ops**
(snapshot then mutation); `ws.snapshot()` on a clean WC ⇒ **None / 0 ops**; the snapshot's `@` commit
id matches the CLI's snapshot byte-for-byte (preserved change id + pinned committer timestamp + the
identical snapshotted tree).

> **GIL note (the new wrinkle).** The heavy work — locking the WC, walking the disk tree, writing the
> new tree objects, saving WC state — is on the **`Send`** `Workspace`/`LockedWorkspace`, so it runs
> **off the GIL** (`py.allow_threads`). But the `Transaction` that records the snapshot rewrite is
> still **`!Send`** ([[m2-transaction-not-send]]), so the *tx span* (start → rewrite → commit) runs
> **on the GIL**, sandwiched between the off-GIL lock+snapshot and the off-GIL `finish`. The whole
> sequence holds the workspace `Mutex` for its duration (one `self.locked()?` guard).

## 1. Verified jj-lib APIs (against the pinned 0.38.0 source)

| What | Signature | Ref |
|---|---|---|
| Begin WC mutation (takes the WC lock) | `Workspace::start_working_copy_mutation(&mut self) -> Result<LockedWorkspace<'_>, WorkingCopyStateError>` | workspace.rs:427 |
| Locked WC handle | `LockedWorkspace::locked_wc(&mut self) -> &mut dyn LockedWorkingCopy` | workspace.rs:471 |
| Finish (save WC state at op, unlock) | `LockedWorkspace::finish(self, operation_id: OperationId) -> Result<(), WorkingCopyStateError>` | workspace.rs:475 |
| Snapshot the WC (async) | `LockedWorkingCopy::snapshot(&mut self, &SnapshotOptions) -> Result<(MergedTree, SnapshotStats), SnapshotError>` | working_copy.rs:118 |
| Old op / old tree at lock time | `LockedWorkingCopy::old_operation_id(&self) -> &OperationId` / `old_tree(&self) -> &MergedTree` | working_copy.rs:~92/96 |
| Freshness check | `WorkingCopyFreshness::check_stale(locked_wc: &dyn LockedWorkingCopy, wc_commit: &Commit, repo: &ReadonlyRepo) -> Result<Self, OpStoreError>` | working_copy.rs:363 |
| Freshness states | `Fresh / Updated(Box<Operation>) / WorkingCopyStale / SiblingOperation` | working_copy.rs:346 |
| Snapshot options struct | `SnapshotOptions<'a> { base_ignores: Arc<GitIgnoreFile>, progress: Option<&SnapshotProgress>, start_tracking_matcher: &dyn Matcher, force_tracking_matcher: &dyn Matcher, max_new_file_size: u64 }` | working_copy.rs:212 |
| Empty ignores | `GitIgnoreFile::empty() -> Arc<GitIgnoreFile>` | gitignore.rs:53 |
| Track-everything / nothing matcher | `EverythingMatcher` / `NothingMatcher` (unit structs) | matchers.rs:120 / 107 |
| Mark the tx as a snapshot op | `Transaction::set_is_snapshot(&mut self, bool)` | transaction.rs:115 |
| Rewrite `@`'s tree | `MutableRepo::rewrite_commit(&commit).set_tree(MergedTreeId).write()` | (used in slices 1–3) |

Notes verified in source:

- `LockedWorkspace<'a>` borrows `&'a mut Workspace` and owns `Box<dyn LockedWorkingCopy>`
  (`LockedWorkingCopy: Any + Send`, working_copy.rs:110). It is **`Send`** → safe to hold across an
  `allow_threads` boundary. `Workspace::check_out` (workspace.rs:437) is literally
  `start_working_copy_mutation → (ConcurrentCheckout guard) → check_out → finish` — slice 2's
  `checkout_wc` already reuses it; **slice 5 hand-rolls the locked sequence** because the snapshot
  tree must exist *before* the recording transaction does (original guide §3.4 spells this out).
- `check_stale` returns `Fresh` when the WC's op == the repo's op (the normal single-process case),
  so the test fixtures hit the `Fresh` branch and never raise.
- `snapshot`/`check_out`/`finish` are `async`; jj-lib drives them with a `block_on`. Match the
  existing pattern in `src/`: `pollster::block_on(future)` (already used for `merge_commit_trees` in
  `transaction.rs`). The `block_on` itself is what you wrap in `py.allow_threads`.
- `SnapshotOptions` has **no `from_settings` constructor in jj-lib 0.38** — the config keys
  (`snapshot.max-new-file-size`, `snapshot.auto-track`) live in jj's **CLI**, not the library. Build
  the struct directly (see §3) and **flag the defaulting as the one verify-against-CLI item** for
  review.

## 2. Rust: `PyWorkspace::snapshot` (`src/workspace.rs`)

A new `#[pymethods]` `snapshot` on `PyWorkspace`, plus a private helper that builds the options.
It returns `Option<Bound<PyDict>>`: the `snapshot working copy` operation row (reuse
`crate::convert::OperationData`), or `None` when the WC was clean.

```rust
/// Snapshot the working copy: record any on-disk changes to `@` as a separate
/// `snapshot working copy` operation (concept §0.1), returning that operation — or `None` if the
/// working copy already matched `@` (no operation published). Mirrors what the pinned `jj` CLI does
/// automatically before each command; this is the explicit form and the auto-snapshot primitive.
///
/// I/O-heavy and **off the GIL** wherever the work is `Send` (lock, disk walk, tree write, finish);
/// only the `!Send` recording `Transaction` runs on the GIL, between those off-GIL spans. The
/// workspace `Mutex` is held for the whole sequence.
fn snapshot<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
    let mut guard = self.locked()?;
    let ws: &mut Workspace = &mut guard;

    // 1. Load the repo at head + the current `@` commit. No `@` ⇒ nothing to snapshot.
    let repo = {
        let loader = ws.repo_loader();
        py.allow_threads(|| loader.load_at_head()).map_err(map_backend_err)?
    };
    let name = ws.workspace_name().to_owned();
    let Some(wc_commit_id) = repo.view().get_wc_commit_id(&name).cloned() else {
        return Ok(None);
    };
    let wc_commit = repo.store().get_commit(&wc_commit_id).map_err(map_backend_err)?;

    // 2. Lock the WC and check freshness (working_copy.rs:363).
    let mut locked_ws = ws.start_working_copy_mutation().map_err(map_workingcopy_err)?;
    match WorkingCopyFreshness::check_stale(locked_ws.locked_wc(), &wc_commit, &repo)
        .map_err(map_backend_err)?
    {
        WorkingCopyFreshness::Fresh => {}
        // Slice 6 adds the full stale surface (`is_stale`/`update_stale`); here we refuse to
        // snapshot a stale/sibling `@` rather than clobber it.
        WorkingCopyFreshness::WorkingCopyStale | WorkingCopyFreshness::SiblingOperation => {
            return Err(StaleWorkingCopyError::new_err(
                "working copy is stale; another operation moved `@`",
            ));
        }
        // The WC moved under us between load-at-head and taking the lock (rare in-process). The
        // recorded WC op is ahead of the repo we loaded; reload at it so the rewrite has the right
        // parent. Deferred-correctness: a single reload covers the realistic case.
        WorkingCopyFreshness::Updated(_op) => {
            return Err(StaleWorkingCopyError::new_err(
                "working copy was updated concurrently; reload and retry",
            ));
        }
    }

    // 3. Snapshot the on-disk tree (off the GIL — `LockedWorkspace` is `Send`).
    let everything = EverythingMatcher;
    let nothing = NothingMatcher;
    let options = SnapshotOptions {
        base_ignores: GitIgnoreFile::empty(),
        progress: None,
        start_tracking_matcher: &everything,
        force_tracking_matcher: &nothing,
        max_new_file_size: 1 << 20, // 1 MiB — the jj CLI's `snapshot.max-new-file-size` default.
    };
    let new_tree_id = py
        .allow_threads(|| pollster::block_on(locked_ws.locked_wc().snapshot(&options)))
        .map_err(map_workingcopy_err)?
        .0
        .id();

    // 4. Clean WC ⇒ tree unchanged ⇒ no operation.
    if new_tree_id == *wc_commit.tree_id() {
        return Ok(None); // `locked_ws` drops here, releasing the WC lock without writing.
    }

    // 5. Record the snapshot as a rewrite of `@` (on the GIL — `Transaction` is `!Send`).
    let mut tx = repo.start_transaction();
    let mrepo = tx.repo_mut();
    let new_wc_commit = mrepo
        .rewrite_commit(&wc_commit)
        .set_tree_id(new_tree_id)
        .write()
        .map_err(map_backend_err)?;
    mrepo.rebase_descendants().map_err(map_backend_err)?; // satisfies commit's !has_rewrites assert
    let _ = new_wc_commit;
    tx.set_is_snapshot(true);
    let new_repo = tx.commit("snapshot working copy").map_err(map_backend_err)?;

    // 6. Save the WC state at the new op (off the GIL). The tree is already on disk — `finish`
    //    records "this WC is at <new op> with <new tree>"; it does **not** check out (no file I/O
    //    beyond saving state), which is exactly why snapshot never moves files.
    let op_id = new_repo.operation().id().clone();
    py.allow_threads(|| locked_ws.finish(op_id)).map_err(map_workingcopy_err)?;

    let data = OperationData::build(new_repo.operation());
    Ok(Some(data.to_dict(py)?))
}
```

**Verify while implementing (don't assume):**
- The tree-id getter on `Commit` — confirm `wc_commit.tree_id()` returns `&MergedTreeId` (else use
  `wc_commit.tree().map_err(..)?.id()`), and that `CommitBuilder` exposes **`set_tree_id(MergedTreeId)`**
  (grep `commit_builder.rs`). The snapshot returns a `MergedTree`; `.id()` is its `MergedTreeId`.
- `start_transaction()` signature on `Arc<ReadonlyRepo>` (same call slices 1–3 reach via the tx, but
  here you call it directly on the loaded repo).
- Imports to add to `workspace.rs`: `jj_lib::working_copy::{SnapshotOptions, WorkingCopyFreshness}`,
  `jj_lib::matchers::{EverythingMatcher, NothingMatcher}`, `jj_lib::gitignore::GitIgnoreFile`,
  `crate::convert::OperationData`, `crate::errors::StaleWorkingCopyError`. `map_workingcopy_err`,
  `map_backend_err` are already imported.

> **The one open review item — `SnapshotOptions` fidelity.** `base_ignores = GitIgnoreFile::empty()`
> and `max_new_file_size = 1 MiB` reproduce the CLI's behavior **for repos without a `.gitignore` and
> without oversized files** — which is every test fixture (jj's own `.jj`/`.git` are excluded
> *internally* by the snapshotter's reserved-path handling, local_working_copy.rs:674+, not via
> `base_ignores`). Full fidelity (chain the user + repo `.gitignore`; read `snapshot.max-new-file-size`
> / `snapshot.auto-track` from settings) is a faithful refinement, not needed for parity on the
> fixtures. **Flag this; don't silently hardcode without calling it out.**

## 3. Python facade + stubs

### `python/pyjutsu/workspace.py` — explicit `snapshot()`

```python
def snapshot(self) -> Operation | None:
    """Snapshot a dirty ``@`` as a separate ``snapshot working copy`` operation → that
    :class:`Operation`, or ``None`` if ``@`` was already clean (no operation published).

    This is what the ``jj`` CLI does automatically before each command; :meth:`transaction` does it
    for you on open when ``auto_snapshot`` is set. Raises
    :class:`~pyjutsu.errors.StaleWorkingCopyError` if ``@`` is stale.
    """
    row = self._handle.snapshot()
    return Operation.model_validate(row) if row is not None else None
```

`Operation` is already imported in `workspace.py`.

### `python/pyjutsu/transaction.py` — wire auto-snapshot into `__enter__`

```python
def __enter__(self) -> Transaction:
    if self._state != "pending":
        raise RuntimeError(f"transaction already {self._state}; create a new one")
    # Auto-snapshot a dirty `@` first, as a *separate preceding* operation (concept §0.1), matching
    # the CLI. A clean `@` snapshots to nothing (no op). Disabled ⇒ the mutation sees `@` as-is.
    if self._auto_snapshot:
        self._handle.snapshot()
    self._native = self._handle.begin_transaction()
    self._state = "open"
    return self
```

Drop the "Reserved for slice 5 / not yet wired" wording in the `__init__` comment and the
`transaction()` docstring in `workspace.py` now that it's live.

### `python/pyjutsu/_pyjutsu.pyi`

Add to `PyWorkspace`:

```python
    def snapshot(self) -> dict[str, object] | None: ...
```

## 4. Differential tests (`tests/test_snapshot.py`)

Reuse the slice-2/3 harness (`_copy_repo`, `JjCli`, `jj` fixture). The pinned CLI snapshots on **any**
command, so to produce the oracle snapshot run a read command (`jj status` or `jj log`) on the copy.
A snapshot rewrites `@` with the new tree but **preserves the change id**, and the committer timestamp
is pinned (`tests/diff/jj_cli.py`), so the snapshotted **`@` commit id is deterministic** across two
byte-identical copies given the **same on-disk edit** — that commit-id equality is the tree-parity
assertion ([[m2-differential-mutation-testing]]).

Helper: a fixture whose `@` is described + has a tracked file, so an on-disk edit makes it dirty. The
`scratch_repo`/`linear_repo` fixtures work — write to an existing tracked file to dirty `@`.

- **`test_snapshot_dirty_creates_op`**: `other = _copy_repo(repo)`; write the **same** new content to
  the same tracked file in `repo` *and* `other`. `op = ws.snapshot()` on `repo`; `jj(other, "status")`
  to force the CLI snapshot. Assert: `op is not None`, `op.is_snapshot is True`,
  `op.description == "snapshot working copy"`; `len(op_log_ids(repo)) == before + 1` on both sides;
  and **`jj.commit_id(repo, "@") == jj.commit_id(other, "@")`** (identical snapshot tree → identical
  `@`). Change id preserved: `jj.change_id(repo, "@")` unchanged from before.
- **`test_snapshot_clean_returns_none`**: clean `@` (no edit) ⇒ `ws.snapshot() is None`; op count
  unchanged.
- **`test_auto_snapshot_two_ops`** (the headline): dirty `@` on `repo` and `other`; through Pyjutsu
  `with ws.transaction("describe") as tx: tx.describe("@", "msg")`; on `other`,
  `jj(other, "describe", "-m", "msg")` (which auto-snapshots first). Assert **+2 ops** on both sides
  (`snapshot working copy` then the mutation), the head op is the mutation (not the snapshot), and
  `jj.commit_id(repo, "@") == jj.commit_id(other, "@")` (the description was applied **on top of** the
  snapshotted tree). The op **below** head is `is_snapshot` on both sides.
- **`test_clean_at_one_op`**: clean `@` + a mutation ⇒ exactly **+1** op (auto-snapshot is a no-op).
  Locks in that wiring auto-snapshot didn't regress the slice-2/3 "1 tx == 1 op" invariant.
- **`test_auto_snapshot_disabled`**: dirty `@`, `ws.transaction("x", auto_snapshot=False)`; a mutation
  inside ⇒ **+1** op only, and the on-disk edit is **not** captured in `@`'s tree (diverges from the
  CLI by design — assert the flag's faithful effect, not CLI parity).
- **`test_snapshot_rollback_safe`** *(optional)*: `ws.snapshot()` itself publishes its op immediately
  (it's not inside a user tx); confirm a subsequent rolled-back mutation tx leaves the snapshot op in
  place (it's a committed, separate operation).

Also re-run the **whole** suite: wiring auto-snapshot touches every `with ws.transaction(...)`. The
existing fixtures' `@` matches disk (the CLI snapshotted last when building them), so auto-snapshot
returns `None` and the existing op-count assertions still hold — **confirm, don't assume.**

## 5. Build / verify / report

In the devenv shell:

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint
```

All green (pytest + `cargo test` + clippy `-D warnings` + ruff), then **stop and report at the slice
boundary** before slice 6 (stale working copy — `is_stale()` / `update_stale()` / the full
`StaleWorkingCopyError` surface, building directly on the `check_stale` call introduced here). Commit
on `main` (`Implement M2 slice 5: snapshot + auto-snapshot`); **no AI attribution** anywhere.

## 6. Guardrails (carried)

- **Thin Rust, rich Python:** `snapshot` returns a plain dict (or `None`); never leak jj-lib types.
  The trailing-newline / model policy stays in Python. No workflow policy in Rust.
- **GIL discipline:** all `Send`, I/O-heavy work (lock, disk walk, tree write, `finish`) goes through
  `py.allow_threads`; the `!Send` recording `Transaction` span stays on the GIL. Hold the workspace
  `Mutex` for the whole sequence. ([[m2-transaction-not-send]], [[m2-slice2-new-checkout]].)
- **A snapshot is a separate operation:** never fold it into the mutation tx. Clean WC ⇒ **no**
  operation (drop the lock, return `None`). `set_is_snapshot(true)` so the op-log flags it like the CLI.
- **Faithful primitive:** snapshot records the disk as-is; no auto-track heuristics, no special files.
  `auto_snapshot=False` is honored literally (the mutation sees the un-snapshotted `@`).
- **Pin stays `=0.38.0`; `Cargo.lock` committed; everything through devenv** — never bare
  `cargo`/`maturin`/`pytest`/`jj`. The pinned `jj` 0.38.0 CLI is the differential oracle only.
