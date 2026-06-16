# Pyjutsu M2 — Slice 9 Implementation Guide (workspace management: `init` / `add_workspace` / `forget_workspace` / `workspaces`)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec; surface list at §124–135) →
> `M2_CONTINUATION_GUIDE.md` (the corrected spine for slices 2–11; §4 "Slice 9") → **this document**
> (the detailed, verified plan for slice 9) → `M2_IMPLEMENTATION_GUIDE.md` (original plan/error
> taxonomy) → the code it produces.
>
> **Pin unchanged:** `jj-lib = "=0.38.0"` (default features include `git`, confirmed
> `jj-lib-0.38.0/Cargo.toml:39`). Slices 0–8 are committed and pushed on `main` (slice 8 = `f9e6b3f`);
> the working tree is clean — start from `main`. Slices 0–1 shipped as pyjutsu `0.40.0`; slices 2+
> ride under `0.40.0` until the **completed** M2 bumps to `0.41.0`. Every API ref below is `file:line`
> into `~/.cargo/registry/.../jj-lib-0.38.0/src/`, **verified against the pinned source while writing
> this guide** (and cross-checked against jj-lib's own `tests/test_workspace.rs`).

---

## 0. Where slice 9 starts

Slices 0–8 built the whole in-`Transaction` mutation surface plus the op-log/stale/snapshot
workspace verbs. This slice is the **workspace-lifecycle** surface (concept §124): creating repos and
managing *secondary* workspaces — the multiple-working-copy feature that is jj's headline over git.
Unlike slices 5–8 these are **`PyWorkspace`-level** (not `Transaction` methods), and unlike the op-log
writes they mostly **delegate to one jj-lib `Workspace::*` constructor that publishes its own
operation** — so there is little hand-rolled transaction code.

Four verbs + one model:

1. **`Workspace.init(path, *, colocate=False) -> Workspace`** *(staticmethod)* — create a brand-new
   jj repo + default workspace at `path`. `colocate=False` ⇒ internal git store (`.jj/repo/store/git`);
   `colocate=True` ⇒ a colocated `.git` sharing the working copy. Matches `jj git init` /
   `jj git init --colocate`.
2. **`Workspace.add_workspace(path, *, name=None) -> WorkspaceInfo`** — add a **secondary** workspace
   rooted at `path`, sharing this repo's store. Matches `jj workspace add` (with the `@`-placement
   caveat in §1). `name` defaults to the destination directory's basename (the CLI default).
3. **`Workspace.forget_workspace(name) -> None`** — stop tracking workspace `name`'s working-copy
   commit in the repo (the on-disk files are left untouched). Matches `jj workspace forget <name>`.
4. **`Workspace.workspaces() -> list[WorkspaceInfo]`** — list all workspaces tracked in the repo
   view. Matches `jj workspace list`.
5. **New model `WorkspaceInfo` `{name, path, wc_commit_id}`** (`python/pyjutsu/models.py`), with a
   golden fixture regenerated.

---

## 1. The two facts that shape this slice (verified in source + jj-lib's own tests)

