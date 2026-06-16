# Pyjutsu M2 — Slice 3 Implementation Guide (`edit` / `abandon`)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `M2_CONTINUATION_GUIDE.md` (the
> corrected spine for slices 2–11; §1 corrections + §2 conventions + §4 slice plans still hold) →
> **this document** (the detailed, verified plan for slice 3 specifically) → the code it produces.
>
> **Pin unchanged:** `jj-lib = "=0.38.0"`. Slices 0–2 are committed on `main` (slice 2 =
> `4466f74`); the working tree is clean. Slices 0–1 shipped as pyjutsu `0.40.0`; slices 2+ ride
> under `0.40.0` until the **completed** M2 bumps to `0.41.0`. API refs below are `file:line` into
> `~/.cargo/registry/.../jj-lib-0.38.0/src/`, verified against the pinned source.

---

## 0. Where slice 3 starts

Slice 2 landed `tx.new` **and the reusable post-commit on-disk checkout** in `PyTransaction::commit`
(`src/transaction.rs`): whenever a committed transaction moves `@`, `PyWorkspace::checkout_wc`
(`src/workspace.rs`) runs `Workspace::check_out(op_id, Some(&old_tree), &new_commit)` off the GIL,
keeping the working copy in lockstep with the repo head. **Slice 3 reuses this for free** — both
`edit` and (when it moves `@`) `abandon` get the on-disk update with no new plumbing.

`PyTransaction` now carries `Py<PyWorkspace>` + `starting_wc_commit`, and `resolve_single(repo,
revset)` resolves single-revision args against the open `MutableRepo`. Mirror the slice-1/2 mutation
template (continuation guide §2.1).

## 1. What to build

Two mutation verbs on the open transaction, plus one new error mapping and one root guard.

### 1.1 `tx.edit(commit) -> Commit`

Point `@` at an **existing** commit (no new commit is written; contrast `new`). The edited commit's
content is unchanged — you read it back and return it.

**Verified API:** `MutableRepo::edit(name: WorkspaceNameBuf, commit: &Commit) -> Result<(),
EditCommitError>` (repo.rs:1526). Internally it `maybe_abandon_wc_commit`s the *old* `@` if it was
empty/discardable (that registers an abandon → a rewrite), `add_head`s the target, then
`set_wc_commit`. So:

```rust
let repo = tx.repo_mut();
let target = self.resolve_single(&*repo, revset_str)?;
repo.edit(self.workspace_name.clone(), &target)
    .map_err(map_edit_err)?;          // §1.4
repo.rebase_descendants().map_err(map_backend_err)?;   // old `@` may have been abandoned
let target = self.resolve_single(&*repo, revset_str)?;  // re-read post-rebase (id is stable here,
                                                        // but bookmarks/graph around it may move)
let data = CommitData::build(&*repo, &target)?;
data.to_dict(py)
```

`@` moves to `target` ⇒ `commit`'s checkout fires and checks out `target`'s tree on disk. Editing
**root** returns `EditCommitError::RewriteRootCommit` (repo.rs:2036) → map to `ImmutableCommitError`
(no panic for `edit` — see §1.4).

> Note: re-resolving `target` after `rebase_descendants` is belt-and-suspenders so the returned
> `Commit` reflects any bookmark movement; the target's own commit id is unchanged by `edit`.

### 1.2 `tx.abandon(commit) -> None`

Drop a commit; its children are rebased onto its parent(s). Returns nothing (the commit is gone),
matching the continuation guide §2.1 convention for effect-only mutations.

