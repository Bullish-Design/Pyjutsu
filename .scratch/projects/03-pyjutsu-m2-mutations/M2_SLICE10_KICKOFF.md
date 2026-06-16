# Pyjutsu M2 — Slice 10 kickoff prompt (git interop: `git_import` / `git_export` + remotes CRUD + `remotes()`)

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_SLICE10_GUIDE.md` (**your spine for this slice — verified, detailed**) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_CONTINUATION_GUIDE.md` (the corrected slices 2–11 spine: §1 corrections, §2 conventions, §4 slice plans — slice 10 at §4) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_IMPLEMENTATION_GUIDE.md` (original plan + error taxonomy — but the continuation guide and slice guide win where they disagree) → this prompt (orientation + cadence). **This is an implementation session: the deliverable is shipped, tested code, one vertical slice.**

---

You are continuing **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic +
Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no subprocess
backend, no text parsing. **M0, M1 (read layer, released `0.39.0`), and M2 slices 0–9 are done,
committed, and pushed on `main`** (slice 9 = `739d17b`; working tree clean — start from `main`).
Slices 0–1 shipped as pyjutsu `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2 bumps
to **`0.41.0`** (versioned independently of jj; pin stays `jj-lib = "=0.38.0"`, default features
include `git`). **This slice adds the first new direct Rust dependency since M1 — `gix = "=0.78.0"`,
matching jj-lib's own lock — because `jj_lib::git::add_remote` takes `gix` types and there is no public
jj-lib remote-URL reader (see guide §1).**

**Read `M2_SLICE10_GUIDE.md` end to end first** — it is the verified, detailed plan for this slice
(every `jj_lib::git::*` API at `file:line` into the pinned 0.38.0 source, cross-checked against
jj-lib's own `tests/test_git.rs`; the new `gix` dependency rationale; the six method bodies with their
lock/off-GIL/`!Send`-tx traps; the `GitError ⊂ BackendError` decision; the new `Remote` model +
`RemoteData` convert struct; the differential tests). Then skim the code you build directly on in
`src/workspace.rs`: **`forget_workspace`** (slice 9 — the `!Send`-tx-in-one-off-GIL-closure +
`load_at_head` shape these git mutations mirror), **`finish_op`** (`:162`, the reusable "checkout new
`@` if moved + return the `Operation` dict" tail `git_import`/`git_export` reuse), **`undo`** (`:453`,
the load-head → tx → `finish_op` op-log-write pattern), and **`workspaces`** (slice 9 — the off-GIL
`view`-enumeration + `to_dict` shape `remotes()` mirrors). Also skim `src/convert.rs`
(`WorkspaceInfoData` — the slice-9 model your new `RemoteData` mirrors), `src/errors.rs`
(`map_workspace_err`/`map_edit_err`/`create_exception!` — you add `GitError` + `map_git_err`),
`python/pyjutsu/models.py` (where `Remote` joins `WorkspaceInfo`/`Commit`), `Cargo.toml`
(`[dependencies]` — where `gix` goes), `tests/conftest.py` (the **`bookmarked_repo`** fixture already
builds a colocated repo + a bare `origin` remote + a pushed `feature` bookmark — your main test bed) +
`tests/test_workspace_mgmt.py` (the slice-9 **structural** differential pattern).

## What's already done (slices 0–9 — do not redo)

