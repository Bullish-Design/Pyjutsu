# Pyjutsu M2 — Slice 9 kickoff prompt (workspace management: `init` / `add_workspace` / `forget_workspace` / `workspaces`)

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_SLICE9_GUIDE.md` (**your spine for this slice — verified, detailed**) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_CONTINUATION_GUIDE.md` (the corrected slices 2–11 spine: §1 corrections, §2 conventions, §4 slice plans — slice 9 at §4) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_IMPLEMENTATION_GUIDE.md` (original plan + error taxonomy — but the continuation guide and slice guide win where they disagree) → this prompt (orientation + cadence). **This is an implementation session: the deliverable is shipped, tested code, one vertical slice.**

---

You are continuing **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic +
Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no subprocess
backend, no text parsing. **M0, M1 (read layer, released `0.39.0`), and M2 slices 0–8 are done,
committed, and pushed on `main`** (slice 8 = `f9e6b3f`; working tree clean — start from `main`).
Slices 0–1 shipped as pyjutsu `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2 bumps
to **`0.41.0`** (versioned independently of jj; pin stays `jj-lib = "=0.38.0"`, default features
include `git`).

**Read `M2_SLICE9_GUIDE.md` end to end first** — it is the verified, detailed plan for this slice
(APIs at `file:line` into the pinned 0.38.0 source, cross-checked against jj-lib's own
`tests/test_workspace.rs`; the four Rust method bodies with their lock/off-GIL/`!Send` traps; the new
model + convert struct; the differential tests). Then skim the code you build directly on in
`src/workspace.rs`: **`load`** (`:187`, the `PyWorkspace` constructor shape `init` mirrors — `Mutex` +
`user_email` + `tx_open`), **`load_user_settings`** (`:43`, reused by `init`), **`snapshot`**/**`undo`**
(`:261`/`:453`, the `locked()` + `load_at_head` + off-GIL + `start_transaction` pattern), and
**`checkout_locked`** (`:126`). Also skim `src/convert.rs` (`BookmarkData` — the model your new
`WorkspaceInfoData` mirrors), `src/errors.rs` (`map_workspace_err`, `map_edit_err`),
`python/pyjutsu/workspace.py` (the facade where `init`/`add_workspace`/… go) + `python/pyjutsu/models.py`
(where `WorkspaceInfo` joins `Commit`/`Bookmark`) + `tests/conftest.py` + `tests/test_new.py` (the
**structural** differential pattern — a fresh workspace `@` has a random change id, so assert shape,
not commit-id parity).

## What's already done (slices 0–8 — do not redo)

- **Slice 0/1** — identity + tx scaffolding; `tx.describe` with commit-id parity.
- **Slice 2** — `tx.new` + the reusable post-commit on-disk checkout in `PyTransaction::commit`.
- **Slice 3** — `tx.edit` / `tx.abandon` (`map_edit_err`; root-abandon panic guard).
- **Slice 4** — five bookmark verbs.
- **Slice 5** — `Workspace.snapshot()` + auto-snapshot on `Transaction.__enter__`.
- **Slice 6** — `Workspace.is_stale()` / `update_stale()`.
- **Slice 7** — `Workspace.undo()` / `Workspace.restore_operation()` (op-log writes); `checkout_locked`.
- **Slice 8** — `tx.rebase` / `tx.squash` / `tx.restore` (commit-rewrite mutations; path-scoped
  restore landed; squash builder holds `&mut repo` → `write()` before `rebase_descendants()`).

## This slice: `init` / `add_workspace` / `forget_workspace` / `workspaces` (per `M2_SLICE9_GUIDE.md`)

Four **`PyWorkspace`-level** verbs (not `Transaction` methods) + one new model `WorkspaceInfo`
`{name, path, wc_commit_id}` (concept §124). The headline insight (verified in source + jj-lib's tests):
**`Workspace::init_workspace_with_existing_repo` (workspace.rs:358) creates the secondary workspace,
sets its `@` to a fresh empty commit on `root()`, *and publishes its own `add workspace '<name>'`
operation*** (via the private `init_working_copy`, workspace.rs:134‑147) — so `add_workspace` calls one
constructor off the GIL and reads the new `@` back; it does **not** hand-roll a transaction or a checkout.

1. **`Workspace.init(path, *, colocate=False) -> Workspace`** *(staticmethod)* —
   `init_internal_git` (workspace.rs:205) / `init_colocated_git` (:221), both needing `&UserSettings`
   from the existing `load_user_settings(&path)`. Wrap the returned `Workspace` in a fresh
   `PyWorkspace` (same shape as `load`). Matches `jj git init` / `jj git init --colocate`.
2. **`Workspace.add_workspace(path, *, name=None) -> WorkspaceInfo`** —
   `init_workspace_with_existing_repo` off the GIL; read `new_repo.view().get_wc_commit_id(&name)`
   for the row. `name` defaults to `path`'s basename. **Placement caveat:** the primitive puts `@` on
   `root()`; `jj workspace add`'s *default* bases it on the current `@`'s parents (the `-r <revs>`
   refinement is **flagged, not faked**) — so drive the differential CLI with `-r 'root()'` and
   compare **structure** (random change id ⇒ no id parity, like `test_new`).
3. **`Workspace.forget_workspace(name) -> None`** — `start_transaction` → `remove_wc_commit(&name)`
   (repo.rs:1470, returns `EditCommitError` → `map_edit_err`) → `rebase_descendants()` → commit one
   op. Error (`PyjutsuError`) if `name` isn't in `wc_commit_ids`. Matches `jj workspace forget <name>`.
4. **`Workspace.workspaces() -> list[WorkspaceInfo]`** — enumerate `view.wc_commit_ids()` (view.rs:54);
   per name, `SimpleWorkspaceStore::load(repo_path).get_workspace_path(name)` (workspace_store.rs:63)
   for the path. **The `WorkspaceStore` trait has no list-all** — enumerate from the view. Matches
   `jj workspace list`.
5. **Model + plumbing:** `WorkspaceInfo` in `models.py` (+ `__all__`, `__init__.py` re-export, a
   regenerated **golden**); `WorkspaceInfoData` in `convert.rs` (mirror `BookmarkData`); facade
   methods in `workspace.py`; stubs in `_pyjutsu.pyi`.

- **Verify, don't assume (slice-5/-7 lesson):** grep the pinned source for every API the guide names.
  This slice's traps (all grepped while writing the guide): (a) `init_workspace_with_existing_repo`
  **publishes its own op + sets `@` on `root()`** — don't double-add a tx, test CLI with `-r 'root()'`;
  (b) `default_working_copy_factory` is **singular** (the plural `…factories` is for `load`);
  (c) `WorkspaceStore` has **no list-all** — and **confirm `jj_lib::workspace_store::{SimpleWorkspaceStore,
  WorkspaceStore}` are publicly exported** (grep `lib.rs`); if not, ship `WorkspaceInfo.path` as `None`
  for non-current workspaces and flag it; (d) `remove_wc_commit` → `EditCommitError` → reuse
  `map_edit_err`; (e) the `!Send` `Transaction` in `forget`/jj-lib's `init_working_copy` is created
  **and dropped inside one synchronous closure on one thread**, so `allow_threads` is sound — but if a
  PyO3 `Send`-bound compile error appears, run that one cheap closure on the GIL instead.
- **Structural differential, not id parity:** a new workspace `@` has a random change id, so assert
  workspace **names present/absent in `wc_commit_ids`, `@` empty + parent `root()`, one op each** (like
  `test_new`), comparing the binding against `jj workspace add … -r 'root()'` / `jj workspace forget`
  on a `_copy_repo` sibling.

## Differential tests (`tests/test_workspace_mgmt.py`) per guide §5

`init` makes a `jj`-loadable repo (internal + colocated; existing-repo → `WorkspaceError`);
`add_workspace` matches `jj workspace add --name … -r 'root()'` structurally (name tracked on both
sides, `@` empty on root, one op each) + default-name-is-basename; `forget_workspace` matches
`jj workspace forget` (name gone, default `@` untouched, one op) + unknown → `PyjutsuError`;
`workspaces()` lists all with right ids. Add a `JjCli.workspaces(repo)` helper (`jj workspace list`;
verify the `-T name` template field in 0.38). **Re-run the whole suite** — additive `PyWorkspace`
methods + one new model, so every prior test (incl. `test_undo`/`test_snapshot`/`test_new`/`test_rewrite`)
must stay green; commit the new golden; confirm.

Run `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` green, then **stop and report at the
slice boundary** before slice 10 (git import/export + remotes: `import_refs`/`export_refs` +
remotes CRUD + a new `Remote` model — **verify `git.rs:529/983/1103/2098/2116/2173/2230/2332` at that
slice**, and decide there whether to add `GitError ⊂ BackendError`). Commit on `main`
(`Implement M2 slice 9: workspace management`).

## Non-negotiable constraints (carried)

- jj-lib via PyO3, in-process; pin `=0.38.0`; `Cargo.lock` committed; the pinned `jj` 0.38.0 CLI is
  **only** the differential oracle. Everything through devenv — never bare `cargo`/`maturin`/
  `python`/`pytest`/`jj`.
- Thin Rust, rich Python: `_pyjutsu` returns opaque handles (`PyWorkspace`) + **plain data only**
  (dicts/lists/strings/bools/`None`); never leak jj-lib types; models/ergonomics/policy live in
  pure-Python `pyjutsu`.
- **GIL discipline:** the `Send` `init_*`/`init_workspace_with_existing_repo` I/O runs **off the GIL**
  (`allow_threads`); the `!Send` `Transaction` `forget` uses stays inside one synchronous closure on
  one thread (or on the GIL if the `Send` bound complains). `Workspace` lives behind the handle's
  `Mutex` (`locked()`), held for each verb's load→act sequence.
- **`rebase_descendants()` after the one rewrite** (`forget`'s discardable-`@` abandon; landmine #1).
- **Faithful primitive, simplest form:** `init`=`init_*_git`; `add_workspace`=`init_workspace_with_existing_repo`
  (empty `@` on root — CLI's `-r` placement + `--sparse-patterns` are flagged, not faked);
  `forget_workspace`=`remove_wc_commit`; `workspaces`=`view.wc_commit_ids()`+`get_workspace_path`.
  `rename_workspace` is available but **out of scope**. Every fallible path maps a jj-lib error to a
  `PyjutsuError` subclass; only its `Display` crosses FFI. **No AI attribution** anywhere.

**Start by reading `M2_SLICE9_GUIDE.md` and skimming `load`/`load_user_settings`/`snapshot`/`undo`/
`checkout_locked` in `src/workspace.rs` and `BookmarkData` in `src/convert.rs`, then implement `init`,
`add_workspace`, `forget_workspace`, `workspaces`, the `WorkspaceInfo` model + `WorkspaceInfoData`
convert struct + golden, the facade methods + stubs, and the differential tests, with the whole suite
green, and stop for review before slice 10.**
