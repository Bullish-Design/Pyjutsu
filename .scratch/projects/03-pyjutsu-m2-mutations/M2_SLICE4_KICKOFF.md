# Pyjutsu M2 — Slice 4 kickoff prompt (bookmark writes)

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_SLICE4_GUIDE.md` (**your spine for this slice — verified, detailed**) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_CONTINUATION_GUIDE.md` (the corrected slices 2–11 spine: §1 corrections, §2 conventions, §4 slice plans) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_IMPLEMENTATION_GUIDE.md` (original plan; build order/model surface/error taxonomy — but the continuation guide wins where they disagree) → this prompt (orientation + cadence). **This is an implementation session: the deliverable is shipped, tested code, one vertical slice.**

---

You are continuing **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic +
Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no subprocess
backend, no text parsing. **M0, M1 (read layer, released `0.39.0`), and M2 slices 0–3 are done and
committed on `main`** (slice 3 = `f841bba`; working tree clean — start from `main`). Slices 0–1
shipped as pyjutsu `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2 bumps to
**`0.41.0`** (versioned independently of jj; pin stays `jj-lib = "=0.38.0"`).

**Read `M2_SLICE4_GUIDE.md` end to end first** — it is the verified, detailed plan for this slice
(APIs, the two precondition guards, the error-taxonomy decision, tests). Then skim the slice 2–3
code so you reuse the established patterns: `src/transaction.rs` (the `describe`/`new`/`edit`/
`abandon` mutation template + `commit`'s post-commit checkout), `src/convert.rs` (the `pub(crate)`
`BookmarkData::{local,remote,to_dict}` you'll reuse), `src/repo_view.rs` (how M1 reads bookmarks
off the view), `src/errors.rs` (`map_*` helpers + taxonomy), `python/pyjutsu/transaction.py`,
`python/pyjutsu/models.py` (`Bookmark`), and `tests/test_edit_abandon.py` + `tests/conftest.py`
(esp. the `bookmarked_repo` fixture) + `tests/diff/jj_cli.py` (`JjCli.bookmarks`, the differential
harness).

## What's already done (slices 0–3 — do not redo)

- **Slice 0/1** — identity + tx scaffolding (real stacked `UserSettings`, `Workspace.transaction()`
  ctx mgr, unsendable `PyTransaction`, single-open-tx guard); `tx.describe` with commit-id parity.
- **Slice 2** — `tx.new(parents=None)` **and the reusable post-commit on-disk checkout** in
  `PyTransaction::commit` (`PyWorkspace::checkout_wc` off the GIL whenever a committed tx moves `@`).
- **Slice 3** — `tx.edit(commit)` / `tx.abandon(commit)`: `map_edit_err` (variant-matching:
  `RewriteRootCommit` → `ImmutableCommitError`) and the explicit root-abandon panic guard. Both
  reuse the slice-2 checkout when `@` moves.

## This slice: bookmark writes (per `M2_SLICE4_GUIDE.md`)

**Different in kind from slices 1–3: bookmark writes rewrite no commit and never move `@`** — so
there is **no checkout and no inline `rebase_descendants()`** (the centralized one in `commit` stays
a harmless no-op). Five thin Rust verbs on the open transaction, returning plain dicts via the
reused `BookmarkData`:

1. **`tx.create_bookmark(name, commit) -> Bookmark`** — `set_local_bookmark_target(RefName::new(name),
   RefTarget::normal(id))` (repo.rs:1676), **guarded** by an "already exists" check
   (`get_local_bookmark(name).is_absent()`, repo.rs:1672).
2. **`tx.set_bookmark(name, commit) -> Bookmark`** — same, create-or-move, **no** existence guard.
3. **`tx.delete_bookmark(name) -> None`** — `set_local_bookmark_target(name, RefTarget::absent())`,
   guarded by a "no such bookmark" check.
4. **`tx.track_bookmark(name, remote) -> Bookmark`** — `track_remote_bookmark(symbol)` (repo.rs:1724,
   returns `IndexResult<()>` → `map_backend_err`); guard the remote bookmark exists.
5. **`tx.untrack_bookmark(name, remote) -> Bookmark`** — `untrack_remote_bookmark(symbol)`
   (repo.rs:1734, returns `()`); same existence guard. Build the symbol with
   `RefName::new(name).to_remote_symbol(RemoteName::new(remote))`.

- **Error taxonomy:** the precondition guards (already-exists / no-such) have no precise taxonomy
  fit → raise the **`PyjutsuError` base** with a clear message (§1.6); flag it as the one open
  review decision, don't invent a `BookmarkError`. No new `map_*` helper needed (`map_backend_err`
  covers `track`'s `IndexError`).
- **Reuse, don't add models:** `BookmarkData` (`src/convert.rs`, already `pub(crate)`) emits the
  exact dict the pure-Python `Bookmark` model validates. No new Pydantic model.
- **Facade + stubs:** add the five methods to `python/pyjutsu/transaction.py` (no `_complete_newline`
  — no message arg) and `python/pyjutsu/_pyjutsu.pyi`.
- **Differential tests** (`tests/test_bookmarks_write.py`) vs `jj bookmark create/set/delete/track/
  untrack`, asserting with `JjCli.bookmarks` (full row set parity — targets are unrewritten commits,
  so commit ids are deterministic): create/set/delete on local bookmarks; track/untrack on
  `feature@origin` (untrack the tracked one; for track, untrack on both copies first then re-track);
  the already-exists / no-such / no-such-remote guards raise `PyjutsuError`; **1 op** on a clean
  `@`; **0 ops on rollback**; and at least one assertion that the working copy / on-disk files are
  **unchanged** (locks in the "no checkout this slice" property). `JjCli.__call__` already drives
  every `jj bookmark` subcommand.

Run `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` green, then **stop and report at
the slice boundary** before slice 5 (snapshot + auto-snapshot). Commit on `main` (`Implement M2
slice 4: bookmark writes`).

## Non-negotiable constraints (carried)

- jj-lib via PyO3, in-process; pin `=0.38.0`; `Cargo.lock` committed; the pinned `jj` 0.38.0 CLI is
  **only** the differential oracle. Everything through devenv — never bare `cargo`/`maturin`/
  `python`/`pytest`/`jj`.
- Thin Rust, rich Python: `_pyjutsu` returns opaque handles + **plain data only** (dicts/lists/
  strings/`None`); never leak jj-lib types; models/ergonomics/policy live in pure-Python `pyjutsu`.
  Bookmark writes are faithful primitives — no auto-tracking heuristics, no special-casing names.
- `Transaction`/`MutableRepo` are `!Send`: all of slice 4 is in-memory view mutation on the GIL;
  there is no off-GIL work this slice (no checkout, no snapshot).
- Bookmark writes record **no rewrite** → no inline `rebase_descendants()`, no checkout; don't add
  machinery the slice doesn't need. Every fallible path maps a jj-lib error to a `PyjutsuError`
  subclass; only its `Display` crosses the FFI. No workflow policy. **No AI attribution** anywhere.

**Start by reading `M2_SLICE4_GUIDE.md` and skimming the slice 2–3 code + the `bookmarked_repo`
fixture, then implement the five bookmark verbs (with their precondition guards), with their
differential tests green, and stop for review before slice 5.**
