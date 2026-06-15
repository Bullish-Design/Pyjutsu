# Pyjutsu M1 — Read Layer · Implementation Guide

> Detailed, self-contained guide for implementing **M1 (the read surface)** of Pyjutsu.
> Canonical spec: `docs/PYJUTSU_CONCEPT.md`. This guide is the concrete, jj-lib-0.38-grounded
> plan on top of it. Read the kickoff prompt (`KICKOFF_PROMPT.md`) first for orientation.

---

## 0. Where things stand (M0 — done, on `main`)

The PyO3/maturin binding is built and proven end to end:

- `Cargo.toml` pins `jj-lib = "=0.38.0"` (edition 2024, MSRV 1.89); `Cargo.lock` committed.
- `pyproject.toml` = maturin mixed layout (`python-source = "python"`,
  `module-name = "pyjutsu._pyjutsu"`, `features = ["pyo3/extension-module"]`). `pyo3 = "0.24"`
  with `abi3-py313` (extension-module kept out of Cargo.toml defaults so `cargo test` links
  libpython; maturin adds it).
- `src/lib.rs` (thin Rust): `version() -> "0.38.0"`, `PyjutsuError` (via `create_exception!`),
  and `#[pyclass] PyWorkspace { inner: Mutex<Workspace> }` with `load`/`name`/`workspace_root`/
  `working_copy`. `working_copy()` returns a **plain dict** `{change_id, commit_id, description}`.
- `python/pyjutsu/`: `Workspace` facade, Pydantic `Commit` + `ChangeId`/`CommitId`,
  `PyjutsuError` subclasses (currently Python-defined — **M1 moves these into Rust**),
  `__init__` version-contract check, `_pyjutsu.pyi`, `py.typed`.
- `tests/`: `tests/diff/jj_cli.py` (pinned-`jj` driver, isolated config), `conftest.py`
  (`scratch_repo` fixture), `test_build.py`, `test_workspace_load.py` (differential id match).
- devenv: pinned `nixpkgs-jj` (jj 0.38.0) + `nixpkgs-python`, Rust toolchain, maturin;
  `nix/pyjutsu.nix` tasks (`pyjutsu:build`/`test`/`lint` + `enterTest`).

**Everything runs inside devenv** (`devenv shell -- …`). Verified green: `maturin develop`,
pytest (5), `cargo test` (1), ruff, clippy.

Two key facts already nailed down:
- **change_id** is exposed in jj's **reverse-hex** (z-k letter) form via
  `ChangeId::reverse_hex()` — matches the CLI; **commit_id** is plain `hex()`.
- `jj_lib::Workspace` is `Send` but **not `Sync`** → held behind a `Mutex`. By contrast
  `Arc<ReadonlyRepo>` (the M1 read backend) **is** `Send + Sync` → no `Mutex`, GIL-releasable.

---

## 1. M1 goal

Build the read surface (concept §5, §12): `resolve`, `log`, `bookmarks`, `operations`,
`diff_stat`, `conflicts`, plus `at_operation`/`head_operation`. Reads return Pydantic models
and are **differential-tested against the pinned `jj` 0.38 CLI**. Aim for the cleanest, most
faithful shape from the jump.

Two architecture decisions were **explicitly approved by the user**:

1. **RepoView split.** All reads live on a new `RepoView` backed by `Arc<ReadonlyRepo>`
   (immutable repo at one operation). `Workspace` keeps path identity + (future) mutation;
   its read conveniences delegate to a fresh head view. `ws.at_operation(op)` returns a
   historical `RepoView`.
2. **Reads never mutate (M1).** Reads operate at the chosen operation **without snapshotting**
   (`--ignore-working-copy` is the default and only mode). Explicit `snapshot()` and all
   mutations are **M2**.

---

## 2. Architecture (the spine)

```
pyjutsu (Python, public)
  Workspace ── delegates reads ──▶ RepoView ──▶ Pydantic models
  Workspace.head()/at_operation(op) ──▶ RepoView
_pyjutsu (Rust, thin)
  PyWorkspace { Mutex<Workspace> }     ── head_view()/at_operation()/head_operation()
  PyRepoView  { Arc<ReadonlyRepo>, … }  ── all reads → plain dicts/lists
jj-lib (=0.38.0)
```

