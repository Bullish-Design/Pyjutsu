# Pyjutsu M2 — Slice 4 Implementation Guide (bookmark writes)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `M2_CONTINUATION_GUIDE.md` (the
> corrected spine for slices 2–11; §1 corrections + §2 conventions + §4 slice plans still hold) →
> **this document** (the detailed, verified plan for slice 4 specifically) →
> `M2_IMPLEMENTATION_GUIDE.md` (original plan: build order/model surface/error taxonomy — but the
> continuation guide and this doc win where they disagree) → the code it produces.
>
> **Pin unchanged:** `jj-lib = "=0.38.0"`. Slices 0–3 are committed on `main` (slice 3 =
> `f841bba`); the working tree is clean — start from `main`. Slices 0–1 shipped as pyjutsu
> `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2 bumps to `0.41.0`. API refs
> below are `file:line` into `~/.cargo/registry/.../jj-lib-0.38.0/src/`, verified against the pinned
> source.

---

## 0. Where slice 4 starts

Slices 0–3 landed the commit-rewriting verbs (`describe`, `new`, `edit`, `abandon`) plus the
reusable post-commit on-disk checkout in `PyTransaction::commit`. **Slice 4 is different in kind:
bookmark writes do not rewrite any commit and do not move `@`.** Consequences:

- **No checkout.** `@`'s commit id is unchanged, so `commit`'s `new_wc_commit != starting_wc_commit`
  guard stays false and the on-disk working copy is never touched. Nothing new to wire.
- **No inline `rebase_descendants()`.** No commit is rewritten, so there are no descendants to fix
  up. The centralized `rebase_descendants()` in `commit` runs as a harmless no-op (it always has).
  Do **not** add an inline call in the bookmark methods — there is nothing to rebase, and the
  returned model is read straight from the view.

So slice 4 is a clean application of the mutation template (continuation guide §2.1) minus the
rewrite/rebase machinery: resolve → mutate the view → read the bookmark back → return a plain dict.

`PyTransaction` already carries everything needed (`workspace_name`/`workspace_root`/`user_email`
for `resolve_single`, the `RefCell<Option<Transaction>>`, the single-open guard).

## 1. What to build

Five bookmark verbs on the open transaction. Three operate on **local** bookmarks
(`create`/`set`/`delete`), two on **remote-tracking** state (`track`/`untrack`). Two precondition
guards (already-exists, no-such-bookmark) live in Rust because they must read the open `MutableRepo`.

### 1.1 Verified jj-lib APIs (against the pinned 0.38.0 source)

| What | Signature | Ref |
|---|---|---|
| Read a local bookmark | `MutableRepo::get_local_bookmark(name: &RefName) -> RefTarget` | repo.rs:1672 |
| Set/move/delete a local bookmark | `MutableRepo::set_local_bookmark_target(name: &RefName, target: RefTarget)` | repo.rs:1676 |
| Read a remote bookmark | `MutableRepo::get_remote_bookmark(symbol: RemoteRefSymbol) -> RemoteRef` | repo.rs:1699 |
| Start tracking a remote bookmark | `MutableRepo::track_remote_bookmark(symbol: RemoteRefSymbol) -> IndexResult<()>` | repo.rs:1724 |
| Stop tracking a remote bookmark | `MutableRepo::untrack_remote_bookmark(symbol: RemoteRefSymbol)` | repo.rs:1734 |
| Build a non-conflicted target | `RefTarget::normal(id: CommitId) -> RefTarget` | op_store.rs:82 |
| Build a delete (absent) target | `RefTarget::absent() -> RefTarget` | op_store.rs:64 |
| Is a target absent? | `RefTarget::is_absent(&self) -> bool` | op_store.rs:109 |

**Name/symbol construction (no allocation needed for the borrowed forms):**
- `RefName::new(name: &str) -> &RefName` — const `ref_cast` wrapper (ref_name.rs:157).
- `RemoteName::new(remote: &str) -> &RemoteName` — same macro (ref_name.rs:305).
- `RefName::new(name).to_remote_symbol(RemoteName::new(remote)) -> RemoteRefSymbol<'_>`
  (ref_name.rs:311).

Notes on behavior:
- `set_local_bookmark_target` **adds the target commit as a head** (`for id in target.added_ids()
  { view.add_head(id) }`) before setting — so pointing a bookmark at a non-head commit is fine and
  matches `jj`. Setting `RefTarget::absent()` deletes the bookmark (and removes the head it implied).
- `track_remote_bookmark` merges the remote ref into the local bookmark and flips the remote ref's
  state to `Tracked`; it returns `IndexResult<()>` → map with `map_backend_err`.
- `untrack_remote_bookmark` just flips the remote ref's state to `New`; it returns `()` (no Result).

### 1.2 `tx.create_bookmark(name, commit) -> Bookmark`

Create a **new** local bookmark at `commit` (single-revision revset). Error if a local bookmark of
that name already exists (matches `jj bookmark create`). Returns the new bookmark row.

