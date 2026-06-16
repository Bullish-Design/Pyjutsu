# Pyjutsu M2 — Slice 11 kickoff prompt (git network: `git_fetch` / `git_push` / `git_clone` — **M2 COMPLETE → 0.41.0**)

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_SLICE11_GUIDE.md` (**your spine for this slice — verified, detailed**) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_CONTINUATION_GUIDE.md` (the corrected slices 2–11 spine: §1 corrections, §2 conventions, §4 slice plans — slice 11 at §4) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_IMPLEMENTATION_GUIDE.md` (original plan + error taxonomy — but the continuation guide and slice guide win where they disagree) → this prompt (orientation + cadence). **This is an implementation session: the deliverable is shipped, tested code, one vertical slice — and it is the LAST M2 slice, so it also bumps pyjutsu to `0.41.0`.**

---

You are continuing **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic +
Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no subprocess
*backend*, no text parsing. **M0, M1 (read layer, released `0.39.0`), and M2 slices 0–10 are done,
committed, and pushed on `main`** (slice 10 = `35deb88`; working tree clean — start from `main`).
Slices 0–1 shipped as pyjutsu `0.40.0`; slices 2–10 ride under `0.40.0`. **This slice (11) completes
M2, so after it is green you bump pyjutsu to `0.41.0`** (versioned independently of jj; the pins stay
`jj-lib = "=0.38.0"` + `gix = "=0.78.0"`, default features include `git`). **No new direct
dependency** — fetch/push use `jj_lib::git::{GitFetch, push_branches, …}`, which jj 0.38 drives via a
**`git` subprocess** (not in-process gix networking).

**Read `M2_SLICE11_GUIDE.md` end to end first** — it is the verified, detailed plan for this slice
(every `jj_lib::git::*` network API at `file:line` into the pinned 0.38.0 source, cross-checked against
jj-lib's own `tests/test_git.rs`; the subprocess/off-GIL + `!Send`-borrow traps; the no-op `NullGitCallback`;
the `GitError` reuse; the "jj-lib has no clone → compose in Python" decision; the `0.41.0` bump; the
local-bare-remote differential tests). Then skim the code you build directly on in `src/workspace.rs`:
**`git_import`** (slice 10 — the `GitImportOptions` + `import_refs` + `rebase_descendants` + `has_changes`
no-op + `finish_op` shape `git_fetch` mirrors almost exactly), **`fresh_loader`** (slice 10 — the
config-snapshot-staleness fix fetch/push still need so the remote is found), **`add_remote`/`remotes`**
(slice 10 — the off-GIL `!Send`-tx-in-one-closure shape), and **`finish_op`** (the reusable "checkout
moved `@` + return `Operation` dict" tail). Also skim `src/errors.rs` (`GitError`/`map_git_err` — **reuse
them, no new exception**), `python/pyjutsu/workspace.py` (where the facade methods + the `git_clone`
**classmethod** join the slice-10 git verbs), `python/pyjutsu/_pyjutsu.pyi`, and `tests/conftest.py`
(the **`bookmarked_repo`** fixture already builds a colocated repo + a bare `origin` remote + a pushed
`feature` bookmark — your main test bed) + `tests/test_git_interop.py` (the slice-10 differential
pattern: `_copy_repo` sibling, read landed git refs via `git show-ref`).

## What's already done (slices 0–10 — do not redo)

- **Slices 0–8** — identity/tx scaffolding; `describe`/`new`/`edit`/`abandon`; 5 bookmark verbs;
  `snapshot`/auto-snapshot; `is_stale`/`update_stale`; `undo`/`restore_operation`;
  `rebase`/`squash`/`restore`.
- **Slice 9** — `init`/`add_workspace`/`forget_workspace`/`workspaces` + `WorkspaceInfo`.
- **Slice 10** — `git_import`/`git_export` + remotes CRUD (`remotes`/`add_remote`/`remove_remote`/
  `rename_remote`/`set_remote_url`) + `Remote` model + `GitError ⊂ BackendError` + the new `gix` dep.
  Key carried lessons: a `GitBackend` **freezes its gix config snapshot at open time**, so each
  config-touching verb loads through a fresh `RepoLoader` (`PyWorkspace::fresh_loader`); `has_changes()`
  is the no-op signal; `set_remote_url` is `&Store` (no op); `GitImportOptions` is `!Sync` (build it
  inside the `allow_threads` closure).

## This slice: `git_fetch` / `git_push` / `git_clone` (per `M2_SLICE11_GUIDE.md`)

Two **`PyWorkspace`-level** verbs + one **pure-Python `Workspace` classmethod**. **No new model, no new
exception** (reuse `GitError`). The headline facts (verified in source + jj-lib's `tests/test_git.rs`):

1. **`git_fetch(remote, *, bookmarks=None) -> Operation | None`** — `GitFetch::new(mut_repo,
   GitSubprocessOptions::from_settings(settings)?, &GitImportOptions{…plain…})` →
   `fetch(remote, expand_fetch_refspecs(remote, GitFetchRefExpression{bookmark: all/patterns, tag:
   none}), &mut NullGitCallback, None, None)` → **`import_refs()`** → `rebase_descendants()` →
   `has_changes()`? commit one op : `None` → `finish_op` (git.rs:2756/2779/2460/2883). **Drop the
   `GitFetch` (inner scope) before re-borrowing `tx.repo_mut()`.**
2. **`git_push(remote, bookmark, *, allow_new=False) -> Operation | None`** — read the local bookmark
   target (`view.get_local_bookmark`) + remote-tracking target (`view.get_remote_bookmark(name
   .to_remote_symbol(remote))`) as `as_normal()` `CommitId`s, build `GitBranchPushTargets {
   branch_updates: [(name, BookmarkPushUpdate{old_target, new_target})] }`, `git::push_branches(
   mut_repo, opts, remote, &targets, &mut NullGitCallback)` (git.rs:2945) → **raise `GitError` if
   `!stats.all_ok()`** → commit one op (it updates the remote-tracking bookmark) → `finish_op`.
   `allow_new=False` + no remote-tracking ref ⇒ `GitError`. Conflicted bookmark ⇒ `GitError`.
3. **`git_clone(url, path, *, colocate=False, remote="origin") -> Workspace`** — **pure-Python
   composition, NO new Rust**: `Workspace.init(path, colocate)` → `ws.add_remote(remote, url)` →
   `ws.git_fetch(remote)` (+ optional default-branch checkout via a thin `git_default_branch` helper
   wrapping `GitFetch::get_default_branch`, git.rs:2863 — recommended but decideable; leave `@` empty if
   discovery is ambiguous). **jj-lib has NO clone primitive** — the CLI composes it; so do we.
4. **Plumbing:** `NullGitCallback` (the no-op 4-method `GitSubprocessCallback`, mirroring jj-lib's test
   `NullCallback`); facade methods + `git_clone` classmethod in `workspace.py`; stubs in `_pyjutsu.pyi`;
   reuse `GitError`/`map_git_err`/`fresh_loader`/`finish_op`/`GitImportOptions`.

- **Verify, don't assume (slice-5/-7/-9/-10 lesson):** grep the pinned source for every API the guide
  names. This slice's traps (all grepped while writing the guide): (a) fetch/push are **`git`
  subprocesses** (`GitSubprocessOptions`) → run **all of it off the GIL**, `!Send` `GitFetch`/tx created
  and dropped inside one closure on one thread; (b) `fetch()` wants an **`ExpandedFetchRefSpecs`** (build
  via `expand_fetch_refspecs` + `GitFetchRefExpression{all, none}`) and a **4-method callback** — supply
  a no-op; **drop the fetcher before re-borrowing `tx.repo_mut()`**; (c) `fetch()` then **`import_refs()`**
  are two steps; import may **abandon commits** → `rebase_descendants()` before commit; (d) **`fresh_loader`
  still required** (slice-10 config-snapshot staleness) or the remote isn't found; (e) push is
  **`push_branches`** with a `GitBranchPushTargets` from `get_local_bookmark`/`get_remote_bookmark`
  `as_normal()`; gate on `GitPushStats::all_ok()`; honour `allow_new`; (f) **jj-lib has NO clone** —
  compose in Python; (g) if the `bookmarks=[...]` → `StringExpression` constructor is unobvious, support
  only `bookmarks=None` (= `all()`) in v1 and flag specific-bookmark fetch — don't block the slice.
- **No-op detection (guide §3b):** `tx.repo_mut().has_changes()` → `None` (uncommitted tx) when a fetch
  imported nothing / a push moved no ref; keep the differential tolerant (`≤ 1` op + document) if exact.

## Differential tests (`tests/test_git_net.py`) per guide §7

**All local, no real network:** on-disk **bare** remotes (the `bookmarked_repo` fixture already builds a
bare `origin` + pushed `feature`). Oracle: `jj git fetch|push|clone` on a `_copy_repo` sibling
(reuse `tests/test_workspace_mgmt.py::_copy_repo`); read landed refs straight from the bare repo via
`git -C <origin.git> show-ref` (dodge the slice-7 colocated jj-read trap). Cases: `git_push` lands
`refs/heads/feature` in the bare `origin` + remote-tracking row + one op (vs `jj git push --bookmark …
--allow-new`); new-without-`allow_new`/unknown-remote/unknown-bookmark → `GitError`; `git_fetch` picks up
a bookmark pushed to `origin` by a sibling (vs `jj git fetch`), second fetch → `None`; `git_clone` gives
a repo with `origin` configured + the remote's bookmarks fetched + `.jj` (vs `jj git clone`). Add the
`JjCli` verbs you need (`jj git fetch|push|clone`). Assert the **binding** publishes exactly one op per
fetch/push and assert **ref state** on the CLI side (the CLI may wrap extra ops — no strict op parity).
**Re-run the whole suite** — additive `PyWorkspace` methods + one classmethod, no new model/exception, so
every prior test (incl. slice 10's `test_git_interop`) must stay green.

Run `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` green (lint = ruff **check** + clippy
`-D warnings`; the project does **not** enforce `ruff format`), then **do the M2-completion bump**
(guide §9): pyjutsu `0.40.0 → 0.41.0` in `python/pyjutsu/__init__.py` (`__version__`; leave
`JJ_LIB_TARGET = "0.38.0"`) + `Cargo.toml` (+ `pyproject.toml` if it carries a version), rebuild so
`Cargo.lock`/`uv.lock` refresh, and flip any concept-doc milestone status to "M2 implemented". Commit on
`main` (`Implement M2 slice 11: git fetch/push/clone (M2 complete, 0.41.0)`), push, and **report — M2 is
done.** No further M2 slices.

## Non-negotiable constraints (carried)

- jj-lib via PyO3, in-process; pin `=0.38.0`; `gix = "=0.78.0"` (slice 10); **no new dependency this
  slice**; `Cargo.lock` committed; the pinned `jj` 0.38.0 CLI is **only** the differential oracle.
  Everything through devenv — never bare `cargo`/`maturin`/`python`/`pytest`/`jj`.
- Thin Rust, rich Python: `_pyjutsu` returns opaque handles (`PyWorkspace`) + **plain data only**
  (dicts/lists/strings/bools/`None`); **never leak jj-lib OR gix types**; models/ergonomics/policy live
  in pure-Python `pyjutsu` (`git_clone` is a pure-Python composition).
- **GIL discipline:** the subprocess + network git I/O runs **entirely off the GIL** (`allow_threads`);
  the `!Send` `GitFetch`/`MutableRepo`/`Transaction` each verb uses stays inside one synchronous closure
  on one thread. `Workspace` lives behind the handle's `Mutex` (`locked()`), held for each verb's
  load→act sequence.
- **`fresh_loader` per fetch/push** (slice-10 config-snapshot staleness) + **`rebase_descendants()`
  after fetch import** (landmine #1).
- **Faithful primitive, simplest form:** `git_fetch` = `GitFetch::new`+`fetch`(plain options, bookmarks-
  or-all, no tags)+`import_refs`; `git_push` = `push_branches` for one named bookmark (create-or-fast-
  forward, `all_ok()` gate, `allow_new`); `git_clone` = init+add_remote+fetch(+default-branch checkout).
  Tags, multi-bookmark/`--all`, deletes, `-r` selection, force, shallow `depth`, and progress callbacks
  are **flagged, not faked**. Every fallible path → `GitError`; only `Display` crosses FFI. **No AI
  attribution** anywhere.

**Start by reading `M2_SLICE11_GUIDE.md`; skimming `git_import`/`fresh_loader`/`finish_op` in
`src/workspace.rs`, `GitError`/`map_git_err` in `src/errors.rs`, the slice-10 git facade in
`python/pyjutsu/workspace.py`, and the `bookmarked_repo` fixture — then implement `git_fetch`,
`git_push`, the `NullGitCallback`, the optional `git_default_branch` helper, the `git_clone` classmethod,
the facade methods + stubs, and the local-bare-remote differential tests; do the `0.41.0` M2-completion
bump; get the whole suite green; commit + push on `main`; and report that M2 is complete.**