Rules held from M0:
- **No jj-lib types cross the FFI.** Reads build **plain Rust intermediate structs**
  (`CommitData`, `BookmarkData`, `OperationData`, `DiffStatData`, `ConflictData`) inside
  `Python::allow_threads`, then convert to dicts/lists after re-acquiring the GIL. Python
  validates them into Pydantic models (drift tripwire: `extra="forbid"`).
- **Thin Rust, rich Python.** No business logic, defaults, or ergonomics in Rust.
- **Panic safety / error mapping**: every fallible path maps jj-lib errors to the right
  `PyjutsuError` subclass.

### Rust module split (was one `lib.rs`)
- `src/lib.rs` — `#[pymodule]` wiring + register classes/exceptions.
- `src/errors.rs` — exception types (`create_exception!`) + `map_*` helpers.
- `src/convert.rs` — intermediate structs + `*_to_pydict` converters.
- `src/workspace.rs` — `PyWorkspace` (load, head_view, at_operation, head_operation, name, root).
- `src/repo_view.rs` — `PyRepoView` + every read.
- `src/revset.rs` — the parse→resolve→evaluate helper.

### Python files
- `python/pyjutsu/repo_view.py` — **new** `RepoView` (one method per read, single conversion point).
- `models.py`, `workspace.py`, `errors.py`, `_pyjutsu.pyi`, `__init__.py` — extended.

---

## 3. Recommended build order (vertical slices, differential-tested each step)

Do them in this order; each is a thin vertical slice with its own differential test before
moving on (concept §7, kickoff "build vertically"):

1. **Plumbing**: error hierarchy → Rust (`src/errors.rs`); module split; `PyRepoView` shell +
   `PyWorkspace.head_view()`; move `working_copy()` onto `PyRepoView`; Python `RepoView` +
   `Workspace.head()`. Keep existing M0 tests green.
2. **`resolve(revset)`** → one `Commit`. Brings up the **revset helper** (the hardest API) on
   the simplest read. Differential vs `jj log -r <revset> -T …`.
3. **`log(revset, limit)`** → `list[Commit]`. Enrich `Commit` (author/committer/parents/
   is_empty/has_conflict/bookmarks) here. Differential vs `jj log`.
4. **`operations(limit)` + `operation()` + `head_operation()` + `at_operation(op)`**. Op-log
   reads + historical view. Differential vs `jj op log`.
5. **`bookmarks()`** → `list[Bookmark]`. Differential vs `jj bookmark list --all-remotes`.
6. **`conflicts(revset)`** → `list[Conflict]` (+ `has_conflict` already on `Commit`).
7. **`diff_stat(revset)`** → `DiffStat`. The implementation-risk item (see §6).
8. Golden fixtures + final sweep (lint, full differential run, op-count invariant test).

---

## 4. Verified jj-lib 0.38 API reference (use these — they're confirmed in the source)

Source: `~/.cargo/registry/src/index.crates.io-*/jj-lib-0.38.0/src/`. Paths/line numbers are
from that tree (approximate but accurate to 0.38.0).

### Repo / view / store  (`repo.rs`, `view.rs`, `store.rs`)
- `use jj_lib::repo::Repo;` — needed for `repo.store()` / `repo.view()` on `Arc<ReadonlyRepo>`.
- `repo.view() -> &View`; `repo.store() -> &Arc<Store>`; `repo.operation() -> &Operation`.
- `repo.index()` is used internally by revset `evaluate`.
- `view.get_wc_commit_id(name: &WorkspaceName) -> Option<&CommitId>` (view.rs:58);
  `view.wc_commit_ids() -> &BTreeMap<WorkspaceNameBuf, CommitId>` (view.rs:54).
- `store.get_commit(&CommitId) -> BackendResult<Commit>` (store.rs:151; receiver `&Arc<Self>`).
- `RepoLoader`: `repo_loader().load_at_head() -> Result<Arc<ReadonlyRepo>, RepoLoaderError>`
  (repo.rs:756); `load_at(&Operation) -> …` (repo.rs:767); `op_store() -> &Arc<dyn OpStore>`
  (repo.rs:744); `root_operation() -> Operation` (repo.rs:792); `store() -> &Arc<Store>`.

### Commit  (`commit.rs`)
- `id() -> &CommitId` (102); `change_id() -> &ChangeId` (166); `parent_ids() -> &[CommitId]`;
  `parents() -> impl Iterator<Item = BackendResult<Commit>>` (110); `description() -> &str` (174).
