# Pyjutsu M2 — Slice 11 Implementation Guide (git network: `git_fetch` / `git_push` / `git_clone`)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec; git surface §134) →
> `M2_CONTINUATION_GUIDE.md` (the corrected spine for slices 2–11; §4 "Slice 11") → **this document**
> (the detailed, verified plan for slice 11) → `M2_IMPLEMENTATION_GUIDE.md` (original plan/error
> taxonomy) → the code it produces.
>
> **Pin unchanged:** `jj-lib = "=0.38.0"` (default features include `git`); `gix = "=0.78.0"` (added in
> slice 10). Slice 10 is committed + pushed on `main` (`35deb88`); the working tree is clean — start
> from `main`. **This is the LAST M2 slice: when it lands, M2 (the write layer) is complete and
> pyjutsu bumps `0.40.0 → 0.41.0`** (see §9). Every API ref below is `file:line` into
> `~/.cargo/registry/.../jj-lib-0.38.0/src/`, **verified against the pinned source while writing this
> guide** (cross-checked against jj-lib's own `tests/test_git.rs`).

---

## 0. Where slice 11 starts

Slices 0–10 built the whole in-`Transaction` mutation surface, op-log/stale/snapshot verbs, workspace
lifecycle, and **local** git interop (import/export + remotes CRUD). This slice adds the **network**
git surface (concept §134): syncing jj with *remote* git repos. Like slices 5–10 these are
**`PyWorkspace`-level** verbs (not `Transaction` methods). All three drive a **`git` subprocess** (jj
0.38 fetches/pushes via subprocess, not in-process gix networking — see §1), so all of it runs **off
the GIL**.

Three verbs (one of them pure-Python composition):

1. **`Workspace.git_fetch(remote, *, bookmarks=None) -> Operation | None`** — fetch a remote's
   bookmarks into jj's remote-tracking refs and import them, publishing one op. `None` if nothing
   changed. Matches `jj git fetch`.
2. **`Workspace.git_push(remote, bookmark, *, allow_new=False) -> Operation | None`** — push a local
   bookmark to a remote and update the remote-tracking ref, publishing one op. Raises `GitError` on
   rejection. Matches `jj git push --bookmark <b>`.
3. **`Workspace.git_clone(url, path, *, colocate=False, remote="origin") -> Workspace`** — a
   **pure-Python composition** of `init` + `add_remote` + `git_fetch` (+ optional default-branch
   checkout). **jj-lib has NO clone primitive** (see §4c); the CLI's `jj git clone` is itself
   composed. **No new Rust for clone.**

No new Pydantic model is required (fetch/push return the published `Operation`, or `None`). Network
failures map to **`GitError`** (the slice-10 exception) — see §6.

---

## 1. The shape-defining facts (verify while implementing)

**(a) jj 0.38 fetch/push are `git` *subprocesses*, not in-process gix networking.** `GitFetch`
(git.rs:2747) and `push_branches`/`push_updates` (git.rs:2945/3011) spawn a `git` child process via a
`GitSubprocessContext`, configured by **`GitSubprocessOptions { executable_path, environment }`**
(git.rs:122). Build it with `GitSubprocessOptions::from_settings(settings)` (git.rs:131; reads
`git.executable-path`, default `"git"`, env empty) — the devenv puts `git` on `PATH`, so the default
is correct. **Consequence:** the entire fetch/push (subprocess spawn + network I/O) is `Send`-heavy and
**must run off the GIL** (`allow_threads`); the `!Send` `MutableRepo`/`GitFetch` it borrows are created
**and dropped inside one synchronous closure on one thread** (the slice-9/-10 pattern).

**(b) `GitFetch` borrows `&mut MutableRepo` for its whole lifetime, and `fetch()` wants an
`ExpandedFetchRefSpecs` + a callback.** `GitFetch<'a>` holds `mut_repo: &'a mut MutableRepo` plus a
boxed `gix::Repository` and the import options (git.rs:2747). The fetch call is **not** a plain
`&[refspec]`:

```rust
fetcher.fetch(
    remote_name: &RemoteName,
    refspecs: ExpandedFetchRefSpecs,                 // built by expand_fetch_refspecs(...)
    callback: &mut dyn GitSubprocessCallback,        // progress/sideband sink — supply a no-op
    depth: Option<NonZeroU32>,                       // None = full
    fetch_tags_override: Option<FetchTagsOverride>,  // None = honour remote config
) -> Result<(), GitFetchError>                       // git.rs:2779
```

Build the refspecs exactly as jj-lib's own `fetch_all_with` test helper does (test_git.rs:186):

```rust
let ref_expr = GitFetchRefExpression {
    bookmark: StringExpression::all(),    // or a specific pattern set for `bookmarks=[...]`
    tag: StringExpression::none(),        // jj-lib's default: don't fetch tags (TODO #7528)
};
let refspecs = git::expand_fetch_refspecs(remote, ref_expr)?;   // git.rs:2460
fetcher.fetch(remote, refspecs, &mut callback, None, None)?;
let stats = fetcher.import_refs()?;                              // git.rs:2883 → GitImportStats
```

`fetch()` records what it fetched; **`import_refs()` is a separate step** that actually updates jj's
view (and returns `GitImportStats`). `import_refs` can abandon commits ⇒ **`rebase_descendants()`
before commit** (landmine #1, same as slice 10's `git_import`).

**(c) The callback is a 4-method trait you must implement.** `GitSubprocessCallback` (re-exported at
`jj_lib::git::GitSubprocessCallback`, defined git_subprocess.rs:674) has `needs_progress`,
`progress(&GitProgress)`, `local_sideband(&[u8], Option<GitSidebandLineTerminator>)`,
`remote_sideband(...)`. jj-lib's tests use a `NullCallback` no-op (test_git.rs:102). **Add an identical
no-op `NullGitCallback` in `src/workspace.rs`** (or a small `src/git_net.rs`). `GitProgress` and
`GitSidebandLineTerminator` are public via `jj_lib::git`. (A future slice could route these to a Python
progress callback; out of scope here — keep it silent.)

**(d) Push is `push_branches` with a `GitBranchPushTargets`.** `push_branches(mut_repo,
subprocess_options, remote, &targets, &mut callback) -> GitPushStats` (git.rs:2945) both pushes **and**
updates the remote-tracking bookmark in the view (so it publishes a view-changing op). Build the
target from the local bookmark + its remote-tracking position:

```rust
let targets = GitBranchPushTargets {
    branch_updates: vec![(
        RefNameBuf::from(bookmark),                       // local bookmark name (no refs/heads/)
        BookmarkPushUpdate {
            old_target: <remote-tracking target, or None if new>,   // Option<CommitId>
            new_target: <local bookmark target>,                    // Option<CommitId> (None = delete)
        },
    )],
};
let stats = git::push_branches(tx.repo_mut(), subprocess_options, remote.as_ref(), &targets, &mut cb)?;
```

`GitPushStats { pushed, rejected, remote_rejected, unexported_bookmarks }` (git.rs:170) has
**`all_ok()`** and `some_exported()`. **Raise `GitError`** (listing `rejected` + `remote_rejected`
names/reasons) when `!stats.all_ok()`. Read the two targets from the view (view.rs):
`view.get_local_bookmark(name).as_normal().cloned()` (the new target; `as_normal` → `Option<&CommitId>`,
op_store.rs:104) and `view.get_remote_bookmark(name.to_remote_symbol(remote)).target.as_normal().cloned()`
(the expected current remote target; `RemoteRef.target`, view.rs:234). A conflicted (non-`as_normal`)
bookmark ⇒ raise `GitError` ("refusing to push a conflicted bookmark").

**(e) `git_clone` has no jj-lib primitive — compose it in Python (§4c).**

---

## 2. Verified jj-lib APIs (against the pinned 0.38.0 source)

| What | Signature / fact | Ref |
|---|---|---|
| Subprocess opts | `GitSubprocessOptions::from_settings(&UserSettings) -> Result<Self, ConfigGetError>` (`{executable_path, environment}`) | git.rs:122/131 |
| **Fetcher ctor** | `GitFetch::new(&mut MutableRepo, GitSubprocessOptions, &GitImportOptions) -> Result<GitFetch, UnexpectedGitBackendError>` | git.rs:2756 |
| **Fetch** | `GitFetch::fetch(&mut self, &RemoteName, ExpandedFetchRefSpecs, &mut dyn GitSubprocessCallback, Option<NonZeroU32>, Option<FetchTagsOverride>) -> Result<(), GitFetchError>` | git.rs:2779 |
| **Fetch import** | `GitFetch::import_refs(&mut self) -> Result<GitImportStats, GitImportError>` (no-op if no `fetch()` since last call) | git.rs:2883 |
| Default branch | `GitFetch::get_default_branch(&self, &RemoteName) -> Result<Option<RefNameBuf>, GitFetchError>` (spawns `git remote show`) | git.rs:2863 |
| Refspec expand | `git::expand_fetch_refspecs(&RemoteName, GitFetchRefExpression) -> Result<ExpandedFetchRefSpecs, GitRefExpansionError>` | git.rs:2460 |
| Ref expression | `GitFetchRefExpression { bookmark: StringExpression, tag: StringExpression }` (`all()` / `none()`) | git.rs:2428 |
| Callback trait | `jj_lib::git::GitSubprocessCallback` (`needs_progress`, `progress`, `local_sideband`, `remote_sideband`) | git_subprocess.rs:674 |
| Callback types | `jj_lib::git::{GitProgress, GitSidebandLineTerminator}` (public re-exports) | git.rs:47/48 |
| **Push** | `git::push_branches(&mut MutableRepo, GitSubprocessOptions, &RemoteName, &GitBranchPushTargets, &mut dyn GitSubprocessCallback) -> Result<GitPushStats, GitPushError>` | git.rs:2945 |
| Push targets | `GitBranchPushTargets { branch_updates: Vec<(RefNameBuf, BookmarkPushUpdate)> }`; `BookmarkPushUpdate { old_target: Option<CommitId>, new_target: Option<CommitId> }` | git.rs:2930/2920 |
| Push stats | `GitPushStats { pushed, rejected, remote_rejected, unexported_bookmarks }` + `all_ok()` / `some_exported()` | git.rs:170 |
| Local bookmark | `View::get_local_bookmark(&RefName) -> &RefTarget`; `RefTarget::as_normal() -> Option<&CommitId>` | view.rs:168 / op_store.rs:104 |
| Remote bookmark | `View::get_remote_bookmark(RemoteRefSymbol) -> &RemoteRef` (`.target: RefTarget`); `RefName::to_remote_symbol(&RemoteName)` | view.rs:234 / ref_name.rs:311 |
| Import options | `GitImportOptions { auto_local_bookmark, abandon_unreachable_commits, remote_auto_track_bookmarks }` — no `Default`; build explicitly (slice 10) | git.rs:483 |
| Has-changes | `MutableRepo::has_changes(&self) -> bool` (the no-op signal, slice 10) | repo.rs:929 |

**Errors (reuse slice 10's `GitError`; `map_git_err`):**

| jj-lib error | variants | map to |
|---|---|---|
| `GitFetchError` (git.rs:2401) | `NoSuchRemote`, `RemoteName`, `RejectedUpdates`, `Subprocess` | `GitError` |
| `GitPushError` (git.rs:2918) | `NoSuchRemote`, `RemoteName`, `Subprocess`, `UnexpectedBackend` | `GitError` |
| `GitRefExpansionError` (git.rs:2452) | bad branch pattern | `GitError` (a bad `bookmarks=[...]` pattern is arguably user-input; `GitError` is acceptable and simplest) |
| `UnexpectedGitBackendError` | not a git backend | `GitError` |

> **Decision (recommended): all network failures → `GitError`.** A `GitNetworkError ⊂ GitError`
> subclass is *possible* (auth/connection vs. local config), but jj-lib doesn't cleanly separate them
> (network errors arrive wrapped in `Subprocess`), so a finer split would be guesswork. Keep one
> `GitError`; revisit if a real consumer needs the distinction. **Push rejection raises `GitError`**
> with the rejected ref names/reasons (a partial/failed push the caller must see).

---

## 3. The structural facts (carry from slices 9–10; re-verify)

**(a) Fresh loader per verb (the slice-10 trap — STILL APPLIES).** Fetch/push read the remote set from
the **git config snapshot** the `GitBackend` froze at open time (`GitFetch::new` → `get_git_backend` →
`git_repo()`; `push_updates` → `get_git_backend`). The workspace's cached loader is opened once at
`Workspace::load`, so a remote added through this handle (slice 10's `add_remote`) would be invisible
to a fetch/push on the same handle. **Use `PyWorkspace::fresh_loader` (added in slice 10)** for both
`git_fetch` and `git_push`, so each re-opens the store from disk and sees the current git config.
(`git_clone` composes Python verbs that each already do this.)

**(b) `import_refs` may abandon commits ⇒ `rebase_descendants()` before commit; reuse `finish_op`.**
After `fetcher.import_refs()` (fetch) the same landmine-#1 applies. Push's `push_branches` only updates
remote-tracking bookmarks (no commit rewrite) — `rebase_descendants()` is harmless but unnecessary;
still route both through **`finish_op`** (workspace.rs) for the "checkout moved `@` + return the
`Operation` dict" tail, uniformly. No-op detection: **`tx.repo_mut().has_changes()`** (slice 10) — a
fetch that imported nothing, or a push that moved no remote-tracking ref, returns `None`.

**(c) `!Send` discipline.** `MutableRepo`, `GitFetch`, and the `Transaction` are `!Send`. Create the
transaction, build the fetcher/targets, run fetch/import/push, check `has_changes`, and commit **all
inside one synchronous `allow_threads` closure on one thread** (as in slice 9/10). The closure returns
`Option<Arc<ReadonlyRepo>>` (`None` = no-op); `finish_op` runs after on the GIL.

---

## 4. Faithful primitive, simplest form (the scope decisions)

### 4a. `git_fetch(remote, *, bookmarks=None)`
- `GitFetch::new(tx.repo_mut(), GitSubprocessOptions::from_settings(settings)?, &options)` with the
  plain slice-10 `GitImportOptions { auto_local_bookmark: false, abandon_unreachable_commits: true,
  remote_auto_track_bookmarks: HashMap::new() }`.
- `ref_expr = GitFetchRefExpression { bookmark: <all or patterns>, tag: StringExpression::none() }`;
  `expand_fetch_refspecs(remote, ref_expr)?`; `fetch(remote, refspecs, &mut NullGitCallback, None,
  None)?`; `import_refs()?`; `rebase_descendants()`; `has_changes()` ? commit `"fetch from <remote>"`
  : `None`; `finish_op` tail.
- **`bookmarks`** (optional `list[str]`): when given, build the `bookmark` `StringExpression` from those
  exact names (see `StringExpression`/`StringPattern` — `StringExpression::all()` for `None`). **Flag:**
  globs/negative patterns and `--all-remotes` are out of scope; `tag` fetching stays `none()` (jj-lib's
  own default, TODO #7528).

### 4b. `git_push(remote, bookmark, *, allow_new=False)`
- Load the local bookmark target (`view.get_local_bookmark`) and the remote-tracking target
  (`view.get_remote_bookmark(name.to_remote_symbol(remote))`). A **missing local bookmark** ⇒
  `GitError` ("no local bookmark '<b>'"). A **conflicted** (non-`as_normal`) local/remote target ⇒
  `GitError`.
- `old_target = remote-tracking as_normal` (None if no remote-tracking ref). **`allow_new`**: if
  `old_target` is `None` and `allow_new` is false ⇒ `GitError` ("bookmark '<b>' doesn't exist on the
  remote; pass allow_new=True") — mirrors the CLI's `--allow-new` gate. `new_target = local as_normal`.
- `push_branches(tx.repo_mut(), opts, remote.as_ref(), &targets, &mut cb)?`; if `!stats.all_ok()` ⇒
  `GitError` (names from `rejected`/`remote_rejected`). `has_changes()` ? commit `"push to <remote>"` :
  `None`; `finish_op` tail. **Flag:** deleting a bookmark on the remote (`new_target=None`), pushing
  multiple bookmarks, `--all`/`--tracked`/`-r <rev>` selection, and force-with-lease beyond jj-lib's
  built-in negotiation are out of scope — one named bookmark, create-or-fast-forward.

### 4c. `git_clone(url, path, *, colocate=False, remote="origin")` — pure-Python composition
**jj-lib has no `git_clone`** (grep is empty; the CLI's `cmd_git_clone` orchestrates primitives). So
this is a `@classmethod` on the Python `Workspace` that composes existing verbs — **no new Rust**:
1. `ws = Workspace.init(path, colocate=colocate)`
2. `ws.add_remote(remote, url)`
3. `ws.git_fetch(remote)`
4. **default-branch checkout (the one refinement, recommended but decideable):** point the new `@` at
   the remote's default branch so the clone is immediately usable. Discover it via a thin Rust
   `git_default_branch(remote) -> str | None` wrapping `GitFetch::get_default_branch` (git.rs:2863), or
   — simpler, no extra subprocess — pick from the freshly-imported bookmarks (prefer the remote's HEAD,
   else `main`/`master`, else the sole bookmark). Then open a transaction and `tx.new([<default
   commit>])` (slice 2) so `@` is an empty child of the default branch tip, and `tx.create_bookmark`/
   track as desired. **Decision:** if discovery is ambiguous (no default, many bookmarks), **leave `@`
   on the empty root child** and document it — don't guess. Recommend implementing the
   `get_default_branch` helper for fidelity; if it proves fiddly under the local-bare-remote fixture,
   ship clone as steps 1–3 only and flag the default-branch checkout as a follow-up.

Return the `Workspace`. Errors propagate as `WorkspaceError` (init) / `GitError` (remote/fetch).

---

## 5. Rust: `#[pymethods]` on `PyWorkspace` (`src/workspace.rs`)

Two new methods (`git_fetch`, `git_push`) + the `NullGitCallback` + (optional) `git_default_branch`.
Imports to add: `use jj_lib::git::{GitFetch, GitFetchRefExpression, GitSubprocessOptions,
GitBranchPushTargets, GitProgress, GitSidebandLineTerminator, GitSubprocessCallback};` (and the
`expand_fetch_refspecs` / `push_branches` free fns as `git::expand_fetch_refspecs` etc.);
`BookmarkPushUpdate` from `jj_lib::git`; `RefNameBuf` from `jj_lib::ref_name`; `std::io`. Reuse
`GitImportOptions`, `StringExpression`, `RemoteName`, `fresh_loader`, `finish_op`, `map_git_err`,
`map_backend_err` from slices 0–10.

```rust
/// No-op `GitSubprocessCallback`: the binding doesn't surface fetch/push progress yet (a future
/// slice could route these to a Python callback). Mirrors jj-lib's own test `NullCallback`.
struct NullGitCallback;
impl GitSubprocessCallback for NullGitCallback {
    fn needs_progress(&self) -> bool { false }
    fn progress(&mut self, _p: &GitProgress) -> std::io::Result<()> { Ok(()) }
    fn local_sideband(&mut self, _m: &[u8], _t: Option<GitSidebandLineTerminator>) -> std::io::Result<()> { Ok(()) }
    fn remote_sideband(&mut self, _m: &[u8], _t: Option<GitSidebandLineTerminator>) -> std::io::Result<()> { Ok(()) }
}
```

```rust
/// Fetch `remote`'s bookmarks into jj's view (`jj git fetch`): subprocess `git fetch` + import,
/// publishing one op — or `None` if nothing changed. Subprocess + network → entirely off the GIL;
/// the `!Send` `GitFetch`/`Transaction` live and die inside the one closure on one thread.
#[pyo3(signature = (remote, bookmarks=None))]
fn git_fetch<'py>(&self, py: Python<'py>, remote: &str, bookmarks: Option<Vec<String>>)
    -> PyResult<Option<Bound<'py, PyDict>>>
{
    let mut guard = self.locked()?;
    let ws: &mut Workspace = &mut guard;
    let name = ws.workspace_name().to_owned();
    let loader = Self::fresh_loader(ws)?;                  // slice-10 staleness fix
    let settings = ws.repo_loader().settings().clone();
    let remote = remote.to_owned();
    let new_repo = py.allow_threads(move || -> PyResult<Option<Arc<ReadonlyRepo>>> {
        let repo = loader.load_at_head().map_err(map_backend_err)?;
        let options = GitImportOptions { auto_local_bookmark: false,
            abandon_unreachable_commits: true, remote_auto_track_bookmarks: HashMap::new() };
        let subprocess = GitSubprocessOptions::from_settings(&settings).map_err(map_git_err)?;
        let mut tx = repo.start_transaction();
        {
            let mut fetcher = GitFetch::new(tx.repo_mut(), subprocess, &options).map_err(map_git_err)?;
            let bookmark = match &bookmarks {
                None => StringExpression::all(),
                Some(names) => /* build an expression matching exactly `names` */,
            };
            let ref_expr = GitFetchRefExpression { bookmark, tag: StringExpression::none() };
            let refspecs = git::expand_fetch_refspecs(remote.as_str().as_ref(), ref_expr)
                .map_err(map_git_err)?;
            fetcher.fetch(remote.as_str().as_ref(), refspecs, &mut NullGitCallback, None, None)
                .map_err(map_git_err)?;
            fetcher.import_refs().map_err(map_git_err)?;
        } // drop fetcher → release the &mut borrow before rebase/commit
        tx.repo_mut().rebase_descendants().map_err(map_backend_err)?;
        if !tx.repo_mut().has_changes() { return Ok(None); }
        Ok(Some(tx.commit(format!("fetch from git remote '{remote}'")).map_err(map_backend_err)?))
    })?;
    let Some(new_repo) = new_repo else { return Ok(None) };
    let repo = { let l = ws.repo_loader(); py.allow_threads(|| l.load_at_head()).map_err(map_backend_err)? };
    Ok(Some(self.finish_op(py, ws, &name, &repo, &new_repo)?))
}
```

> **Borrow note (verify):** `GitFetch` holds `&mut MutableRepo`; **drop it (inner block scope) before**
> `tx.repo_mut().rebase_descendants()`/`commit`, or the second `repo_mut()` borrow conflicts. The
> `finish_op` tail needs an `old_repo` for the moved-`@` check — reload head **after** the closure (its
> view predates the fetch op; cheap `Arc`), exactly as the no-op branches elsewhere do, **or** capture
> `repo.clone()` before the closure consumes `loader`. Pick whichever type-checks; keep it `Arc`-cheap.

`git_push` mirrors this: read the two `CommitId`s from `repo.view()` **before** starting the tx (so the
read borrow ends), build `GitBranchPushTargets`, `push_branches(...)`, `all_ok()` gate, `has_changes`,
commit `"push to git remote '<remote>'"`, `finish_op`. **Verify the `bookmarks`→`StringExpression`
construction against `str_util.rs`** (likely `StringExpression::from_pattern`/an `any`/`union` of exact
patterns — grep `impl StringExpression` and `StringPattern::exact`; if the ergonomic constructor is
unobvious, support only `bookmarks=None` (= `all()`) in v1 and flag specific-bookmark fetch as a
refinement — don't block the slice).

**Verify while implementing (don't assume — the slice-5/-7/-9/-10 lesson):**
- **Drop `GitFetch` before re-borrowing `tx.repo_mut()`** (inner scope).
- **`fresh_loader` reused** for fetch/push (the remote must be found — slice-10 staleness).
- **`has_changes()` no-op signal** for both; keep the differential tolerant if exactness is fiddly.
- **`GitSubprocessOptions::from_settings`** default `executable_path = "git"` resolves on the devenv
  `PATH` — confirm `git` is available in `devenv shell` (it is; the conftest already shells out to it).
- **`BookmarkPushUpdate` field names** are `old_target` / `new_target` (test_git.rs:4394) — re-grep.
- **`get_default_branch` spawns another subprocess** — only call it once, in `git_clone`'s step 4.

---

## 6. Python: facade, stubs, errors

**No new exception, no new model.** `GitError` (slice 10) covers fetch/push/clone failures.

### `python/pyjutsu/workspace.py` (facade)
- `git_fetch(self, remote: str, *, bookmarks: list[str] | None = None) -> Operation | None`
- `git_push(self, remote: str, bookmark: str, *, allow_new: bool = False) -> Operation | None`
- `git_clone(cls, url, path, *, colocate=False, remote="origin") -> Workspace` — **`@classmethod`**,
  composing `init` + `add_remote` + `git_fetch` (+ §4c default-branch checkout). Match the existing
  docstring density + `Operation.model_validate(row) if row is not None else None` pattern.

### `python/pyjutsu/_pyjutsu.pyi` (stubs, `PyWorkspace`)
```python
    def git_fetch(self, remote: str, bookmarks: list[str] | None = ...) -> dict[str, object] | None: ...
    def git_push(self, remote: str, bookmark: str, allow_new: bool = ...) -> dict[str, object] | None: ...
    # only if you add the helper:
    def git_default_branch(self, remote: str) -> str | None: ...
```
(`git_clone` is pure-Python — no native stub.)

---

## 7. Differential tests (`tests/test_git_net.py`) per §5

**All local, no network:** use `file://` / on-disk **bare** remotes (the `bookmarked_repo` fixture
already builds a bare `origin` + a pushed `feature`). The differential oracle is `jj git fetch` /
`jj git push` / `jj git clone` on a `_copy_repo` sibling (reuse `tests/test_workspace_mgmt.py`'s
`_copy_repo`). Add `JjCli` helpers as needed (`jj git fetch <remote>`, `jj git push --bookmark <b>
[--allow-new]`, `jj git clone <url> <path>`); read landed refs straight from the bare repo with
`git -C <origin.git> show-ref` (the common oracle — dodge the slice-7 colocated jj-read trap).

Suggested cases:
- **`test_push_bookmark_matches_cli`** *(headline)*: on a colocated repo with a bare `origin` and a
  local bookmark, `ws.git_push("origin", "feature", allow_new=True)` vs `jj git push --bookmark feature
  --allow-new` on a copy. Assert `refs/heads/feature` now in the **bare** `origin` on both sides (via
  `git -C origin.git show-ref`), the remote-tracking `feature@origin` row appears in `ws.bookmarks()`,
  and **one op each**.
- **`test_push_new_without_allow_new_raises`**: pushing a brand-new bookmark with `allow_new=False` ⇒
  `GitError`.
- **`test_push_unknown_remote_raises`** / **`test_push_unknown_bookmark_raises`** ⇒ `GitError`.
- **`test_fetch_matches_cli`** *(headline)*: two colocated repos sharing one bare `origin`; push a new
  bookmark from repo A (or the CLI) to `origin`, then `ws_B.git_fetch("origin")` vs `jj git fetch` on a
  copy of B. Assert B picks up the bookmark (`feature@origin` remote-tracking row) on both sides; op
  published.
- **`test_fetch_noop_returns_none`**: a second `git_fetch` with nothing new ⇒ `None`, no op.
- **`test_clone_matches_cli`** *(headline)*: `Workspace.git_clone(bare_origin_url, dest)` vs
  `jj git clone <url> <cli_dest>`. Assert the clone has `origin` configured (`ws.remotes()`), the
  remote's bookmarks fetched (`ws.bookmarks()`), and `.jj` exists; if you implemented the default-branch
  checkout, assert `@`'s parent is the default branch tip on both sides (else assert the documented
  empty-`@` behaviour).
- **`test_push_then_fetch_roundtrip`** *(optional)*: push from A, fetch in B, B sees A's bookmark.

Mind the op-count asymmetry the CLI may add (the CLI wraps extra ops like the slice-9 `workspace add`
case): assert the **binding** publishes exactly one op per fetch/push and assert **state** (refs
present/absent) on the CLI side rather than strict CLI op parity. Keep no-op detection tolerant if
exactness is fiddly (`≤ 1` op + document), per §3(b).

Re-run the **whole** suite — additive `PyWorkspace` methods + one `@classmethod`, no new model/exception,
so every slice 0–10 test (incl. `test_git_interop.py`) must stay green.

---

## 8. Build / verify / report

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint     # ruff check + clippy -D warnings (NOT ruff format)
```

All green, then **do the M2-completion bump (§9)**, commit on `main`
(`Implement M2 slice 11: git fetch/push/clone (M2 complete, 0.41.0)`), and **report**. **No AI
attribution** anywhere.

---

## 9. M2 completion: the `0.41.0` bump (this slice only)

Slice 11 finishes M2. After the slice is green, bump pyjutsu `0.40.0 → 0.41.0` (versioned independently
of jj; the `jj-lib`/`gix` pins are unchanged):
- `python/pyjutsu/__init__.py` `__version__ = "0.41.0"` (leave `JJ_LIB_TARGET = "0.38.0"`).
- `Cargo.toml` `version = "0.41.0"`; `pyproject.toml` version if it carries one.
- **Rebuild** so `Cargo.lock` / `uv.lock` refresh; commit them.
- Concept §3/§5 status → "M2 implemented" if those docs track milestone state.
- The `JJ_VERSION == JJ_LIB_TARGET` tripwire stays green (unrelated to pyjutsu's own version).

Update memory: a slice-11 note + flip the M2-scoping note to "M2 complete @ 0.41.0".

---

## 10. Guardrails (carried)

- **Thin Rust, rich Python:** `git_fetch`/`git_push` return the published `Operation` dict (or `None`);
  `git_clone` is pure-Python composition returning a `Workspace`. **No `gix`/jj-lib type crosses FFI** —
  dicts/strings/None only.
- **GIL discipline:** subprocess + network → **all off the GIL** (`allow_threads`). The `!Send`
  `GitFetch`/`MutableRepo`/`Transaction` are created and dropped inside one synchronous closure on one
  thread (slice 9/10). The workspace `Mutex` is held for each verb's load→act sequence.
- **Fresh loader per verb** (slice-10 staleness): fetch/push must see remotes added in-process.
- **`rebase_descendants()` after fetch import** (landmine #1). Push authors no commit rewrite.
- **Faithful primitive, simplest form:** `git_fetch` = `GitFetch::new` + `fetch`(plain options,
  bookmarks-or-all, no tags) + `import_refs`; `git_push` = `push_branches` for one named bookmark
  (create-or-fast-forward, `all_ok()` gate, `allow_new`); `git_clone` = init + add_remote + fetch
  (+ default-branch checkout). Tag fetch/push, multi-bookmark/`--all`, deletes, `-r` selection, force,
  shallow `depth`, and progress callbacks are **flagged, not faked**. Every fallible path → `GitError`;
  only `Display` crosses FFI. **Pin stays `=0.38.0` + `gix =0.78.0`; `Cargo.lock` committed; everything
  through devenv** — never bare `cargo`/`maturin`/`pytest`/`jj`. The pinned `jj` 0.38.0 CLI is the
  differential oracle only.

> **Slice-11 traps (grepped against the pinned source + `tests/test_git.rs` while writing this guide;
> re-grep if doubted):** (a) jj 0.38 fetch/push are **`git` subprocesses** (`GitSubprocessOptions`) —
> all off the GIL; (b) `GitFetch` borrows `&mut MutableRepo` and `fetch()` wants an
> **`ExpandedFetchRefSpecs`** (via `expand_fetch_refspecs` + `GitFetchRefExpression{all, none}`) + a
> **4-method callback** (supply a no-op `NullGitCallback`) — **drop the fetcher before re-borrowing the
> repo**; (c) `fetch()` then **`import_refs()`** is two steps; import may abandon ⇒
> `rebase_descendants()` before commit; (d) **`fresh_loader`** still required (slice-10 config-snapshot
> staleness) or the remote isn't found; (e) push is **`push_branches`** with a `GitBranchPushTargets`
> built from `get_local_bookmark`/`get_remote_bookmark` `as_normal()` targets; gate on
> `GitPushStats::all_ok()`, honour `allow_new`; (f) **jj-lib has NO clone** — compose it in Python.
> See [[m2-slice10-git-interop]], [[m2-slice9-workspace-mgmt]], [[m2-slice7-undo-restore]].