- **Slice 0/1** — identity + tx scaffolding; `tx.describe` with commit-id parity.
- **Slice 2** — `tx.new` + the reusable post-commit on-disk checkout in `PyTransaction::commit`.
- **Slice 3** — `tx.edit` / `tx.abandon`. **Slice 4** — five bookmark verbs.
- **Slice 5** — `snapshot()` + auto-snapshot. **Slice 6** — `is_stale()` / `update_stale()`.
- **Slice 7** — `undo()` / `restore_operation()` (op-log writes); `checkout_locked`/`finish_op`.
- **Slice 8** — `tx.rebase` / `tx.squash` / `tx.restore` (commit-rewrite mutations).
- **Slice 9** — `init` / `add_workspace` / `forget_workspace` / `workspaces` + `WorkspaceInfo` model
  (`!Send` tx runs fine inside one off-GIL closure; `add_workspace` must `create_dir_all(path)` first;
  CLI `jj workspace add` emits 2 ops vs the faithful primitive's 1 — structural diff, not op parity).

## This slice: `git_import` / `git_export` / `remotes` / `add_remote` / `remove_remote` / `rename_remote` / `set_remote_url` (per `M2_SLICE10_GUIDE.md`)

Six **`PyWorkspace`-level** verbs (not `Transaction` methods) + one new model `Remote` `{name, url}`
(concept §134/§140). Network **fetch/push/clone is slice 11 — NOT this slice.** The headline facts
(verified in source + jj-lib's `tests/test_git.rs`):

1. **`git_import() -> Operation | None`** — `git::import_head` + `git::import_refs(mut_repo,
   &GitImportOptions{auto_local_bookmark:false, abandon_unreachable_commits:true,
   remote_auto_track_bookmarks:empty})` (git.rs:983/529/483), then **`rebase_descendants()`** (import
   can abandon commits) → commit one op → `finish_op` (import may move `@`). `None` on no-op.
2. **`git_export() -> Operation | None`** — `git::export_refs(mut_repo)` (git.rs:1103) → commit one op.
   Raise `GitError` if `stats.failed_bookmarks` non-empty. `None` on no-op.
3. **`remotes() -> list[Remote]`** — `git::get_all_remote_names(store)` (git.rs:2098) for names; read
   each fetch URL via **`git::get_git_backend(store)` (git.rs:386, public) →
   `GitBackend::git_repo()` (git_backend.rs:336, public) → `.find_remote(name).url(Direction::Fetch)`**
   (gix) → stringify. No public jj-lib URL reader exists — this gix path is the only way (guide §1).
4. **`add_remote(name, url)`** — `git::add_remote(mut_repo, name.as_ref(), url, None, Tags::None,
   &StringExpression::all())` (git.rs:2116; exactly jj-lib's own test call) → commit one op.
5. **`remove_remote(name)`** / **`rename_remote(old, new)`** — git.rs:2173/2230 → commit one op each.
6. **`set_remote_url(name, url)`** — `git::set_remote_urls(store, name.as_ref(), Some(url), None)`
   (git.rs:2332). **Takes `&Store`, not `&mut MutableRepo`: pure git-config write → NO transaction, NO
   op.** This asymmetry breaks op-count parity with the other CRUD verbs (guide §3a).
7. **Dep + model + plumbing:** add **`gix = "=0.78.0"` (default-features=false)** to `Cargo.toml`
   matching jj-lib's lock (`cargo tree -i gix` must show one 0.78.0); new **`GitError ⊂ BackendError`**
   + `map_git_err` in `errors.rs` (register + re-export); `Remote` in `models.py` (+ `__init__.py`
   re-export, a regenerated **golden**); `RemoteData` in `convert.rs` (mirror `WorkspaceInfoData`);
   facade methods in `workspace.py`; stubs in `_pyjutsu.pyi`.

- **Verify, don't assume (slice-5/-7/-9 lesson):** grep the pinned source for every API the guide
  names. This slice's traps (all grepped while writing the guide): (a) `add_remote` takes **gix types**
  → the direct `gix` dep is mandatory or it won't compile; (b) **no public remote-URL reader** → use
  the `get_git_backend`/`git_repo`/`find_remote`/`url` gix path, stringified Rust-side (no gix type
  crosses FFI); (c) `set_remote_urls` is **`&Store`** → no tx, no op; (d) `import_refs` may **abandon
  commits** → `rebase_descendants()` before commit, and import/export may move `@` → reuse `finish_op`;
  (e) `GitImportOptions` has **no `Default`** — build it explicitly; (f) the `!Send` `Transaction` in
  each git mutation is created **and dropped inside one synchronous off-GIL closure on one thread** (as
  in slice 9's `forget_workspace`) — sound under `allow_threads`; if a `Send`-bound compile error
  appears, run that one cheap closure on the GIL.
- **No-op detection (guide §3b):** prefer returning `None` (uncommitted tx) when import/export changed
  nothing; if exact detection proves fiddly against `tests/test_git.rs`, keep the differential test
  tolerant (`≤ 1` new op) and document it — don't block the slice.
- **Structural differential, not id parity:** assert ref/bookmark/remote **state present/absent** on
  both sides (binding vs `jj git import|export|remote …` on a `_copy_repo` sibling), op counts per the
  asymmetry above, like `test_workspace_mgmt`. Mind the **slice-7 colocated trap**: git HEAD/refs
  interact with jj reads — read both sides through the same tool where ids must match.

## Differential tests (`tests/test_git_interop.py`) per guide §7

`remotes()` matches `jj git remote list` on `bookmarked_repo` (name + url); `add_remote`/`remove_remote`/
`rename_remote` match `jj git remote add|remove|rename` structurally (state on both sides, one op each;
unknown → `GitError`); `set_remote_url` changes the url but publishes **no op** (the asymmetry); `git_export`
makes a bookmark's git ref appear (vs `jj git export`); `git_import` picks up an out-of-band git change
(vs `jj git import`); a second import/export returns `None`. Add a `JjCli.remotes(repo)` helper
(`jj git remote list`; verify the 0.38 output format). **Re-run the whole suite** — additive
`PyWorkspace` methods + one new model + one new exception, so every prior test (incl. slice 9's
`test_workspace_mgmt`) must stay green; commit the new golden; confirm `cargo tree -i gix` is clean.

Run `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` green (lint = ruff **check** + clippy
`-D warnings`; the project does **not** enforce `ruff format`), then **stop and report at the slice
boundary** before slice 11 (git **fetch / push / clone** — network; subprocess via `gix`; new
auth/network error surface). Commit on `main` (`Implement M2 slice 10: git interop`). After slice 11,
M2 is complete → bump pyjutsu to **`0.41.0`**.

## Non-negotiable constraints (carried)

- jj-lib via PyO3, in-process; pin `=0.38.0`; **add `gix = "=0.78.0"`** (jj-lib's lock); `Cargo.lock`
  committed; the pinned `jj` 0.38.0 CLI is **only** the differential oracle. Everything through devenv
  — never bare `cargo`/`maturin`/`python`/`pytest`/`jj`.
- Thin Rust, rich Python: `_pyjutsu` returns opaque handles (`PyWorkspace`) + **plain data only**
  (dicts/lists/strings/bools/`None`); **never leak jj-lib OR gix types** (the remote URL is stringified
  Rust-side); models/ergonomics/policy live in pure-Python `pyjutsu`.
- **GIL discipline:** the `Send` git I/O runs **off the GIL** (`allow_threads`); the `!Send`
  `Transaction` each git mutation uses stays inside one synchronous closure on one thread (or on the
  GIL if the `Send` bound complains). `Workspace` lives behind the handle's `Mutex` (`locked()`), held
  for each verb's load→act sequence.
- **`rebase_descendants()` after `import_refs`** (it can abandon commits → rewrites; landmine #1).
- **Faithful primitive, simplest form:** `git_import`=`import_head`+`import_refs`(plain options);
  `git_export`=`export_refs`; remotes CRUD = the matching `git::*` calls with the CLI's defaults
  (`push_url=None`, `Tags::None`, `StringExpression::all()`); `set_remote_url`=`set_remote_urls`
  (**no op**). Network verbs + per-remote tracking/`push_url`/tags/`import --remote` are **slice 11 or
  flagged, not faked**. Every fallible path maps a jj-lib/gix error to a `PyjutsuError` subclass
  (`GitError ⊂ BackendError`); only its `Display` crosses FFI. **No AI attribution** anywhere.

**Start by reading `M2_SLICE10_GUIDE.md`; skimming `forget_workspace`/`finish_op`/`undo`/`workspaces`
in `src/workspace.rs`, `WorkspaceInfoData` in `src/convert.rs`, and the `bookmarked_repo` fixture; and
adding the `gix` dep + confirming `cargo tree -i gix` — then implement `git_import`, `git_export`,
`remotes`, `add_remote`, `remove_remote`, `rename_remote`, `set_remote_url`, the `GitError` exception +
`map_git_err`, the `Remote` model + `RemoteData` convert struct + golden, the facade methods + stubs,
and the differential tests, with the whole suite green, and stop for review before slice 11.**