- `author() -> &Signature` (178); `committer() -> &Signature` (182).
- `is_empty(&self, repo: &dyn Repo) -> BackendResult<bool>` (155).
- `has_conflict() -> bool` (162); `tree() -> MergedTree` (124); `tree_ids() -> &Merge<TreeId>` (132).

### Ids  (`object_id.rs`, `backend.rs`)
- `use jj_lib::object_id::ObjectId;` → `.hex() -> String` for `CommitId`/`OperationId`.
- `ChangeId::reverse_hex() -> String` (backend.rs:64) — **use this for change_id** (z-k form).

### Signature / Timestamp  (`backend.rs`)
- `Signature { name: String, email: String, timestamp: Timestamp }` (140).
- `Timestamp { timestamp: MillisSinceEpoch, tz_offset: i32 }` (86). `MillisSinceEpoch(pub i64)`.
  Emit `{name, email, timestamp_ms: i64, tz_offset_minutes: i32}`; build the `datetime` in Python.

### Revset pipeline  (`revset.rs`) — the hardest API; here is the full recipe
Construct context (mirrors jj-lib's own test helper at revset.rs:3626):
```rust
use std::collections::HashMap;
use jj_lib::revset::{
    self, RevsetAliasesMap, RevsetExtensions, RevsetDiagnostics,
    RevsetParseContext, RevsetWorkspaceContext, SymbolResolver, SymbolResolverExtension,
};
use jj_lib::repo_path::RepoPathUiConverter;

let aliases = RevsetAliasesMap::new();
let extensions = RevsetExtensions::default();
let path_converter = RepoPathUiConverter::Fs { cwd: root.clone(), base: root.clone() }; // workspace_root
let ws_ctx = RevsetWorkspaceContext { path_converter: &path_converter, workspace_name };
let ctx = RevsetParseContext {
    aliases_map: &aliases,
    local_variables: HashMap::new(),
    user_email: &user_email,
    date_pattern_context: chrono::Utc::now().fixed_offset().into(),
    default_ignored_remote: Some("git".as_ref()),   // &RemoteName
    use_glob_by_default: false,
    extensions: &extensions,
    workspace: Some(ws_ctx),
};
let mut diags = RevsetDiagnostics::new();
let expr = revset::parse(&mut diags, revset_str, &ctx)?;                 // Arc<UserRevsetExpression>
let no_ext: &[Box<dyn SymbolResolverExtension>] = &[];
let resolver = SymbolResolver::new(repo, no_ext);                        // repo: &dyn Repo
let resolved = expr.resolve_user_expression(repo, &resolver)?;          // Arc<ResolvedRevsetExpression>
let revset = resolved.evaluate(repo)?;                                   // Box<dyn Revset>
// iterate:
use jj_lib::revset::RevsetIteratorExt as _;
for c in revset.iter().commits(repo.store()) { let commit = c?; /* build CommitData */ }
```
- `parse` (revset.rs:1406); `resolve_user_expression` (655); `evaluate` (666); `Revset::iter`
  (3373); `RevsetIteratorExt::commits` (3414).
- **Errors**: `RevsetParseError` / `RevsetResolutionError` / `RevsetEvaluationError` → map to
  **`RevsetError`**.
- For `resolve(revset)`: collect commits; require exactly one (else `RevsetError`).
- Wrap evaluation+collection in `py.allow_threads(|| …)`; build PyObjects after.

### Bookmarks  (`view.rs`, `op_store.rs`)  — prefer the explicit accessors
- `view.local_bookmarks() -> impl Iterator<Item = (&RefName, &RefTarget)>` (view.rs:140).
- `view.all_remote_bookmarks() -> impl Iterator<Item = (RemoteRefSymbol, &RemoteRef)>` (view.rs:192).
- `view.local_bookmarks_for_commit(id) -> …` (view.rs:149) — for `Commit.bookmarks`.
- `RefName::as_str()`. `RemoteRefSymbol { name, remote }` (each has `.as_str()`-ish access).
- `RefTarget` (op_store.rs:62): `as_normal() -> Option<&CommitId>` (104),
  `added_ids() -> impl Iterator<&CommitId>` (128), `removed_ids()` (124); **conflicted** when
  `added_ids().count() > 1`. Emit `target_ids: Vec<String>` (hex of `added_ids`).
- `RemoteRef { target: RefTarget, state: RemoteRefState }` (op_store.rs:139);
  `is_tracked() -> bool` (172); `RemoteRefState { New, Tracking }` (191).
- Model: one `Bookmark` row per local bookmark (`remote=None`) and one per remote ref.

### Operations  (`operation.rs`, `op_store.rs`, `op_walk.rs`)
- `op.id() -> &OperationId` (97; `.hex()`); `op.parent_ids() -> &[OperationId]` (105);
  `op.parents() -> impl ExactSizeIterator<Item = OpStoreResult<Operation>>` (109);
  `op.metadata() -> &OperationMetadata` (122).
- `OperationMetadata { time: TimestampRange, description: String, hostname: String,
  username: String, tags: HashMap<String,String> }` (op_store.rs:417). `TimestampRange { start,
  end }` of `Timestamp` — **confirm field names during impl** (likely `start`/`end`).
- List: `op_walk::walk_ancestors(&[head_op]) -> impl Iterator<…>` (op_walk.rs:257) starting from
  `repo.operation().clone()`; apply `limit`.
- `at_operation(op_str)`: resolve via `op_walk::resolve_op_with_repo(repo, op_str)` (op_walk.rs:111)
  or `resolve_op_for_load` (89) → `Operation`, then `repo_loader.load_at(&op)`. **Confirm which
  resolver fits**; both exist.

### Conflicts  (`merged_tree.rs`, `merge.rs`)
- `commit.has_conflict() -> bool` (fast flag, already on `Commit`).
- `commit.tree().conflicts() -> impl Iterator<Item = (RepoPathBuf, BackendResult<MergedTreeValue>)>`
  (merged_tree.rs:193). `MergedTreeValue = Merge<Option<TreeValue>>`.
- `Merge<T>` (merge.rs:198): `num_sides() -> usize` (338), `adds() -> ExactSizeIterator<&T>` (288),
  `removes() -> ExactSizeIterator<&T>` (283). Emit `{path, num_sides, num_bases}`
  (`num_bases = removes().count()`). Path → string: use `RepoPath`'s internal-string accessor
  (e.g. `as_internal_file_string()`) — **confirm method name during impl**.

### Diff (for diff_stat)  (`merged_tree.rs`, `diff.rs`) — see §6 for the recipe + risk
- `MergedTree::diff_stream(&other, &matcher)` (merged_tree.rs:276) → async stream of
  `(RepoPathBuf, BackendResult<(before: MergedTreeValue, after: MergedTreeValue)>)`.
  Use `jj_lib::matchers::EverythingMatcher`. Drive the stream with `pollster`'s `block_on`
  (jj-lib already depends on it; see `.block_on()` use in `workspace.rs`).
- Line counts per file: `jj_lib::diff::diff(before_bytes, after_bytes)` (diff.rs:1016) → hunks;
  count inserted/removed lines. Parent tree: merge the commit's parents' trees — **find the
  helper** (`merge_commit_trees` in `rewrite.rs`, or build from `commit.parents()` trees); for a
  root commit, the "before" is the empty tree.

### Exceptions  (`pyo3`)
```rust
create_exception!(_pyjutsu, PyjutsuError, pyo3::exceptions::PyException);
create_exception!(_pyjutsu, RevsetError,   PyjutsuError);
create_exception!(_pyjutsu, ConflictError, PyjutsuError);
create_exception!(_pyjutsu, BackendError,  PyjutsuError);
create_exception!(_pyjutsu, WorkspaceError,PyjutsuError);
// register: m.add("RevsetError", m.py().get_type::<RevsetError>())?; … (one per type)
```
`errors.py` then becomes `from ._pyjutsu import PyjutsuError, RevsetError, …` (re-export).

---

## 5. Pydantic models (`python/pyjutsu/models.py`)

All Pydantic v2, `model_config = ConfigDict(frozen=True, extra="forbid")`.

- `Signature(name: str, email: str, timestamp: datetime)` — a validator builds a tz-aware
  `datetime` from `{timestamp_ms, tz_offset_minutes}` (e.g.
  `datetime.fromtimestamp(ms/1000, tz=timezone(timedelta(minutes=off)))`).
- `Commit` (extend M0): `change_id: ChangeId`, `commit_id: CommitId`, `description: str`,
  `author: Signature`, `committer: Signature`, `parent_ids: list[CommitId]`, `is_empty: bool`,
  `has_conflict: bool`, `bookmarks: list[str]`.
- `Bookmark(name: str, remote: str | None, target_ids: list[CommitId], tracked: bool)`.
- `Operation(id: str, parent_ids: list[str], description: str, tags: dict[str, str],
  start_time: datetime, end_time: datetime, hostname: str, username: str)`.
- `DiffStat(files: list[FileStat], total_insertions: int, total_deletions: int)` and
  `FileStat(path: str, insertions: int, deletions: int)`.
- `Conflict(path: str, num_sides: int, num_bases: int)`.

`__init__.py`: export the new models + Rust-defined exceptions; keep the version-contract check.

---

## 6. diff_stat — the implementation-risk item

jj-lib has **no `DiffStat`** type (the CLI computes it). Compute in Rust:
1. Resolve `revset` to one commit `c`; `to_tree = c.tree()`; `from_tree` = merged parent tree
   (empty tree for a root commit).
2. `from_tree.diff_stream(&to_tree, &EverythingMatcher)`, `block_on` the stream.
3. Per changed path: read before/after file contents (skip/zero for non-file or binary/non-UTF-8),
   run `diff::diff` and count inserted/removed lines → `FileStat`. Sum totals.
4. Differential-test against `jj diff -r <rev> --stat` (parse its totals line).

**Fallback (decide by whether the differential test passes):** if exact line parity is fiddly
for M1, ship file-level granularity (changed-file list + add/modify/delete kind, line counts 0)
and defer exact line counts to M1.1. Don't let diff_stat block the rest of M1 — it's last in
the build order for this reason.

---

## 7. Testing (differential-first, concept §7)

- Extend `tests/diff/jj_cli.py` with typed readers: `change_id`, `commit_id`, author/committer
  via templates, `bookmarks`, `op log` (id/description/time), `diff --stat`. Keep the isolated
  `JJ_CONFIG`.
- New fixtures in `tests/conftest.py`: a **linear history** (3–4 commits), a **bookmarked** repo
  (local bookmark; optionally a remote via a second colocated git for tracking state), and a
  **conflict** repo (two diverging single-file edits, `jj new A B` to make `@` conflicted).
- New test modules (one per read): `test_resolve.py`, `test_log.py`, `test_operations.py`,
  `test_at_operation.py`, `test_bookmarks.py`, `test_conflicts.py`, `test_diff_stat.py`.
  Each asserts the model equals what the pinned `jj` reports.
- **Op-count invariant** test: capture `jj op log` length, run several reads, assert it's
  unchanged — proves the reads-never-mutate contract.
- **Golden fixtures** (`tests/golden/`): committed JSON for `Commit` and `Operation` shapes,
  regenerated against the pin (guards model-shape drift). Differential tests guard *values*;
  golden guards *shape*.
- `cargo test`: unit-test the revset helper (parse a couple of revsets) and a `convert` round-trip.

---

## 8. Verification (all inside devenv)

```sh
devenv shell -- devenv tasks run pyjutsu:build      # maturin develop
devenv shell -- devenv tasks run pyjutsu:test       # pytest (all differential) + cargo test
devenv shell -- devenv tasks run pyjutsu:lint       # ruff + clippy -D warnings
```
Manual smoke on a scratch repo: `ws.log("::@", limit=10)`, `ws.resolve("@")`, `ws.bookmarks()`,
`ws.operations()`, `ws.at_operation(op).working_copy()`, `ws.diff_stat("@")`, `ws.conflicts("@")`
— compared to the equivalent `jj` commands.

---

## 9. Guardrails (unchanged)

No subprocess/CLI backend, no old-Pyjutsu compat. No jj-lib types across the FFI; no business
logic in Rust. No workflow policy (lanes/frozen trunk) — faithful primitives only. Everything
through devenv. No AI-attribution in commits/PRs/docs. Pin stays `=0.38.0`.

## 10. Out of scope (→ M2)

`snapshot()` and all mutations (`new`/`describe`/`edit`/`abandon`/`rebase`/`squash`/bookmark
writes); `undo`/`restore_operation`. M1 is read-only; `at_operation` reads history, writes nothing.