```rust
let repo = tx.repo_mut();
let target = self.resolve_single(&*repo, revset_str)?;
let ref_name = RefName::new(name);
if !repo.get_local_bookmark(ref_name).is_absent() {
    return Err(PyjutsuError::new_err(format!("bookmark '{name}' already exists")));
}
repo.set_local_bookmark_target(ref_name, RefTarget::normal(target.id().clone()));
let new_target = repo.get_local_bookmark(ref_name);          // read back faithfully
let data = BookmarkData::local(name, &new_target);
data.to_dict(py)
```

### 1.3 `tx.set_bookmark(name, commit) -> Bookmark`

Create-or-move: point `name` at `commit` whether or not it already exists (matches `jj bookmark
set`). Identical to `create_bookmark` **without** the existence guard.

### 1.4 `tx.delete_bookmark(name) -> None`

Delete a local bookmark by setting its target absent (matches `jj bookmark delete`). Guard
no-such-bookmark so a typo doesn't silently no-op:

```rust
let repo = tx.repo_mut();
let ref_name = RefName::new(name);
if repo.get_local_bookmark(ref_name).is_absent() {
    return Err(PyjutsuError::new_err(format!("no such bookmark '{name}'")));
}
repo.set_local_bookmark_target(ref_name, RefTarget::absent());
Ok(())
```

### 1.5 `tx.track_bookmark(name, remote) -> Bookmark` / `tx.untrack_bookmark(name, remote) -> Bookmark`

Start/stop tracking the remote-tracking bookmark `name@remote` (matches `jj bookmark track
name@remote` / `untrack`). Guard that the remote bookmark exists, then flip its state; return the
**remote** bookmark row (`BookmarkData::remote`).

```rust
let repo = tx.repo_mut();
let symbol = RefName::new(name).to_remote_symbol(RemoteName::new(remote));
if repo.get_remote_bookmark(symbol).target.is_absent() {
    return Err(PyjutsuError::new_err(format!("no such remote bookmark '{name}@{remote}'")));
}
repo.track_remote_bookmark(symbol).map_err(map_backend_err)?;   // untrack: no Result, no map
let remote_ref = repo.get_remote_bookmark(symbol);
let data = BookmarkData::remote(name, remote, &remote_ref);
data.to_dict(py)
```

> `untrack_remote_bookmark` returns `()`, so its body is the same minus the `.map_err(...)?`.

### 1.6 Error taxonomy decision (precondition guards)

The "already exists" / "no such bookmark" guards have **no precise fit** in the existing taxonomy
(`RevsetError`/`ConflictError`/`BackendError`/`WorkspaceError`/`WorkingCopyError`/
`ImmutableCommitError`). **Default: raise the `PyjutsuError` base** with a clear message (every
binding error derives from it, so callers can still `except PyjutsuError`). This keeps the surface
faithful to `jj` (which rejects these) without inventing a class M2 never scoped. **Do not** invent
a `BookmarkError` unless review asks for it — flag it as the one open decision and proceed with
`PyjutsuError`. (Revset misses still raise `RevsetError` via `resolve_single`, unchanged.)

`map_backend_err` already exists; no new `map_*` helper is needed this slice (`IndexError` →
`BackendError` via `Display` is correct). Extend the `use crate::errors::{...}` in
`transaction.rs` only if `PyjutsuError` isn't already imported (it is).

### 1.7 Reuse `BookmarkData` (no new model)

`BookmarkData::{local, remote, to_dict}` in `src/convert.rs` are already `pub(crate)` and emit
exactly the dict shape the pure-Python `Bookmark` model validates (`name`, `remote`, `target_ids`,
`tracked`). Import it into `transaction.rs` and return its `to_dict`. **No new Pydantic model** —
`Bookmark` (models.py:103) is reused for `create`/`set`/`track`/`untrack` return values; `delete`
returns `None`.

## 2. Python facade (`python/pyjutsu/transaction.py`)

Add five methods next to `edit`/`abandon`, each guarded by `self._require_open()`. None take a
message → **no `_complete_newline`**. Import `Bookmark` from `.models` (next to `Commit`).

```python
def create_bookmark(self, name: str, commit: str) -> Bookmark:
    """Create a new local bookmark ``name`` at ``commit`` → the new :class:`Bookmark`.
    Raises :class:`~pyjutsu.errors.PyjutsuError` if a local bookmark ``name`` already exists."""
    return Bookmark.model_validate(self._require_open().create_bookmark(name, commit))

def set_bookmark(self, name: str, commit: str) -> Bookmark:
    """Point local bookmark ``name`` at ``commit``, creating it if absent → the :class:`Bookmark`."""
    return Bookmark.model_validate(self._require_open().set_bookmark(name, commit))

def delete_bookmark(self, name: str) -> None:
    """Delete local bookmark ``name``. Raises :class:`~pyjutsu.errors.PyjutsuError` if absent."""
    self._require_open().delete_bookmark(name)

def track_bookmark(self, name: str, remote: str) -> Bookmark:
    """Start tracking remote bookmark ``name@remote`` → its :class:`Bookmark` row."""
    return Bookmark.model_validate(self._require_open().track_bookmark(name, remote))

def untrack_bookmark(self, name: str, remote: str) -> Bookmark:
    """Stop tracking remote bookmark ``name@remote`` → its :class:`Bookmark` row."""
    return Bookmark.model_validate(self._require_open().untrack_bookmark(name, remote))
```

