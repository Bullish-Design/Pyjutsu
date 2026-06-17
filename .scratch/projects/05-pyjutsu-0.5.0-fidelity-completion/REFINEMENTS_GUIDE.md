# Pyjutsu 0.5.0 — Fidelity-completion Implementation Guide (3 slices)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec; §5 surface, §12 scope) →
> `.scratch/projects/04-pyjutsu-0.4.0-refinements/REFINEMENTS_GUIDE.md` (the just-completed
> milestone whose flags these continue) → **this document** (the verified plan for 0.5.0) → the code
> it produces. The parent is the 0.4.0 session that landed rebase `-r/-b`, push delete/multi, fetch
> patterns, and the snapshot file-size cap.
>
> **Pins unchanged:** `jj-lib = "=0.38.0"` (default features include `git`); `gix = "=0.78.0"`.
> `JJ_LIB_TARGET` stays `"0.38.0"`. Pyjutsu uses **independent semver**: this milestone bumps
> `0.4.0 → 0.5.0` (see §7). **Start from a clean `main`** (0.4.0 complete @ `v0.4.0`, 165 tests
> green, pushed). Every jj-lib API ref below is `file:line` into
> `~/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/jj-lib-0.38.0/src/`, **verified against the
> pinned source while writing this guide** (2026-06-16). Where a fact still needs runtime
> confirmation against the CLI it is marked **VERIFY**.

---

## 0. What this milestone is, and why these three

0.4.0 shipped the snapshot **file-size** cap but left two snapshot layers flagged, and shipped
multi-bookmark push but left the **bulk-selection** flags. 0.5.0 *completes the working-copy +
bookmark-push fidelity story* with the three remaining clean, jj-lib-backed, differentially-testable
refinements:

| Slice | Item | 0.4.0 flag it closes | jj-lib API status |
|---|---|---|---|
| **1** | `git_push` gains `--all` / `--tracked` bulk selection | slice-2 flag "`--all`/`--tracked` selection out of scope"; `src/workspace.rs` `git_push` | clean — `View::local_remote_bookmarks` (view.rs:269) + `RemoteRef::is_tracked` (op_store.rs:172) |
| **2** | Snapshot honors `snapshot.auto-track` → `start_tracking_matcher` | slice-4 comment "wiring `snapshot.auto-track` … remains flagged"; `src/workspace.rs` `snapshot` | clean — `fileset::parse` (fileset.rs:597) + `FilesetExpression::to_matcher` (fileset.rs:428) |
| **3** | Snapshot `base_ignores` gains the **global git-excludes** layer (`.git/info/exclude` + `core.excludesFile`) | slice-4 comment "only the *global* git-excludes layer … remain[s] flagged"; `src/workspace.rs` `snapshot` | mostly clean — `GitBackend::git_repo_path`/`git_repo` (git_backend.rs:336/341) + `GitIgnoreFile::chain_with_file` (gitignore.rs:108) |