**(a) `init_workspace_with_existing_repo` does almost everything — including publishing the op.**
`Workspace::init_workspace_with_existing_repo(workspace_root, repo_path, repo, working_copy_factory,
workspace_name)` (`workspace.rs:358`) creates the new workspace's `.jj` dir + repo pointer, then calls
the private `init_working_copy` (`workspace.rs:134`), which **starts a transaction, `check_out`s a
fresh empty commit on `root()` for the new workspace name, and commits an operation named
`add workspace '<name>'`** (`workspace.rs:144‑147`), then initializes the (empty) working-copy state at
the new path. It returns `(Workspace, Arc<ReadonlyRepo>)` where the returned repo's view **already has
the new workspace's `@`** registered. jj-lib's own `tests/test_workspace.rs:46` (`test_init_additional_workspace`)
asserts exactly this: after the call, `repo.view().get_wc_commit_id(&ws2_name)` is `Some`, and that
commit's parent is `root()`. **So `add_workspace` does not hand-roll a transaction or a checkout** —
it calls this one constructor (off the GIL) and reads the result back. The on-disk working copy at the
new path is the empty root tree, so there are no files to write (nothing like slice 2's checkout).

**(b) The `@`-placement divergence from `jj workspace add` (test against `-r 'root()'`).** The
primitive puts the new `@` on **`root()`**. The CLI's `jj workspace add` *default* instead bases the
new `@` on **the current workspace's `@`'s parents** ("they will share the same parent(s)",
`jj workspace add --help`). Since a fresh workspace commit also gets a **random change id** (like
`tx.new`, [[m2-slice2-new-checkout]]), commit-id parity is impossible regardless. So:
- Implement the **faithful primitive** (empty `@` on `root()`) and **document** that the CLI's default
  bases it on the current `@`'s parents — the `-r <revs>` placement is the out-of-scope refinement
  (flag it in the docstring; don't fake a half-version by hand-rebasing).
- For the differential test, drive the CLI with **`jj workspace add --name <n> -r 'root()' <path>`**,
  which reproduces the primitive's placement, and compare **structure** (new name present in
  `wc_commit_ids` on both sides; new `@` empty with parent `root()`; one new op each), the way
  `test_new` compares structure rather than ids.

---

## 2. Verified jj-lib APIs (against the pinned 0.38.0 source)

| What | Signature / fact | Ref |
|---|---|---|
| **Init internal-git** | `Workspace::init_internal_git(&UserSettings, &Path) -> Result<(Workspace, Arc<ReadonlyRepo>), WorkspaceInitError>` (`#[cfg(feature="git")]`) | workspace.rs:205 |
| **Init colocated** | `Workspace::init_colocated_git(&UserSettings, &Path) -> Result<(Workspace, Arc<ReadonlyRepo>), WorkspaceInitError>` | workspace.rs:221 |
| **Add secondary ws** | `Workspace::init_workspace_with_existing_repo(workspace_root: &Path, repo_path: &Path, repo: &Arc<ReadonlyRepo>, &dyn WorkingCopyFactory, WorkspaceNameBuf) -> Result<(Workspace, Arc<ReadonlyRepo>), WorkspaceInitError>` — **publishes its own `add workspace '<name>'` op**; new `@` is empty on `root()` | workspace.rs:358 / 134‑147 |
| Default factory (one) | `default_working_copy_factory() -> Box<dyn WorkingCopyFactory>` (note: **singular**; `load` uses the plural `default_working_copy_factories()`) | workspace.rs:623 / 614 |
| Workspace accessors | `Workspace::{workspace_root() -> &Path, workspace_name() -> &WorkspaceName, repo_path() -> &Path, repo_loader() -> &RepoLoader}` | workspace.rs:402/406/411/414 |
| **Forget ws** | `MutableRepo::remove_wc_commit(&mut self, &WorkspaceName) -> Result<(), EditCommitError>` (abandons the ws's `@` if discardable, then drops the view entry) | repo.rs:1470 |
| Rename ws (later) | `MutableRepo::rename_workspace(&mut self, &WorkspaceName, WorkspaceNameBuf) -> Result<(), RenameWorkspaceError>` | repo.rs:1506 |
| View: list ws | `View::wc_commit_ids(&self) -> &BTreeMap<WorkspaceNameBuf, CommitId>` | view.rs:54 |
| View: one ws | `View::get_wc_commit_id(&self, &WorkspaceName) -> Option<&CommitId>` | view.rs:58 |
| **Ws → path** | `WorkspaceStore::get_workspace_path(&self, &WorkspaceName) -> Result<Option<PathBuf>, WorkspaceStoreError>`; impl `SimpleWorkspaceStore::load(repo_path: &Path) -> Result<Self, _>` | workspace_store.rs:63 / 100 |
| Init error | `enum WorkspaceInitError` (impls `Display`) → `map_workspace_err` (`WorkspaceError`) | errors.rs:47 |

Notes verified in source:

- **`init_*` need `&UserSettings`.** Reuse the binding's existing `load_user_settings(&path)`
  (`src/workspace.rs:43`) — at init time the repo config doesn't exist yet, so it loads `JJ_CONFIG` +
  built-in defaults (the repo layer is silently skipped, `src/workspace.rs:65‑70`). That is exactly
  what makes the binding share the CLI's pinned identity for any commit it later authors.
- **`init_*` and `init_workspace_with_existing_repo` return `(Workspace, Arc<ReadonlyRepo>)`.** For
  `init` you wrap the returned `Workspace` in a fresh `PyWorkspace` (same shape as `load`: new `Mutex`,
  `user_email` from settings, fresh `tx_open`). For `add_workspace` you **discard** the returned new
  `Workspace` (it's the *secondary* one, which the caller will `Workspace.load(path)` separately) and
  read the new `@` out of the returned repo's view to build the `WorkspaceInfo` row.
- **`forget` maps `EditCommitError`.** `remove_wc_commit` returns `EditCommitError`; the existing
  `map_edit_err` (`src/errors.rs:69`) already turns `RewriteRootCommit` → `ImmutableCommitError` and
  the rest → `BackendError`, which is the right mapping here too. Run `rebase_descendants()` after it
  (the abandon of a discardable `@` registers a rewrite, [[m2-slice7-undo-restore]]) and let `commit`
  re-run it idempotently.
- **`WorkspaceStore` listing.** The trait (workspace_store.rs:46) has `get_workspace_path(name)` and
  `add`/`forget`/`rename` but **no list-all** — enumerate names from `view.wc_commit_ids()` and call
  `get_workspace_path` per name. **Verify `jj_lib::workspace_store::{SimpleWorkspaceStore,
  WorkspaceStore}` are publicly exported** (grep `lib.rs` for `pub mod workspace_store`). If they are
  **not** re-exported, ship `WorkspaceInfo` as `{name, wc_commit_id, path: None}` for now (fill the
  current workspace's own path from `workspace_root()`) and flag path-for-all-workspaces as a
  follow-up — don't block the slice on it.

Imports to add to `src/workspace.rs` (extend the existing `use jj_lib::workspace::{...}`):
`default_working_copy_factory` (singular). Add `init`-related nothing else — `Workspace`,
`UserSettings`, `ReadonlyRepo`, `WorkspaceName` are already in scope. For `forget` you need
`map_edit_err` from `crate::errors` (add to the existing `use`). For paths (if exported)
`use jj_lib::workspace_store::{SimpleWorkspaceStore, WorkspaceStore};`.

---

## 3. Rust: `#[pymethods]` on `PyWorkspace` (`src/workspace.rs`)

All four slot into the existing `#[pymethods] impl PyWorkspace`. Sketches (adapt to the surrounding
dense-comment style; lock discipline + off-GIL exactly like `load`/`snapshot`):

```rust
/// Create a new jj repo + default workspace at `path`, returning a handle to it. `colocate=false`
/// uses an internal git store (`.jj/repo/store/git`); `colocate=true` colocates a `.git` sharing
/// the working copy. Matches `jj git init` / `jj git init --colocate`. I/O-heavy → off the GIL.
#[staticmethod]
#[pyo3(signature = (path, colocate=false))]
fn init(py: Python<'_>, path: PathBuf, colocate: bool) -> PyResult<Self> {
    let settings = load_user_settings(&path)?;
    let user_email = settings.user_email().to_owned();
    let (workspace, _repo) = py
        .allow_threads(|| {
            if colocate {
                Workspace::init_colocated_git(&settings, &path)
            } else {
                Workspace::init_internal_git(&settings, &path)
            }
        })
        .map_err(map_workspace_err)?;
    Ok(Self {
        inner: Mutex::new(workspace),
        user_email,
        tx_open: Arc::new(AtomicBool::new(false)),
    })
}

/// Add a secondary workspace rooted at `path`, sharing this repo's store; returns its `WorkspaceInfo`
/// (name + path + the fresh empty `@` on `root()`). `name` defaults to `path`'s basename. Publishes
/// one `add workspace '<name>'` operation (jj-lib does this internally). Matches `jj workspace add`
/// — but the new `@` lands on `root()` here; the CLI's default bases it on the current `@`'s parents
/// (the `-r <revs>` placement is the out-of-scope refinement).
#[pyo3(signature = (path, name=None))]
fn add_workspace<'py>(&self, py: Python<'py>, path: PathBuf, name: Option<&str>)
    -> PyResult<Bound<'py, PyDict>>
{
    let guard = self.locked()?;
    let repo_path = guard.repo_path().to_owned();
    let name_buf = WorkspaceNameBuf::from(match name {
        Some(n) => n.to_owned(),
        None => path.file_name()/*OsStr*/.and_then(|s| s.to_str())
            .ok_or_else(|| PyjutsuError::new_err("workspace path has no valid basename"))?
            .to_owned(),
    });
    // Load this repo at head, then let jj-lib create the new workspace (+ its op). The new `@` is
    // an empty commit on root; there are no files to check out. All `Send` → off the GIL.
    let loader = guard.repo_loader();
    let (new_id, new_root) = py.allow_threads(|| -> PyResult<_> {
        let repo = loader.load_at_head().map_err(map_backend_err)?;
        let factory = default_working_copy_factory();
        let (new_ws, new_repo) = Workspace::init_workspace_with_existing_repo(
            &path, &repo_path, &repo, &*factory, name_buf.clone(),
        ).map_err(map_workspace_err)?;
        let wc_id = new_repo.view().get_wc_commit_id(&name_buf)
            .ok_or_else(|| PyjutsuError::new_err("new workspace has no working-copy commit"))?
            .hex();
        Ok((wc_id, new_ws.workspace_root().to_owned()))
    })?;
    WorkspaceInfoData::new(name_buf.as_str(), &new_root, &new_id).to_dict(py)
}

/// Stop tracking workspace `name`'s working-copy commit in the repo (the on-disk files are left
/// untouched), publishing one operation. Matches `jj workspace forget <name>`. Errors with
/// `PyjutsuError` if no workspace `name` is tracked.
fn forget_workspace(&self, py: Python<'_>, name: &str) -> PyResult<()> {
    let guard = self.locked()?;
    let name_buf = WorkspaceNameBuf::from(name.to_owned());
    let loader = guard.repo_loader();
    py.allow_threads(|| -> PyResult<_> {
        let repo = loader.load_at_head().map_err(map_backend_err)?;
        if repo.view().get_wc_commit_id(&name_buf).is_none() {
            return Err(PyjutsuError::new_err(format!("no such workspace '{name}'")));
        }
        let mut tx = repo.start_transaction();           // !Send Transaction stays inside this closure
        tx.repo_mut().remove_wc_commit(&name_buf).map_err(map_edit_err)?;
        tx.repo_mut().rebase_descendants().map_err(map_backend_err)?;
        tx.commit(format!("forget workspace '{name}'")).map_err(map_backend_err)?;
        Ok(())
    })
}

/// List all workspaces tracked in the repo view (name + path + `@` commit id). Matches
/// `jj workspace list`. Read-only.
fn workspaces<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let guard = self.locked()?;
    let repo_path = guard.repo_path().to_owned();
    let loader = guard.repo_loader();
    let rows = py.allow_threads(|| -> PyResult<_> {
        let repo = loader.load_at_head().map_err(map_backend_err)?;
        let store = SimpleWorkspaceStore::load(&repo_path).map_err(map_workspace_err)?;
        repo.view().wc_commit_ids().iter()
            .map(|(name, id)| {
                let path = store.get_workspace_path(name).map_err(map_workspace_err)?; // Option
                Ok(WorkspaceInfoData::new(name.as_str(), path.as_deref(), &id.hex()))
            })
            .collect::<PyResult<Vec<_>>>()
    })?;
    rows.iter().map(|r| r.to_dict(py)).collect()
}
```

**Verify while implementing (don't assume — the slice-5/-7 lesson):**
- **`load_at_head` borrows the loader, which borrows the `MutexGuard`.** In `snapshot`/`undo` the repo
  is loaded inside the held lock and the heavy work runs in `allow_threads` *after* dropping the
  borrow — but here the `Workspace::init_*` calls happen **inside** `allow_threads` while the guard is
  still held on the GIL thread. That's fine (the guard isn't moved into the closure; only `loader`/
  `repo_path` are, and `loader: &RepoLoader` is `Send`+borrowed for the closure's duration). If the
  borrow checker fights the `&dyn WorkingCopyFactory` lifetime, bind `let factory = …;` *outside*
  `allow_threads` and move a `&*factory` in.
- **`!Send` `Transaction` inside `allow_threads`.** In `forget`/`add` the transaction is created **and
  dropped within the same synchronous closure on one worker thread** — it never crosses a thread
  boundary, so `allow_threads` is sound (same as how `init_working_copy` runs internally). If PyO3's
  `Send` bound on the closure complains because something non-`Send` is captured, pull the tx work
  back onto the GIL (drop `allow_threads` for `forget` — it's cheap: one op-store write).
- **`WorkspaceNameBuf::from`** — confirm the `From<String>`/`From<&str>` ctor and `as_str()` /
  `as_symbol()` (used by jj-lib's own op description, workspace.rs:147). The op description text isn't
  asserted by tests, so either is fine; match jj-lib's `'<name>'` wording if you want byte-equality.
- **`default_working_copy_factory()` lifetime** — it returns an owned `Box`; keep it alive across the
  `init_workspace_with_existing_repo` call (bind to a `let`, pass `&*factory`).
- Confirm **`SimpleWorkspaceStore`/`WorkspaceStore` are exported** (§2 note). If not, degrade
  `workspaces()`/`add_workspace` path to `None` + the current `workspace_root()` and flag it.

---

## 4. Python: model, facade, stubs

### `python/pyjutsu/models.py` — new `WorkspaceInfo`

```python
class WorkspaceInfo(BaseModel):
    """A workspace tracked in the repo: its name, on-disk root, and current ``@`` commit id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    #: Absolute path to the workspace's working-copy root; ``None`` if not recorded in the store.
    path: str | None
    #: The commit id of this workspace's ``@`` (working-copy commit).
    wc_commit_id: CommitId
```

Add `WorkspaceInfo` to `models.py`'s `__all__`/exports and re-export from `python/pyjutsu/__init__.py`
alongside `Commit`/`Bookmark`/`Operation`. Add a **golden fixture** for it (see `tests/golden/` +
`tests/test_golden.py`; regenerate the way the existing models do).

### `src/convert.rs` — `WorkspaceInfoData`

Mirror `BookmarkData`: a plain struct `{name: String, path: Option<String>, wc_commit_id: String}`
with `new(name: &str, path: Option<&Path>, wc_commit_id: &str)` (lossy-stringify the path) and
`to_dict`. Keep it pure-data (no jj-lib types cross).

### `python/pyjutsu/workspace.py` (the pure-Python `Workspace` facade)

Add `init` (classmethod), `add_workspace`, `forget_workspace`, `workspaces` wrapping the native
handle and validating into models. Match the existing facade's docstring density. Example shapes:

```python
@classmethod
def init(cls, path: str | os.PathLike[str], *, colocate: bool = False) -> Workspace:
    """Create a new jj repo + default workspace at ``path`` → a :class:`Workspace` (``jj git init``)."""
    return cls(PyWorkspace.init(os.fspath(path), colocate))

def add_workspace(self, path, *, name: str | None = None) -> WorkspaceInfo:
    """Add a secondary workspace at ``path`` → its :class:`WorkspaceInfo` (``jj workspace add``).

    The new ``@`` is an empty commit on the root commit; the CLI's default instead shares the
    current workspace's parents (out-of-scope refinement). ``name`` defaults to ``path``'s basename.
    """
    return WorkspaceInfo.model_validate(self._handle.add_workspace(os.fspath(path), name))

def forget_workspace(self, name: str) -> None: ...
def workspaces(self) -> list[WorkspaceInfo]: ...
```

> **Check the existing `Workspace` facade** (`python/pyjutsu/workspace.py`): how it stores the native
> handle (`self._handle`?) and how `load` is written, so `init` mirrors it. The native `load` is a
> `@staticmethod` returning `PyWorkspace`; `PyWorkspace.init` is the same shape.

### `python/pyjutsu/_pyjutsu.pyi` (in `PyWorkspace`)

```python
    @staticmethod
    def init(path: str | os.PathLike[str], colocate: bool = ...) -> PyWorkspace: ...
    def add_workspace(self, path: str | os.PathLike[str], name: str | None = ...) -> dict[str, object]: ...
    def forget_workspace(self, name: str) -> None: ...
    def workspaces(self) -> list[dict[str, object]]: ...
```

---

## 5. Differential tests (`tests/test_workspace_mgmt.py`)

Reuse the harness (`_copy_repo`, the `jj`/`scratch_repo`/`linear_repo` fixtures, `jj.change_ids`,
`jj.op_log_ids`, `jj.commit_id`). A new workspace's `@` has a **random change id**, so — like
`test_new` — assert **structure**, not commit-id parity. Add a small `JjCli` helper if useful:
`workspaces(repo) -> set[str]` via `jj workspace list -T 'name ++ "\n"'` (verify the template field
name with `jj workspace list --help`/`-T`; fall back to parsing default output if templating a
workspace name isn't supported in 0.38).

Suggested cases:

- **`test_init_creates_loadable_repo`** *(internal git)*: `pyjutsu.Workspace.init(tmp_path/"r")` into
  an empty dir; assert the dir now has `.jj`, `ws.name() == "default"`, `ws.working_copy().is_empty`,
  and `jj` can read it (`jj.op_log_ids(r)` non-empty / `jj log` succeeds). Compare against
  `jj git init` in a sibling empty dir (same workspace name, same empty `@` shape).
- **`test_init_colocated_creates_git_dir`**: `init(..., colocate=True)` ⇒ both `.jj` and `.git`
  present; matches `jj git init --colocate`.
- **`test_init_existing_repo_raises`**: `init` into a dir that already has a repo → `WorkspaceError`.
- **`test_add_workspace_matches_cli`** *(headline)*: on `scratch_repo` (or `linear_repo`), binding
  `ws.add_workspace(tmp_path/"second", name="second")`; CLI on a copy
  `jj(other, "workspace", "add", "--name", "second", "-r", "root()", str(other_second))`. Assert: the
  returned `WorkspaceInfo.name == "second"`, its `@` is empty with parent `root()`, `"second"` appears
  in the repo's workspace set on **both** sides (binding: `{w.name for w in ws.workspaces()}`; CLI:
  `jj.workspaces(other)`), and exactly **one** new op on each side (the `add workspace` op). The new
  `.jj` exists at the new path. (Drive the CLI with `-r 'root()'` to match the primitive's placement,
  §1(b).)
- **`test_add_workspace_default_name_is_basename`**: `add_workspace(tmp_path/"wsx")` with no `name`;
  assert the tracked name is `"wsx"`.
- **`test_forget_workspace_matches_cli`** *(headline)*: add a second workspace (binding + CLI on a
  copy), then binding `ws.forget_workspace("second")` vs `jj(other, "workspace", "forget", "second")`.
  Assert `"second"` is gone from the workspace set on both sides, the default workspace's `@` is
  unchanged, and one new op each.
- **`test_forget_unknown_workspace_raises`**: `forget_workspace("nope")` → `PyjutsuError`.
- **`test_workspaces_lists_all`**: after adding two, `ws.workspaces()` returns `default` + both, each
  with the right `wc_commit_id` (cross-check `jj.commit_id(repo, name + "@")` if the `<name>@` revset
  resolves; else just assert the set of names and that each id is a valid hex commit present in the
  repo).

Re-run the **whole** suite — these are additive `PyWorkspace` methods + one new model; nothing in
slices 0–8 changes, so every prior test must stay green (and the new golden must be committed).

---

## 6. Build / verify / report

In the devenv shell:

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint
```

All green (pytest + `cargo test` + clippy `-D warnings` + ruff), then **stop and report at the slice
boundary** before slice 10 (git import/export + remotes: `import_refs`/`export_refs`/remotes CRUD +
a `Remote` model — continuation guide §4 "Slice 10"; verify `git.rs:529/983/1103/2098…` at that
slice). Commit on `main` (`Implement M2 slice 9: workspace management`); **no AI attribution** anywhere.

---

## 7. Guardrails (carried)

- **Thin Rust, rich Python:** `add_workspace`/`workspaces` return plain `WorkspaceInfo` dicts (via a
  new `WorkspaceInfoData` in `convert.rs`); `init` returns an opaque `PyWorkspace`. Never leak jj-lib
  types.
- **GIL discipline:** the `init_*` constructors and `init_workspace_with_existing_repo` are I/O-heavy
  and `Send` → run them in `allow_threads`. The `!Send` `Transaction` that `forget` (and jj-lib's own
  `init_working_copy`) uses is created **and dropped inside one synchronous closure on a single
  thread**, so it never crosses a thread boundary — sound under `allow_threads`. If a `Send`-bound
  compile error appears, fall back to running that one closure on the GIL (it's a light op-store
  write), exactly as the module's other `!Send` paths do.
- **Faithful primitive, simplest form:** `init` = `init_internal_git`/`init_colocated_git`;
  `add_workspace` = `init_workspace_with_existing_repo` (empty `@` on root — **the CLI's `-r` parent
  placement and `--sparse-patterns` inheritance are flagged, not faked**); `forget_workspace` =
  `remove_wc_commit`; `workspaces` = `view.wc_commit_ids()` + `get_workspace_path`. `rename_workspace`
  (repo.rs:1506) is **available but out of scope** for this slice — note it as a follow-up.
- **`rebase_descendants()` after the one rewrite** (`forget`'s discardable-`@` abandon registers a
  rewrite; landmine #1). `init`/`add_workspace` author no rewrite of an existing commit, so they need
  none beyond what jj-lib runs internally.
- **Errors:** `WorkspaceInitError` → `WorkspaceError` (`map_workspace_err`); `EditCommitError` from
  `remove_wc_commit` → `map_edit_err`; unknown-workspace and bad-basename → `PyjutsuError`. Only the
  error's `Display` crosses FFI. **Pin stays `=0.38.0`; `Cargo.lock` committed; everything through
  devenv** — never bare `cargo`/`maturin`/`pytest`/`jj`. The pinned `jj` 0.38.0 CLI is the
  differential oracle only.

> **Slice-9 traps (grepped against the pinned source + jj-lib's `tests/test_workspace.rs` while
> writing this guide; re-grep if doubted):** (a) `init_workspace_with_existing_repo` **publishes its
> own op and sets the new `@` on `root()`** — don't double-add a transaction, and test the CLI with
> `-r 'root()'` for placement parity; (b) `default_working_copy_factory` is **singular** (the plural
> `…factories` is for `load`); (c) the `WorkspaceStore` trait has **no list-all** — enumerate via
> `view.wc_commit_ids()`, and **confirm the store type is exported** before depending on per-workspace
> paths; (d) `remove_wc_commit` returns `EditCommitError` → reuse `map_edit_err`. See
> [[m2-slice2-new-checkout]], [[m2-slice7-undo-restore]].