**Verified API:** `MutableRepo::record_abandoned_commit(old_commit: &Commit)` (repo.rs:1005) records
the abandon; `rebase_descendants()` then rebases children onto the abandoned commit's parents and
moves/deletes bookmarks at the old commit. A working copy pointing at the abandoned commit is moved
to a **new** commit on top of the old parents (so abandoning `@` advances `@` to a fresh empty
commit — `commit`'s checkout fires).

```rust
let repo = tx.repo_mut();
let target = self.resolve_single(&*repo, revset_str)?;
if target.id() == repo.store().root_commit_id() {       // §1.3 — guard the panic
    return Err(ImmutableCommitError::new_err("cannot abandon the root commit"));
}
repo.record_abandoned_commit(&target);
repo.rebase_descendants().map_err(map_backend_err)?;
Ok(())                                                   // facade returns None
```

(`record_abandoned_commit_with_parents`, repo.rs:1019, is available if you ever want explicit
re-parenting; slice 3 doesn't need it.)

### 1.3 ⚠️ Abandoning root **panics** — guard it explicitly

`record_abandoned_commit` opens with `assert_ne!(old_commit.id(), root_commit_id())` (repo.rs:1006).
Unlike `edit` (which returns a typed error for root), this is an **assert → panic**. PyO3 catches the
unwind, so it would surface as a generic `PanicException`, not `ImmutableCommitError`. **Check for
root before calling** and raise `ImmutableCommitError` yourself (code above).

> **Faithful-primitives scope:** enforce only the **root** here — that's the hard backend rule. jj's
> configurable `immutable_heads()` set is *CLI workflow policy*, not a jj-lib mutation guard, and
> Pyjutsu deliberately does not replicate workflow policy (concept: faithful, un-opinionated
> primitives; continuation guide guardrails). Do **not** wire an immutable-revset check into Rust.

### 1.4 Error mapping — add `map_edit_err`

`EditCommitError` (repo.rs:2032) has three variants; only `RewriteRootCommit` is an immutable-commit
error, the rest are backend failures. A `Display`-only mapper can't distinguish them, so add a
variant-matching mapper in `src/errors.rs` (import the jj-lib type):

```rust
use jj_lib::repo::EditCommitError;

/// `edit`/`check_out` failures: rewriting the root → ImmutableCommitError; everything else is a
/// backend problem.
pub(crate) fn map_edit_err(err: EditCommitError) -> PyErr {
    match err {
        EditCommitError::RewriteRootCommit(_) => ImmutableCommitError::new_err(err.to_string()),
        _ => BackendError::new_err(err.to_string()),
    }
}
```

`ImmutableCommitError`, `BackendError`, `WorkingCopyError`, and `StaleWorkingCopyError` already exist
in `errors.rs` (registered in slice 0); `map_workingcopy_err` was added in slice 2. In
`transaction.rs`, extend the `use crate::errors::{...}` to bring in `ImmutableCommitError` (for the
root guard) and `map_edit_err`.

## 2. Python facade (`python/pyjutsu/transaction.py`)

Add two methods next to `new`, guarded by `self._require_open()`:

```python
def edit(self, commit: str) -> Commit:
    """Point ``@`` at the existing ``commit`` (single-revision revset) → that :class:`Commit`.
    The on-disk working copy is updated to the edited commit's tree when the transaction commits.
    Editing the root raises :class:`~pyjutsu.errors.ImmutableCommitError`."""
    return Commit.model_validate(self._require_open().edit(commit))

def abandon(self, commit: str) -> None:
    """Abandon ``commit`` (single-revision revset); its children rebase onto its parent(s).
    Abandoning ``@`` advances ``@`` to a fresh empty commit. Abandoning the root raises
    :class:`~pyjutsu.errors.ImmutableCommitError`."""
    self._require_open().abandon(commit)
```

`edit`/`abandon` take no message, so **no `_complete_newline`**. Update `python/pyjutsu/_pyjutsu.pyi`
(`def edit(self, revset_str: str) -> dict[str, object]: ...`, `def abandon(self, revset_str: str)
-> None: ...`). No new Pydantic model — `edit` returns the existing `Commit`.

## 3. Differential tests (`tests/test_edit_abandon.py`)

Follow the slice-2 harness: `shutil.copytree` for a byte-identical sibling, apply via Pyjutsu on one
and the pinned CLI on the other, assert structure + on-disk tree + op-log effect. `JjCli.__call__`
already drives `jj edit`/`jj abandon` (no new driver method needed).

**`edit`:**
- `test_edit_moves_at_and_checks_out` (use `linear_repo`): `tx.edit(<A's change id>)` vs `jj edit
  <A>`. Assert `ws.working_copy().change_id == A`; `@`'s **commit id == A's commit id** (edit doesn't
  rewrite, and A is deterministic across the copy); on-disk tree == A's (a.txt present, b/c.txt
  gone — reuse the slice-2 file-presence checks); **1 op** each side.
- `test_edit_root_raises`: `tx.edit("root()")` → `ImmutableCommitError`.

**`abandon`:**
- `test_abandon_leaf_at` (use `linear_repo`, abandon `@`): vs `jj abandon @`. The old `@` change id
  is gone; new `@` is an **empty** child of the old parent (`C`). Compare the surviving change-id
  graph `::@ ~ @` to the CLI's, and assert `ws.working_copy().is_empty`. (Don't compare the new
  `@`'s commit id — like `new`, it gets a fresh random change id → non-deterministic hash.)
- `test_abandon_middle_rebases_children` (use `linear_repo`, abandon `B`): vs `jj abandon <B>`.
  Assert the change-id graph `::@` minus B matches the CLI's, and the children kept their change ids
  but were rebased (parent of the commit that was B's child now points at A). Commit ids of rebased
  survivors are deterministic (change ids preserved + pinned timestamp), so you *may* assert
  commit-id parity on a named survivor against the CLI.
- `test_abandon_root_raises`: `tx.abandon("root()")` → `ImmutableCommitError` (proves the §1.3 guard,
  i.e. **no panic**).

Plus the standard invariants (continuation guide §2.3): **1 tx == 1 op** on a clean `@`, **0 ops on
rollback** (e.g. raise inside the `with` after an `edit`, assert op count unchanged).

## 4. Build / verify / report

Run, in the devenv shell:

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint
```

All green (build + pytest + `cargo test` + clippy `-D warnings` + ruff), then **stop and report at
the slice boundary** before slice 4 (bookmark writes). Commit on `main` following the per-slice
convention (`Implement M2 slice 3: edit / abandon`); **no AI attribution** in the commit/PR/code.

## 5. Guardrails (carried)

- Thin Rust, rich Python: return plain dicts/`None`; never leak jj-lib types; no workflow policy in
  Rust (root-only immutability — **not** the `immutable_heads` config set).
- `rebase_descendants()` before `commit` is centralized; the inline call after each rewrite keeps the
  returned model faithful. Forgetting it **aborts the process** (transaction.rs landmine #1).
- `Transaction`/`MutableRepo` are `!Send` — in-tx work stays on the GIL; only `check_out` (already
  wired) runs off-GIL on the `Send` `Workspace`.
- Everything through devenv; never bare `cargo`/`maturin`/`pytest`/`jj`.