**Sequencing is deliberate (mirrors 0.4.0).** Slice 1 is *additive* and never touches the
snapshot → tree → commit-id path, so the existing differential net validates it unchanged. **Slices
2 and 3 can each move a commit id** (they change which untracked files enter `@`'s tree), so they are
**fenced last** and ship with their own fixtures + explicit tree/commit-id parity assertions. **Do
them in order. Each slice is independently committable and must leave the suite green before the next
begins.**

**Explicitly still out of scope (do NOT implement; keep the flags):** `git_push` force-push beyond
jj-lib's built-in lease negotiation, `--change`/`-r <rev>` push selection; `git_fetch` **tag**
fetching (jj #7528) and `--all-remotes`; interactive/partial squash; `add_workspace -r`/sparse
working copy; revset/fileset *builder* surfaces; diffs/hunks; async. These remain *flagged, not
faked*.

---

## 1. Carried structural facts (true for every slice; re-verify, don't assume)

- **Thin Rust, rich Python.** `_pyjutsu` returns opaque dicts / plain scalars / `None` only. **No
  jj-lib or gix type crosses the FFI.** Models, ergonomics, and policy live in pure-Python `pyjutsu`.
- **`!Send` discipline** (`[[m2-transaction-not-send]]`). `Transaction`/`MutableRepo`/`GitFetch` are
  `!Send`. Anything off-GIL (`py.allow_threads`) must create + drop them **inside one synchronous
  closure on one thread** — the slice-9/10/11 pattern, which the existing `git_push`/`snapshot`
  already follow. **Keep it.**
- **Fresh loader per git verb** (`[[m2-slice10-git-interop]]`). `git_push` re-opens the store via
  `PyWorkspace::fresh_loader` so an in-process remote/bookmark change is visible (git config-snapshot
  staleness). Slice 1 inherits this from the existing `git_push` — keep it.
- **`has_changes()` is the no-op signal** (`[[m2-slice10-git-interop]]`): a git verb that changed
  nothing drops the tx uncommitted and returns `None`. Push authors no commit rewrite, so
  `rebase_descendants` is **not** needed there. Snapshot's no-op signal is the tree-id equality check
  (`new_tree.tree_ids() == wc_commit.tree_ids()`), already in place.
- **Differential oracle = the pinned `jj` 0.38.0 CLI**, via `tests/diff/jj_cli.py::JjCli` against a
  `_copy_repo`/`cp -r` sibling. Binding + CLI share identity + pinned `debug.commit-timestamp` via
  `JJ_CONFIG` (set in `tests/conftest.py::jj`), which is why commit ids match. Assert **commit/tree
  ids** where parity is exact (slices 2, 3) and **ref state** where the CLI wraps extra ops (slice
  1). `JjCli.append_config(toml)` (added in 0.4.0) writes to the shared `JJ_CONFIG` so both sides
  read the same `snapshot.*` settings. **Everything runs through devenv** — never bare
  `cargo`/`maturin`/`pytest`/`jj`.
- **Build/verify per slice:** `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` (task
  names confirmed in `nix/pyjutsu.nix`: `build` = `maturin develop --uv`, `test` = `pytest -q &&
  cargo test`, `lint` = `ruff check python tests && cargo clippy --all-targets -- -D warnings`). The
  venv pytest is at `$DEVENV_STATE/venv/bin/pytest` if you need to filter (`-k`); the task `cd`s into
  `$DEVENV_ROOT`. **No AI attribution** anywhere.

---

## 2. Slice 1 — `git_push` `--all` / `--tracked`

### 2.1 What it is
Today `git_push(remote, bookmarks, *, allow_new, delete)` pushes the **named** bookmarks (0.4.0
slice 2). Add the two bulk modes jj-cli offers:
- **`--all`** (`jj git push --all`): push **every local bookmark** — creating new ones on the remote,
  fast-forwarding existing ones, **and deleting** remote bookmarks whose local bookmark is now absent.
- **`--tracked`** (`jj git push --tracked`): push **every already-tracked bookmark** (those with a
  remote-tracking ref for `remote`), but **not** new/untracked ones.

These are *selection* modes: when set, the `bookmarks` list is ignored (or must be empty).

### 2.2 jj-lib APIs (verified)
| What | Signature / fact | Ref |
|---|---|---|
| Enumerate local⊕remote bookmarks for a remote | `View::local_remote_bookmarks(&RemoteName) -> impl Iterator<Item = (&RefName, LocalAndRemoteRef)>` | view.rs:269 |
| The pair | `LocalAndRemoteRef { local_target: &RefTarget, remote_ref: &RemoteRef }` | refs.rs:198 |
| Remote ref state | `RemoteRef { target: RefTarget, state: RemoteRefState }`; `RemoteRef::is_tracked() -> bool` | op_store.rs:139/172 |
| Local-only iteration | `View::local_bookmarks() -> impl Iterator<Item = (&RefName, &RefTarget)>` | view.rs:140 |
| Push (unchanged) | `git::push_branches(&mut MutableRepo, opts, &RemoteName, &GitBranchPushTargets, &mut cb)` | git.rs:2945 (in use) |
| Update record (unchanged) | `BookmarkPushUpdate { old_target: Option<CommitId>, new_target: Option<CommitId> }` — `new_target: None` = delete | git.rs:2920 (in use) |

**No new jj-lib surface** — this slice composes `branch_updates` from a view scan instead of a name
list. The existing `git_push` already builds `BookmarkPushUpdate`s and gates on `all_ok()`; factor
the per-bookmark "build one update from (local_target, remote_ref)" into a helper and feed it either
the requested names (today) or the view scan (new).

### 2.3 The selection (define precisely, then VERIFY against the CLI)
For each `(name, LocalAndRemoteRef { local_target, remote_ref })` from `local_remote_bookmarks(remote)`:
- `old_target` = `remote_ref.target.as_normal().cloned()` (conflicted remote ⇒ skip with a warning,
  or `GitError` — match the named-push behavior).
- `new_target` = `local_target.as_normal().cloned()` (absent local ⇒ `None` = delete; conflicted
  local ⇒ skip).
- **Skip no-ops:** if `old_target == new_target`, don't emit an update (avoids an empty push / spurious
  op). This is the `has_changes()` story at the per-bookmark level.
- **`--all`:** emit for every bookmark with `old_target != new_target` (creates, updates, *and*
  deletes). New bookmarks are implied (jj's `--all` includes new) — so `--all` does **not** require
  `allow_new`.
- **`--tracked`:** emit only where `remote_ref.is_tracked()` is true — i.e. skip bookmarks that have
  no tracking relationship with this remote (and skip pure-creates, since an untracked-but-absent
  remote ref isn't "tracked").

> **VERIFY (the subtle part — do this before trusting the above):** confirm jj 0.38's exact `--all`
> and `--tracked` selection with `jj git push --help` and a differential test. Open questions to nail
> down: does `--all` push *deletions* for locally-deleted bookmarks? does `--tracked` push *new*
> tracked-but-absent-on-remote bookmarks? Build a fixture exercising create + update + delete and
> compare the bare-remote `show-ref` state on both sides. **If `--all`'s delete semantics or
> `--tracked`'s edge cases are more than a few lines beyond this sketch, ship the clear subset
> (`--all` = create+update of all local bookmarks; `--tracked` = update of tracked ones) and re-flag
> the deletion/new edge cases** — don't fake it. The headline test asserts **ref state**, so a
> correctly-scoped subset still passes its own oracle.

### 2.4 Rust sketch (`src/workspace.rs`, generalize `git_push`)
Add `all` / `tracked` flags; when either is set, build `branch_updates` from the view scan instead
of `bookmarks`.

```rust
#[pyo3(signature = (remote, bookmarks, allow_new=false, delete=false, all=false, tracked=false))]
fn git_push<'py>(&self, py: Python<'py>, remote: &str, bookmarks: Vec<String>,
                 allow_new: bool, delete: bool, all: bool, tracked: bool)
    -> PyResult<Option<Bound<'py, PyDict>>>
{
    if all && tracked { return Err(map_git_err("pass at most one of all/tracked".into())); }
    let bulk = all || tracked;
    if bulk && !bookmarks.is_empty() {
        return Err(map_git_err("bookmarks list must be empty with all/tracked".into()));
    }
    if !bulk && bookmarks.is_empty() {
        return Err(map_git_err("no bookmarks to push".into()));
    }
    // … fresh_loader, settings, off-GIL closure as today …
    // inside the closure, after `let view = repo.view();`:
    let mut branch_updates = Vec::new();
    if bulk {
        for (name, pair) in view.local_remote_bookmarks(remote_name) {
            if tracked && !pair.remote_ref.is_tracked() { continue; }
            let old_target = pair.remote_ref.target.as_normal().cloned();
            let new_target = pair.local_target.as_normal().cloned();
            if old_target == new_target { continue; }            // no-op
            branch_updates.push((name.to_owned(), BookmarkPushUpdate { old_target, new_target }));
        }
    } else {
        // … the existing per-name loop (delete/allow_new gates) …
    }
    if branch_updates.is_empty() { return Ok(None); }            // nothing to push ⇒ no op
    // … GitBranchPushTargets { branch_updates }, push_branches, all_ok() gate, commit …
}
```

**Verify while implementing:**
- `name.to_owned()` yields a `RefNameBuf` (the iterator hands back `&RefName`); match the existing
  `RefNameBuf::from(...)` construction.
- The `view` read borrow must end before `repo.start_transaction()` (NLL handles it — *don't*
  `drop(view)`; that's the clippy "dropping a reference" warning 0.4.0 hit).
- Keep the `all_ok()` rejection → `GitError` path exactly as today.

### 2.5 Python facade (`python/pyjutsu/workspace.py`, modify `git_push`)
```python
def git_push(self, remote: str, bookmark: str | list[str] | None = None, *,
             allow_new: bool = False, delete: bool = False,
             all: bool = False, tracked: bool = False) -> Operation | None:
    names = [] if bookmark is None else ([bookmark] if isinstance(bookmark, str) else list(bookmark))
    row = self._handle.git_push(remote, names, allow_new, delete, all, tracked)
    return Operation.model_validate(row) if row is not None else None
```
> **Back-compat:** keep `remote` first positional and `bookmark` second; widen `bookmark` to allow
> `None` (so `ws.git_push("origin", all=True)` reads naturally). Update the docstring to document
> `all`/`tracked` and that force-push / `--change` stay out of scope. Update `_pyjutsu.pyi`:
> `def git_push(self, remote, bookmarks, allow_new=..., delete=..., all=..., tracked=...) -> dict|None`.

### 2.6 Differential tests (extend `tests/test_git_net.py`)
Reuse `_init_bare`, `_has_ref`, `_op_count`, and the two-origin pattern from
`test_push_bookmark_matches_cli`/`test_push_delete_matches_cli`. Add:
- **`test_push_all_matches_cli`** *(headline)*: bookmark `feat-a` + `feat-b` (and delete one already
  on the remote to exercise deletion if you keep that in scope), `ws.git_push("origin", all=True)`
  vs `jj git push --all`. Assert the bare origin's `refs/heads/*` set matches on both sides; one op
  on the binding.
- **`test_push_tracked_matches_cli`**: one tracked bookmark + one untracked local bookmark; `tracked=True`
  pushes only the tracked one. Assert vs `jj git push --tracked`.
- **`test_push_all_and_tracked_raises`** ⇒ `GitError`; **`test_push_all_with_names_raises`** ⇒ `GitError`.
- Keep every existing named-push test green (they still pass a list and leave `all`/`tracked` false).

---

## 3. Slice 2 — Snapshot honors `snapshot.auto-track`

### 3.1 What it is
`PyWorkspace::snapshot` hardcodes `start_tracking_matcher: &EverythingMatcher` (so *every* new file is
auto-tracked). jj-cli instead reads the `snapshot.auto-track` **fileset** (default `all()`) and uses
its matcher, so users can restrict which new files become tracked on snapshot. Honor it.

### 3.2 Why it's fenced / the risk
This changes **which untracked files enter `@`'s tree**, so it can move a commit id — exactly like
0.4.0 slice 4. The differential net currently passes because the hardcoded `EverythingMatcher`
matches jj-cli's *default* `all()`. Changing it must be fixtured with an `auto-track` setting and
verified tree-id-identical.

### 3.3 jj-lib APIs (verified)
| What | Signature | Ref |
|---|---|---|
| Parse a fileset | `fileset::parse(&mut FilesetDiagnostics, text: &str, &RepoPathUiConverter) -> FilesetParseResult<FilesetExpression>` | fileset.rs:597 |
| Expression → matcher | `FilesetExpression::to_matcher(&self) -> Box<dyn Matcher>` | fileset.rs:428 |
| Setting (string) | read `snapshot.auto-track` via `settings.get_string("snapshot.auto-track")`; jj-cli's default is `"all()"` | settings.rs:247 |
| Path converter (reuse) | `RepoPathUiConverter::Fs { cwd, base }` rooted at the workspace root — exactly the one `src/revset.rs::evaluate` builds | revset.rs:40 |

`fileset::parse` (not `parse_maybe_bare`) is the strict grammar — match jj-cli, which parses
`snapshot.auto-track` as a full fileset. **VERIFY** jj-cli uses `parse` (strict) vs `parse_maybe_bare`
(bare-string fallback) for this setting; if unsure, `all()` (the default) parses identically under
both, and the realistic test patterns (`glob:"src/**"`) are unambiguous.

### 3.4 Rust sketch (`src/workspace.rs`, in `snapshot`)
Read the setting *before* the working-copy lock (like the 0.4.0 `max_new_file_size` read), build the
matcher, and hold it in a local that outlives the `SnapshotOptions` (the field is `&'a dyn Matcher`).

```rust
// before the lock, alongside the max_new_file_size read:
let auto_track = ws.repo_loader().settings()
    .get_string("snapshot.auto-track").unwrap_or_else(|_| "all()".to_owned());
let path_converter = RepoPathUiConverter::Fs {
    cwd: ws.workspace_root().to_path_buf(), base: ws.workspace_root().to_path_buf(),
};
let mut diagnostics = FilesetDiagnostics::new();
let auto_track_matcher = fileset::parse(&mut diagnostics, &auto_track, &path_converter)
    .map_err(map_fileset_err)?            // NEW small mapper → PyjutsuError (or reuse map_revset-style)
    .to_matcher();
// … in SnapshotOptions:
start_tracking_matcher: auto_track_matcher.as_ref(),
```

**Verify while implementing:**
- `auto_track_matcher` (a `Box<dyn Matcher>`) must be a local that lives until after the
  `snapshot(&options)` call; `start_tracking_matcher: auto_track_matcher.as_ref()`.
- Add a tiny error mapper for `FilesetParseError` (mirror `map_revset_err` in `src/errors.rs`); a bad
  `auto-track` fileset ⇒ a clear `PyjutsuError`/`WorkingCopyError`, not a panic.
- `force_tracking_matcher` stays `NothingMatcher` (jj-cli's default; force-tracking ignored files is
  not part of this slice).
- `RepoPathUiConverter`/`FilesetDiagnostics` imports: `jj_lib::repo_path::RepoPathUiConverter`,
  `jj_lib::fileset::{self, FilesetDiagnostics}`.

### 3.5 Differential tests (`tests/test_snapshot.py`)
- **`test_snapshot_auto_track_matches_cli`** *(headline)*: `jj.append_config('[snapshot]\nauto-track =
  "glob:\\"tracked.txt\\""')` (or `"none()"` for the simplest discriminator), create `tracked.txt`
  and `other.txt`, snapshot via binding vs `jj status` on a copy. Assert `@`'s tree/commit id are
  identical on both sides and only the auto-tracked file entered `@`.
- **`test_snapshot_auto_track_default_unchanged`**: with no setting, behavior is unchanged
  (everything tracked) — i.e. the existing snapshot tests still pass.
- **`test_snapshot_bad_auto_track_raises`**: a malformed fileset ⇒ a Pyjutsu error.
- **Re-run the full suite** — this touches the shared snapshot path.

---

## 4. Slice 3 — Snapshot `base_ignores` global git-excludes layer (do LAST)

### 4.1 What it is
0.4.0 verified that jj's snapshotter chains each directory's own `.gitignore` (so repo/nested
`.gitignore` is already honored), and left `base_ignores = empty()`. The one remaining layer jj-cli
composes is the **global git-excludes**: `core.excludesFile` (the user's global gitignore) and the
repo-local `.git/info/exclude`. Chain those into `base_ignores`, matching jj-cli's `base_ignores()`.

### 4.2 Why it's last / the risk
Like slices 2 and 4-of-0.4.0, it changes which untracked files enter `@`'s tree → can move a commit
id. **It is also the only slice that reads *environment* state** (`$HOME`/global git config), so its
parity depends on binding and CLI reading the *same* files — which they do, since both run as the
same user with the same `JJ_CONFIG`/git config. Fixture it and assert tree-id parity, and drive the
exclude file through a repo-local path (`.git/info/exclude`) so the test is deterministic.

### 4.3 jj-lib APIs (verified)
| What | Signature | Ref |
|---|---|---|
| Get the GitBackend | `store.backend_impl::<GitBackend>() -> Option<&GitBackend>` | store.rs:92 |
| Git dir | `GitBackend::git_repo_path(&self) -> &Path` (the `.git` dir / bare repo path) | git_backend.rs:341 |
| gix handle (for `core.excludesFile`) | `GitBackend::git_repo(&self) -> gix::Repository` | git_backend.rs:336 |
| Chain a file | `GitIgnoreFile::chain_with_file(&self, prefix: &str, path: PathBuf) -> Result<Arc<Self>, …>` | gitignore.rs:108 |
| Empty base | `GitIgnoreFile::empty() -> Arc<Self>` | gitignore.rs:53 |

### 4.4 The chain order (match jj-cli, then VERIFY)
git/jj precedence, low→high (later overrides earlier; `!`-rules un-ignore):
1. **global** `core.excludesFile` (gix config; jj-cli falls back to `~/.config/git/ignore` when
   unset) — the user's global gitignore.
2. **repo-local** `.git/info/exclude` (`git_repo_path().join("info/exclude")`).
3. *(per-directory `.gitignore` — already handled by the snapshotter; NOT part of `base_ignores`.)*

```rust
let base_ignores = {
    let mut ig = GitIgnoreFile::empty();
    if let Some(git) = repo.store().backend_impl::<jj_lib::git_backend::GitBackend>() {
        // (1) global core.excludesFile, if configured / present — VERIFY the gix accessor.
        if let Some(path) = global_excludes_file(&git.git_repo()) {            // helper, see below
            if path.exists() { ig = ig.chain_with_file("", path).map_err(map_workingcopy_err)?; }
        }
        // (2) repo-local info/exclude
        let info_exclude = git.git_repo_path().join("info").join("exclude");
        if info_exclude.exists() {
            ig = ig.chain_with_file("", info_exclude).map_err(map_workingcopy_err)?;
        }
    }
    ig
};
```

> **VERIFY (the two load-bearing facts):**
> 1. **jj-cli's exact `base_ignores` order** — grep your memory/jj docs; the precedence above is
>    git's documented one, but confirm jj-cli composes `core.excludesFile` *then* `.git/info/exclude`
>    (and nothing else) in `base_ignores`. **If you can't confirm the global-file order cheaply, ship
>    just `.git/info/exclude`** (deterministic, repo-local, easy to fixture) **and re-flag the global
>    `core.excludesFile` layer** — that is the clean, safe half.
> 2. **The gix accessor for `core.excludesFile`** — `gix::Repository` exposes excludes via its
>    config/`excludes` API; pin the exact call against `gix 0.78` (the registry has it under the same
>    path). If it's more than a few lines, defer it per (1).

### 4.5 Differential tests (`tests/test_snapshot.py`)
- **`test_snapshot_info_exclude_matches_cli`** *(headline, deterministic)*: write
  `.git/info/exclude` with `excluded.txt`, create `excluded.txt` + `kept.txt`, snapshot via binding
  vs `jj status` on a copy. Assert `@`'s tree/commit id identical and `excluded.txt` absent from `@`.
  (`cp -r` copies `.git/info/exclude` into the sibling, so both sides read it.)
- **`test_snapshot_global_excludes_matches_cli`** *(only if the global layer ships)*: point
  `core.excludesFile` at a repo-relative file via the shared git config and assert parity. If the
  global layer is deferred, omit this and leave the flag.
- **Re-run the full suite** — shared snapshot path. If anything regresses, the chain order (§4.4) is
  wrong: per-directory `.gitignore` is the snapshotter's job, **not** `base_ignores`'.

---

## 5. Build / verify / report (every slice)

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test     # pytest -q && cargo test
devenv shell -- devenv tasks run pyjutsu:lint     # ruff check + clippy -D warnings (NOT ruff format)
```
Per slice: build → full suite green → lint clean → commit on `main`. **No AI attribution** anywhere.
Commit messages, one per slice:
`Implement 0.5.0 slice 1: git_push --all/--tracked`,
`… slice 2: snapshot honors snapshot.auto-track`,
`… slice 3: snapshot base_ignores global git-excludes`.

---

## 6. Version bump to 0.5.0 (after slice 3 lands)
- `python/pyjutsu/__init__.py`: `__version__ = "0.5.0"` (leave `JJ_LIB_TARGET = "0.38.0"`).
- `Cargo.toml` + `pyproject.toml`: `version = "0.5.0"`.
- **Rebuild** so `Cargo.lock` / `uv.lock` refresh; **commit the lockfiles**.
- Tag `v0.5.0` (annotated, message `pyjutsu 0.5.0 — fidelity completion (push --all/--tracked,
  auto-track, global excludes)`), matching the `v0.1.0…v0.4.0` convention; push `main` + tag.
- **Update memory:** record what landed + verification surprises (the `--all`/`--tracked` selection
  semantics, whether the global excludesFile layer shipped or stayed flagged, the gix accessor used);
  flip `[[pyjutsu-0-4-0-refinements-plan]]`'s "remaining flagged" lines to "closed in 0.5.0".

---

## 7. Guardrails (carried; non-negotiable)
- **Thin Rust, rich Python.** Only dicts / scalars / `None` cross FFI. No jj-lib or gix type leaks.
- **GIL discipline.** Slice 1 is subprocess+network → **off** the GIL, `!Send` types created+dropped
  in one closure on one thread. Slices 2–3 run inside the existing off-GIL `snapshot` span — keep it;
  the fileset/gitignore *construction* is cheap and can stay on the GIL before the lock (like the
  0.4.0 `max_new_file_size` read).
- **Differential, against the pinned `jj` 0.38.0 CLI only.** Assert **ref state** for slice 1 and
  **tree/commit ids** for slices 2–3; the binding publishes exactly one op per verb.
- **Pins frozen:** `jj-lib =0.38.0`, `gix =0.78.0`, `JJ_LIB_TARGET =0.38.0`. `Cargo.lock` committed.
- **Faithful primitive, simplest form.** Implement exactly these three; keep every other flag
  (force-push, `--change`, tag fetch, `--all-remotes`, interactive/partial squash, sparse/`-r`
  workspace, revset/fileset builders, diffs, async) **flagged, not faked**.

> **Top traps (grepped against the pinned source while writing this guide; re-grep if doubted):**
> (1) **slice 1** — build `branch_updates` from `View::local_remote_bookmarks` (view.rs:269); skip
> `old==new` no-ops; **VERIFY `--all` deletion + `--tracked` edge cases vs the CLI**, ship the clean
> subset if subtle. Don't `drop(view)` (clippy "dropping a reference"). (2) **slice 2** — read
> `snapshot.auto-track` (default `"all()"`), `fileset::parse` (fileset.rs:597) + `.to_matcher()`
> (fileset.rs:428) with the workspace-root `RepoPathUiConverter`; the `Box<dyn Matcher>` must outlive
> `SnapshotOptions`; add a `FilesetParseError` mapper. (3) **slice 3** — `base_ignores` adds ONLY the
> global layer (`core.excludesFile` + `.git/info/exclude`); per-directory `.gitignore` is the
> snapshotter's job (verified 0.4.0); `store.backend_impl::<GitBackend>()` (store.rs:92) →
> `git_repo_path()` (git_backend.rs:341); **this can move a commit id — fence it last, fixture
> `.git/info/exclude`, verify tree-id parity**; ship `.git/info/exclude` and flag the global file if
> the gix accessor is fiddly. See [[m2-slice5-snapshot]], [[m2-slice11-git-net]],
> [[m2-transaction-not-send]], [[pyjutsu-0-4-0-refinements-plan]].
