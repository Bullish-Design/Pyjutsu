# Pyjutsu M2 — Slice 10 Implementation Guide (git interop: `git_import` / `git_export` + remotes CRUD + `remotes()`)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec; git surface at §134, models at §140) →
> `M2_CONTINUATION_GUIDE.md` (the corrected spine for slices 2–11; §4 "Slice 10") → **this document**
> (the detailed, verified plan for slice 10) → `M2_IMPLEMENTATION_GUIDE.md` (original plan/error
> taxonomy) → the code it produces.
>
> **Pin unchanged:** `jj-lib = "=0.38.0"` (default features include `git`). Slice 9 is committed and
> pushed on `main` (`739d17b`); the working tree is clean — start from `main`. Slices 2+ ride under
> `0.40.0` until the **completed** M2 bumps to `0.41.0`. **This slice adds one new direct dependency
> (`gix`, see §1) — the first since M1.** Every API ref below is `file:line` into
> `~/.cargo/registry/.../jj-lib-0.38.0/src/`, **verified against the pinned source while writing this
> guide** (and cross-checked against jj-lib's own `tests/test_git.rs`).

---

## 0. Where slice 10 starts

Slices 0–9 built the full in-`Transaction` mutation surface, the op-log/stale/snapshot verbs, and the
workspace-lifecycle verbs. This slice is the **git interop** surface (concept §134): syncing jj's view
with the colocated/backing git repo (`git_import`/`git_export`) and managing git remotes. Network
fetch/push is **slice 11** (subprocess via `gix` networking) — **not** this slice. Like slices 5–9
these are **`PyWorkspace`-level** verbs (not `Transaction` methods).

Five verbs + one model:

1. **`Workspace.git_import() -> Operation | None`** — reflect changes in the backing git repo into jj's
   view (`import_head` + `import_refs`), publishing one operation. `None` if nothing changed. Matches
   `jj git import`.
2. **`Workspace.git_export() -> Operation | None`** — export jj's bookmarks/tags to the backing git
   repo's refs (`export_refs`), publishing one operation. `None` if nothing changed. Matches
   `jj git export`.
3. **`Workspace.remotes() -> list[Remote]`** — list configured git remotes (name + fetch url).
   Read-only. Matches `jj git remote list`.
4. **`Workspace.add_remote(name, url) -> None`** / **`remove_remote(name)`** / **`rename_remote(old,
   new)`** — remotes CRUD that mutate jj's view (each publishes one op). Match `jj git remote
   add|remove|rename`.
5. **`Workspace.set_remote_url(name, url) -> None`** — change a remote's fetch URL. **Pure git-config
   write — publishes NO jj operation** (the asymmetry of §3). Matches `jj git remote set-url`.
6. **New model `Remote` `{name, url}`** (`python/pyjutsu/models.py`), with a golden fixture
   regenerated. (`url` is the **fetch** url; `push_url` is available but out of scope — see §4.)

---

## 1. The new `gix` dependency (the fact that shapes this slice)

`jj_lib::git::add_remote` (git.rs:2116) takes two **`gix` types** in its signature — `fetch_tags:
gix::remote::fetch::Tags` and (indirectly) needs `gix::remote::Direction` to *read* a remote's URL
back for the `Remote` model. jj-lib does **not** re-export `gix`. So the binding must add `gix` as a
**direct dependency at jj-lib's exact locked version** so the types unify:

```toml
# Cargo.toml [dependencies] — match jj-lib 0.38.0's own gix pin (its Cargo.toml:93) exactly so the
# `gix::remote::fetch::Tags` / `gix::remote::Direction` types passed to `jj_lib::git::*` unify. Only
# remote management + URL read are used; default-features = false keeps the build lean (jj-lib already
# compiles gix with its needed features, and Cargo unifies them).
gix = { version = "=0.78.0", default-features = false }
```

`Cargo.lock` already resolves `gix 0.78.0` (it's jj-lib's transitive dep), so this reuses the locked
build — **no `Cargo.lock` churn beyond adding the direct edge; commit it.** Verify after adding:
`devenv shell -- cargo tree -i gix` shows a single `gix v0.78.0`. If a second version appears, pin
harder (`"=0.78.0"`) and re-lock.

**Why this is the only viable path for `Remote.url`:** jj-lib 0.38 exposes `get_all_remote_names`
(names only, git.rs:2098) but **no public URL reader**. The URL lives in the git repo's config, reached
via `get_git_backend(store)` (git.rs:386, **public**) → `GitBackend::git_repo()` (git_backend.rs:336,
**public**, returns an owned `gix::Repository`) → `repo.find_remote(name)?.url(Direction::Fetch)`
(`Option<&gix::Url>`, stringify via `gix::Url`'s `Display`). jj-lib's own
`tests/test_git.rs::assert_fetch_and_push_urls` reads URLs exactly this way. **No `gix` type crosses
the FFI** — only the stringified URL does (guardrail intact).

---

## 2. Verified jj-lib (+ gix) APIs (against the pinned 0.38.0 source)

| What | Signature / fact | Ref |
|---|---|---|
| **Import refs** | `git::import_refs(&mut MutableRepo, &GitImportOptions) -> Result<GitImportStats, GitImportError>` | git.rs:529 |
| **Import HEAD** | `git::import_head(&mut MutableRepo) -> Result<(), GitImportError>` (updates `git_head()` in the view) | git.rs:983 |
| Import options | `struct GitImportOptions { auto_local_bookmark: bool, abandon_unreachable_commits: bool, remote_auto_track_bookmarks: HashMap<RemoteNameBuf, StringMatcher> }` — **no `Default` impl**; build it explicitly (§3) | git.rs:483 |
| Import stats | `struct GitImportStats { abandoned_commits, changed_remote_bookmarks, changed_remote_tags, failed_ref_names }` (all `Vec`) — used only to detect "did anything change" | git.rs:494 |
| **Export refs** | `git::export_refs(&mut MutableRepo) -> Result<GitExportStats, GitExportError>` | git.rs:1103 |
| Export stats | `struct GitExportStats { failed_bookmarks, failed_tags }` (`Vec`) | git.rs:1067 |
| **List remotes** | `git::get_all_remote_names(&Store) -> Result<Vec<RemoteNameBuf>, UnexpectedGitBackendError>` (sorted) | git.rs:2098 |
| **Add remote** | `git::add_remote(&mut MutableRepo, &RemoteName, url: &str, push_url: Option<&str>, fetch_tags: gix::remote::fetch::Tags, bookmark_expr: &StringExpression) -> Result<(), GitRemoteManagementError>` | git.rs:2116 |
| **Remove remote** | `git::remove_remote(&mut MutableRepo, &RemoteName) -> Result<(), GitRemoteManagementError>` (also deletes the remote's git refs from the view) | git.rs:2173 |
| **Rename remote** | `git::rename_remote(&mut MutableRepo, old: &RemoteName, new: &RemoteName) -> Result<(), GitRemoteManagementError>` | git.rs:2230 |
| **Set remote url** | `git::set_remote_urls(&Store, &RemoteName, new_url: Option<&str>, new_push_url: Option<&str>) -> Result<(), GitRemoteManagementError>` — **takes `&Store`, not `&mut MutableRepo`: a pure git-config write, no view change, no op** | git.rs:2332 |
| Git backend access | `git::get_git_backend(&Store) -> Result<&GitBackend, UnexpectedGitBackendError>` (public); `GitBackend::git_repo(&self) -> gix::Repository` (public) | git.rs:386 / git_backend.rs:336 |
| URL read (gix) | `gix::Repository::find_remote(name: &str) -> Result<gix::Remote, _>`; `gix::Remote::url(Direction) -> Option<&gix::Url>`; `gix::Url: Display` | (jj test helper `assert_fetch_and_push_urls`) |
| String expr "all" | `StringExpression::all()` (matches every bookmark) | str_util.rs:368 |
| Name ctors | `RemoteNameBuf: From<&str>`/`From<String>`; `&str: AsRef<RemoteName>` (so `"origin".as_ref()` → `&RemoteName`); `RemoteName::as_str()` | ref_name.rs (impl_name_type) |

**Errors (the §1 "decide here" — recommendation: introduce `GitError ⊂ BackendError`):**

| jj-lib error | variants worth noting | map to |
|---|---|---|
| `GitImportError` (git.rs:452) | `Backend`, `Index`, `Git(Box<dyn Error>)`, `UnexpectedBackend`, `MissingHeadTarget`, `MissingRefAncestor` | `GitError` |
| `GitExportError` (git.rs:1022) | `Git(Box<dyn Error>)`, `UnexpectedBackend` | `GitError` |
| `GitRemoteManagementError` (git.rs:1897) | `NoSuchRemote`, `RemoteAlreadyExists`, `RemoteName`, `NonstandardConfiguration`, `GitConfigSaveError`, `InternalGitError`, `UnexpectedBackend`, `RefExpansionError` | `GitError` (and see §5 note) |
| `UnexpectedGitBackendError` (git.rs:383) | unit struct — "this store isn't a git backend" | `GitError` |

---

## 3. The two structural facts (verify while implementing)

**(a) `git_import`/`git_export`/`add`/`remove`/`rename` are `MutableRepo` mutations → one op each;
`set_remote_url` is not.** `import_refs`/`export_refs`/`add_remote`/`remove_remote`/`rename_remote` all
take `&mut MutableRepo` and change the view (refs, bookmarks, git_refs), so each runs inside
`repo.start_transaction()` → … → `tx.commit("…")`, publishing exactly one operation — the same shape as
`forget_workspace` (slice 9, [[m2-slice9-workspace-mgmt]]). `set_remote_urls` takes **`&Store`** and only
rewrites git config, so it publishes **no jj operation** — call it directly off the held lock, no
transaction. **This asymmetry drives the tests' op-count assertions.**

**(b) import abandons commits ⇒ `rebase_descendants()` before commit; both may move `@` ⇒ checkout.**
`import_refs` can abandon unreachable git commits (its `abandon_unreachable_commits` option), which
registers rewrites — so run `tx.repo_mut().rebase_descendants()` after it and before `commit`
(landmine #1, the slice-5/-7/-9 lesson). jj-lib's own `tests/test_git.rs:241` does exactly
`import_refs(...); tx.repo_mut().rebase_descendants().unwrap(); tx.commit(...)`. Both import and export
can move this workspace's `@` (e.g. import abandons the commit `@` sat on; export is usually `@`-neutral
but treat uniformly), so reuse the existing **`finish_op`** tail (workspace.rs:162) — load head before,
commit, then `finish_op(py, ws, &name, &repo, &new_repo)` checks out the new `@` if it moved and returns
the published `Operation`. **No-op detection:** if the tx changed nothing, match the CLI and publish no
op. The cheapest faithful test: compare the new repo's view/op against head — but simpler and robust is
to check the stats + whether `@` moved. **Recommended:** for `git_import`, treat it as a no-op (return
`None`, roll the tx back by simply not committing) when `stats.abandoned_commits`,
`changed_remote_bookmarks`, `changed_remote_tags`, `failed_ref_names` are **all empty** *and*
`import_head` left `git_head()` unchanged; for `git_export`, when `diff` produced no updates — but jj-lib
gives you only `failed_*` in `GitExportStats`, so detect "nothing exported" by comparing
`repo.view().git_refs()` before/after, or (simpler) by checking whether `tx.repo_mut()` recorded any
view change. **Verify the cleanest no-op signal against `tests/test_git.rs` while implementing**; if
detection proves fiddly, it is acceptable for v1 to always commit an op and document that `git_import`/
`git_export` may publish an empty-effect op where the CLI would skip it — but prefer the `None` path,
and make the differential test tolerant (assert "≤ 1 new op", or set up a guaranteed-change scenario).

---

## 4. Faithful primitive, simplest form (the scope decisions)

- **`git_import`** = `import_head(mut_repo)?; import_refs(mut_repo, &options)?` then `rebase_descendants`
  + commit. Options (matching jj-lib's test `default_import_options` + the CLI's plain `jj git import`):
  `GitImportOptions { auto_local_bookmark: false, abandon_unreachable_commits: true,
  remote_auto_track_bookmarks: HashMap::new() }`. **Flag:** `--remote`/track refinements out of scope.
- **`git_export`** = `export_refs(mut_repo)?` + commit. **Flag:** if `stats.failed_bookmarks` is
  non-empty, surface it — recommend raising `GitError` listing the failed names (a partial export is a
  real failure the caller must see), since the binding returns plain data, not a stats object. (Decide:
  raise on any failure vs. return-and-ignore. Recommendation: **raise** — it's the safe default and
  matches "every fallible path maps an error".)
- **`add_remote`** = `add_remote(mut_repo, name.as_ref(), url, None, gix::remote::fetch::Tags::None,
  &StringExpression::all())` (exactly jj-lib's own test call, test_git.rs:4192) + commit. **Flag:**
  `push_url`, `fetch_tags`, and per-remote auto-track (`bookmark_expr`) are out of scope — faked to the
  CLI's defaults, not exposed.
- **`remove_remote`** = `remove_remote(mut_repo, name.as_ref())` + commit. `NoSuchRemote` → `GitError`.
- **`rename_remote`** = `rename_remote(mut_repo, old.as_ref(), new.as_ref())` + commit.
- **`set_remote_url`** = `set_remote_urls(store, name.as_ref(), Some(url), None)` — **no tx, no op**.
- **`remotes`** = `get_all_remote_names(store)` for names, then per name read the fetch URL via
  `get_git_backend(store)?.git_repo().find_remote(name.as_str())?.url(Direction::Fetch)` →
  `Option<gix::Url>` → `map(|u| u.to_string())`. A remote with no fetch URL ⇒ `url = None`.
  **The `Remote` model's `url` is therefore `str | None`.**

`git_clone`/`git_fetch`/`git_push` (network) are **slice 11**. `tags` and `@git` internal refs are
jj-lib internals here, not surfaced.

---

## 5. Rust: `#[pymethods]` on `PyWorkspace` (`src/workspace.rs`)

All slot into the existing `#[pymethods] impl PyWorkspace`, beside the slice-9 verbs. Lock discipline +
off-GIL exactly like `forget_workspace`/`undo`. **Imports to add:**
`use jj_lib::git::{self, GitImportOptions};` (call as `git::import_refs` etc.); a new `map_git_err` in
`crate::errors`; `gix::remote::{Direction, fetch::Tags}`; `jj_lib::str_util::StringExpression`;
`std::collections::HashMap`; `RemoteName`/`RemoteNameBuf` from `jj_lib::ref_name`; `RemoteData` from
`crate::convert`. `get_git_backend` from `jj_lib::git`.

Sketches (adapt to the surrounding dense-comment style):

```rust
/// Reflect changes in the backing git repo into jj's view (`jj git import`): import HEAD + refs,
/// publishing one operation — or `None` if nothing changed. If import abandons the commit `@` sat on,
/// the on-disk working copy is checked out to the new `@` (off the GIL).
fn git_import<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
    let mut guard = self.locked()?;
    let ws: &mut Workspace = &mut guard;
    let name = ws.workspace_name().to_owned();
    let repo = { let loader = ws.repo_loader();
        py.allow_threads(|| loader.load_at_head()).map_err(map_backend_err)? };

    // !Send Transaction created+dropped in one closure → off-GIL sound (like forget_workspace).
    let options = GitImportOptions {
        auto_local_bookmark: false,
        abandon_unreachable_commits: true,
        remote_auto_track_bookmarks: HashMap::new(),
    };
    let (new_repo, changed) = py.allow_threads(|| -> PyResult<_> {
        let mut tx = repo.start_transaction();
        git::import_head(tx.repo_mut()).map_err(map_git_err)?;
        let stats = git::import_refs(tx.repo_mut(), &options).map_err(map_git_err)?;
        tx.repo_mut().rebase_descendants().map_err(map_backend_err)?;
        let changed = !stats.abandoned_commits.is_empty()
            || !stats.changed_remote_bookmarks.is_empty()
            || !stats.changed_remote_tags.is_empty()
            // VERIFY: also treat a git_head() change as "changed" — compare before/after, or rely on
            // the op having a non-empty diff. See §3(b).
            ;
        // ... commit only if changed; else drop tx for a true no-op.
        if !changed { return Ok((repo.clone(), false)); }
        let new_repo = tx.commit("import git refs").map_err(map_backend_err)?;
        Ok((new_repo, true))
    })?;
    if !changed { return Ok(None); }
    Ok(Some(self.finish_op(py, ws, &name, &repo, &new_repo)?))
}
```

> **Borrow note (verify):** `finish_op` takes `&self` + `ws: &mut Workspace`; you already hold the
> guard as `ws`. `finish_op` returns the `Operation` dict and does the conditional checkout — reuse it
> verbatim. The `repo.clone()` no-op branch is cheap (`Arc`).

```rust
/// Export jj's bookmarks/tags to the backing git repo's refs (`jj git export`), publishing one op —
/// or `None` if nothing changed. Raises GitError listing any bookmark that failed to export.
fn git_export<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> { /* export_refs;
   if !stats.failed_bookmarks.is_empty() => GitError; no-op detection per §3(b); finish_op tail */ }

/// List configured git remotes (name + fetch url). Read-only. Matches `jj git remote list`.
fn remotes<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let guard = self.locked()?;
    let loader = guard.repo_loader();
    let rows = py.allow_threads(|| -> PyResult<Vec<RemoteData>> {
        let repo = loader.load_at_head().map_err(map_backend_err)?;
        let store = repo.store();
        let names = git::get_all_remote_names(store).map_err(map_git_err)?;
        let backend = git::get_git_backend(store).map_err(map_git_err)?;
        let git_repo = backend.git_repo();
        names.iter().map(|n| {
            let url = git_repo.find_remote(n.as_str()).ok()
                .and_then(|r| r.url(Direction::Fetch).map(|u| u.to_string()));
            Ok(RemoteData::new(n.as_str(), url.as_deref()))
        }).collect()
    })?;
    rows.iter().map(|r| r.to_dict(py)).collect()
}

/// Add a git remote (`jj git remote add`); publishes one op. push_url/tags/track are CLI defaults.
fn add_remote(&self, py: Python<'_>, name: &str, url: &str) -> PyResult<()> { /* tx; git::add_remote(
   tx.repo_mut(), name.as_ref(), url, None, Tags::None, &StringExpression::all())?; commit */ }

fn remove_remote(&self, py: Python<'_>, name: &str) -> PyResult<()> { /* tx; git::remove_remote; commit */ }
fn rename_remote(&self, py: Python<'_>, old: &str, new: &str) -> PyResult<()> { /* tx; rename; commit */ }

/// Change a remote's fetch URL (`jj git remote set-url`). Pure git-config write — NO jj operation.
fn set_remote_url(&self, py: Python<'_>, name: &str, url: &str) -> PyResult<()> {
    let guard = self.locked()?;
    let loader = guard.repo_loader();
    py.allow_threads(|| -> PyResult<_> {
        let repo = loader.load_at_head().map_err(map_backend_err)?;
        git::set_remote_urls(repo.store(), name.as_ref(), Some(url), None).map_err(map_git_err)
    })
}
```

**Verify while implementing (don't assume — the slice-5/-7/-9 lesson):**
- **`cargo tree -i gix` shows one `0.78.0`** after adding the dep (§1). If `add_remote`'s `Tags`/the
  URL read won't type-check, the gix versions diverged — pin `=0.78.0`.
- **`finish_op` reuse** — it already does "checkout new `@` if moved + return Operation dict"; confirm
  its signature still matches (`&self, py, ws: &mut Workspace, name, old_repo, new_repo`).
- **No-op detection** (§3(b)) — settle import's and export's "did anything change" signal against
  `tests/test_git.rs`; keep tests tolerant if you can't make it exact.
- **`get_git_backend`/`git_repo()` are public** (re-confirm `git.rs:386` / `git_backend.rs:336`).
- **`set_remote_urls` is `&Store`** — confirm it needs no tx and produces no op (the test
  `test_set_remote_urls` wraps it in a tx only because it *also* calls `add_remote`; set-url alone is
  config-only).
- **`map_git_err`** — add to `errors.rs`: `GitError ⊂ BackendError`, `create_exception!` + register +
  re-export in `python/pyjutsu/errors.py` and `__init__.py` + `.pyi`. Display-only crosses FFI.

---

## 6. Python: model, facade, stubs, errors

### `src/errors.rs` — new `GitError ⊂ BackendError`

```rust
create_exception!(_pyjutsu, GitError, BackendError, "A git import/export or remote operation failed.");
// register() add; and:
pub(crate) fn map_git_err<E: std::fmt::Display>(err: E) -> PyErr { GitError::new_err(err.to_string()) }
```

Re-export `GitError` from `python/pyjutsu/errors.py`, `__init__.py` (`__all__`), and `_pyjutsu.pyi`.

### `python/pyjutsu/models.py` — new `Remote`

```python
class Remote(BaseModel):
    """A configured git remote: its name and fetch URL (``jj git remote list``)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    #: The remote's fetch URL; ``None`` if the remote has no fetch URL configured.
    url: str | None
```

Add to `__init__.py` imports + `__all__`, and a **golden** entry (`tests/golden/model_fields.json`:
`"Remote": ["name", "url"]` — the golden test will confirm against `models.Remote.model_fields`).

### `src/convert.rs` — `RemoteData`

Mirror the slice-9 `WorkspaceInfoData`: `{name: String, url: Option<String>}` with
`new(name: &str, url: Option<&str>)` and `to_dict`. Pure data.

### `python/pyjutsu/workspace.py` (facade) + `_pyjutsu.pyi`

Facade methods `git_import`/`git_export` (→ `Operation | None`), `remotes` (→ `list[Remote]`),
`add_remote`/`remove_remote`/`rename_remote`/`set_remote_url` (→ `None`), matching the existing
docstring density and `model_validate` pattern. Stubs in `PyWorkspace`:

```python
    def git_import(self) -> dict[str, object] | None: ...
    def git_export(self) -> dict[str, object] | None: ...
    def remotes(self) -> list[dict[str, object]]: ...
    def add_remote(self, name: str, url: str) -> None: ...
    def remove_remote(self, name: str) -> None: ...
    def rename_remote(self, old: str, new: str) -> None: ...
    def set_remote_url(self, name: str, url: str) -> None: ...
```

---

## 7. Differential tests (`tests/test_git_interop.py`) per §5

Reuse the harness (`_copy_repo`, `jj`/`scratch_repo`/`linear_repo`/`bookmarked_repo` fixtures,
`jj.op_log_ids`, `jj.bookmarks`, `jj.commit_id`). The `bookmarked_repo` fixture already builds a
colocated repo + a bare `origin` remote + a pushed `feature` bookmark — ideal for these tests. Add a
`JjCli.remotes(repo) -> dict[str, str]` helper (`jj git remote list` → `{name: url}`; verify its plain
output format in 0.38: lines like `origin https://…`).

Suggested cases:

- **`test_remotes_lists_origin`**: on `bookmarked_repo`, `{r.name for r in ws.remotes()} == {"origin"}`
  and the binding's `url` matches `jj git remote list`'s url for `origin`.
- **`test_add_remote_matches_cli`**: binding `ws.add_remote("upstream", url)` vs
  `jj git remote add upstream <url>` on a copy. Assert `upstream` appears in `ws.remotes()` and
  `jj.remotes(other)` with the same url; **one new op each**.
- **`test_remove_remote_matches_cli`** / **`test_rename_remote_matches_cli`**: structurally vs
  `jj git remote remove|rename`; name gone/changed on both sides; one op each. Unknown remote →
  `GitError`.
- **`test_set_remote_url_no_op`** *(headline asymmetry)*: `ws.set_remote_url("origin", new_url)` changes
  the url (binding `remotes()` + `jj.remotes` agree) but **publishes no new operation** (op count
  unchanged on the binding side) — unlike `jj git remote set-url`, confirm whether the *CLI* creates an
  op in 0.38 (it may publish a trivial op or none; assert only the binding's no-op invariant and the
  url change, and document the CLI's behavior you observe).
- **`test_git_export_matches_cli`** *(headline)*: create a bookmark in a transaction (binding) /
  `jj bookmark create` (CLI copy), then `ws.git_export()` vs `jj git export`. Assert the git ref
  (`refs/heads/<bookmark>`) now exists on both sides (read via `jj.bookmarks` `@git` rows, or
  `git show-ref` in the colocated `.git`) and the export op is published (binding side).
- **`test_git_import_matches_cli`** *(headline)*: make a change in the backing git repo out-of-band
  (e.g. `git -C <repo> branch newbranch <sha>` or move HEAD), then `ws.git_import()` vs `jj git import`.
  Assert jj's view picks up the new bookmark on both sides; op published. Use a colocated repo so the
  `.git` is directly manipulable.
- **`test_git_import_noop_returns_none`** / **`test_git_export_noop_returns_none`**: on a freshly-synced
  repo, a second import/export returns `None` and adds no op (or, if you couldn't make detection exact,
  assert `≤ 1` op and document — see §3(b)).

Re-run the **whole** suite — additive `PyWorkspace` methods + one new model; every slice 0–9 test must
stay green (and commit the new golden). Mind the **slice-7 colocated trap**
([[m2-slice7-undo-restore]]): in colocated repos, git HEAD/refs interact with jj reads — read both
sides through the same tool where ids must match, and prefer structural assertions.

---

## 8. Build / verify / report

In the devenv shell:

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint     # ruff check + clippy -D warnings (NOT ruff format)
```

All green (pytest + `cargo test` + clippy + ruff check), then **stop and report at the slice boundary**
before slice 11 (git **fetch/push** — network; subprocess via `gix`; new auth/network error surface —
continuation guide §4 "Slice 11"). Commit on `main` (`Implement M2 slice 10: git interop`); **no AI
attribution** anywhere. After slice 11, M2 is complete → bump pyjutsu to `0.41.0`.

---

## 9. Guardrails (carried)

- **Thin Rust, rich Python:** `remotes` returns plain `Remote` dicts (via a new `RemoteData` in
  `convert.rs`); `git_import`/`git_export` return the published `Operation` dict (or `None`); CRUD
  return `None`. **No `gix`/jj-lib type crosses FFI** — only strings/dicts (the URL is stringified Rust
  side).
- **GIL discipline:** all git I/O is `Send`-heavy → `allow_threads`. The `!Send` `Transaction` used by
  import/export/add/remove/rename is created **and dropped inside one synchronous closure on one
  thread** (sound under `allow_threads`, as in `forget_workspace`); if a `Send`-bound compile error
  appears, drop `allow_threads` for that verb (cheap op-store write). The workspace `Mutex` is held for
  each verb's whole load→act sequence.
- **`rebase_descendants()` after import** (it may abandon commits → rewrites; landmine #1). Export/CRUD
  author no commit rewrite, so they need none.
- **Faithful primitive, simplest form:** `git_import` = `import_head` + `import_refs`(plain options);
  `git_export` = `export_refs`; remotes CRUD = the matching `git::*` calls with the CLI's defaults
  (`push_url=None`, `Tags::None`, `StringExpression::all()`); `set_remote_url` = `set_remote_urls`
  (**no op**). Network `git_fetch`/`git_push`/`git_clone` are **slice 11**. Per-remote tracking,
  `push_url`, tags, and `import --remote`/track refinements are **flagged, not faked into the surface**.
- **Errors:** introduce `GitError ⊂ BackendError` (the §1 decision); `GitImportError`/`GitExportError`/
  `GitRemoteManagementError`/`UnexpectedGitBackendError` → `GitError`; partial export
  (`failed_bookmarks`) → raise `GitError`. Only the error's `Display` crosses FFI. **Pin stays
  `=0.38.0`; add `gix = "=0.78.0"` matching jj-lib's lock; `Cargo.lock` committed; everything through
  devenv** — never bare `cargo`/`maturin`/`pytest`/`jj`. The pinned `jj` 0.38.0 CLI is the differential
  oracle only.

> **Slice-10 traps (grepped against the pinned source + jj-lib's `tests/test_git.rs` while writing this
> guide; re-grep if doubted):** (a) `add_remote` takes **`gix` types** (`fetch::Tags`) → the binding
> must add a **direct `gix = "=0.78.0"`** dep matching jj-lib's lock, or it won't type-check; (b)
> **no public jj-lib remote-URL reader** — go through `get_git_backend(store).git_repo().find_remote()
> .url(Direction::Fetch)` (gix), stringify Rust-side; (c) `set_remote_urls` takes **`&Store`, not
> `&mut MutableRepo`** → no transaction, **no op** (asymmetry that breaks op-count parity vs the other
> CRUD verbs); (d) `import_refs` may **abandon commits** → `rebase_descendants()` before commit, and
> both import/export may move `@` → reuse `finish_op`; (e) `GitImportOptions` has **no `Default`** —
> build `{auto_local_bookmark:false, abandon_unreachable_commits:true, remote_auto_track_bookmarks:
> empty}`. See [[m2-slice9-workspace-mgmt]], [[m2-slice7-undo-restore]].