Update `python/pyjutsu/_pyjutsu.pyi` with the five `PyTransaction` stubs (plain return types:
`dict[str, object]` for the four that return a row, `None` for `delete_bookmark`).

## 3. Differential tests (`tests/test_bookmarks_write.py`)

Follow the slice-2/3 harness: `shutil.copytree` for a byte-identical sibling, apply via Pyjutsu on
one and the pinned CLI on the other, assert with **`JjCli.bookmarks(repo)`** (the full row set:
`{(name, remote, target_commit_id, tracked)}`) plus the op-log effect. `JjCli.__call__` already
drives every `jj bookmark …` subcommand — no new driver method needed. Targets here are **existing,
unrewritten commits**, so their commit ids are deterministic across the copy and bookmark rows
compare directly.

**create / set / delete** (use `linear_repo` for create/set on plain commits; `bookmarked_repo`
for move/delete of the existing `feature`):
- `test_create_bookmark_matches_cli`: `tx.create_bookmark("feat", <A's change id>)` vs
  `jj bookmark create feat -r <A>`. Assert `jj.bookmarks(py) == jj.bookmarks(cli)`; the returned
  `Bookmark` has `name == "feat"`, `remote is None`, `target_ids == [A's commit id]`; **1 op** each.
- `test_create_existing_raises`: creating a name that already exists (`feature` in
  `bookmarked_repo`) → `PyjutsuError`.
- `test_set_bookmark_creates_and_moves`: `set` a new name (created), then `set` an existing name
  (`feature`) onto a different commit (moved). Both match `jj bookmark set …`.
- `test_delete_bookmark_matches_cli`: `tx.delete_bookmark("feature")` vs `jj bookmark delete
  feature`; the local `feature` row is gone on both sides (the remote-tracking rows behave
  identically to the CLI — compare the full row set). `test_delete_missing_raises`: deleting an
  absent name → `PyjutsuError`.

**track / untrack** (use `bookmarked_repo`: `feature@origin` is tracked after the push):
- `test_untrack_matches_cli`: `tx.untrack_bookmark("feature", "origin")` vs `jj bookmark untrack
  feature@origin`; the `feature@origin` row's `tracked` flips to `False` on both sides; returned
  `Bookmark` has `remote == "origin"`, `tracked is False`.
- `test_track_matches_cli`: first `jj bookmark untrack feature@origin` on **both** copies (setup),
  then `tx.track_bookmark("feature", "origin")` vs `jj bookmark track feature@origin`; `tracked`
  flips back to `True` on both sides.
- `test_track_missing_raises`: tracking a nonexistent `name@remote` → `PyjutsuError`.

**Invariants** (continuation guide §2.3):
- **1 tx == 1 op** on a clean `@` — asserted in the create/delete/untrack cases above.
- **0 ops on rollback** — raise inside the `with` after a `create_bookmark`, assert the op count and
  the bookmark row set are unchanged.
- **No `@` movement / no spurious checkout** — after a bookmark write, `ws.working_copy().commit_id`
  is unchanged and the on-disk files are untouched (bookmark writes never check out). Assert this in
  at least one case to lock in the "no checkout" property of this slice.

## 4. Build / verify / report

Run, in the devenv shell:

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint
```

All green (build + pytest + `cargo test` + clippy `-D warnings` + ruff), then **stop and report at
the slice boundary** before slice 5 (snapshot + auto-snapshot on tx open — the next big piece).
Commit on `main` following the per-slice convention (`Implement M2 slice 4: bookmark writes`); **no
AI attribution** in the commit/PR/code.

## 5. Guardrails (carried)

- Thin Rust, rich Python: return plain dicts/`None`; never leak jj-lib types; no workflow policy in
  Rust. Bookmark writes are faithful primitives — no auto-tracking heuristics, no "main is special".
- Bookmark writes record **no commit rewrite** → no inline `rebase_descendants()`, no checkout. The
  centralized `rebase_descendants()` in `commit` stays a no-op. Do not add machinery this slice
  doesn't need.
- `Transaction`/`MutableRepo` are `!Send` — all of this is in-memory view mutation on the GIL; there
  is no off-GIL work in slice 4 (no I/O-heavy `Send` call to release the GIL around).
- Every fallible jj-lib path maps to a `PyjutsuError` subclass via `map_backend_err`; the
  precondition guards raise `PyjutsuError` (base) — see §1.6. Only a `Display` message crosses FFI.
- Everything through devenv; never bare `cargo`/`maturin`/`pytest`/`jj`. Pin stays `=0.38.0`;
  `Cargo.lock` committed.
