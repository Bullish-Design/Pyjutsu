# Pyjutsu — Concept (PyO3 / jj-lib binding)

**Status:** Concept / pre-implementation.
**Name:** Pyjutsu · **Import:** `import pyjutsu`
**What:** A general-purpose, Pythonic + Pydantic binding to **jujutsu's Rust engine
(`jj-lib`)** via **PyO3**, distributed as a compiled wheel (maturin).
**Not:** a gitman component. Gitman is one *consumer*; Pyjutsu must stand alone and be
useful to any Python tool that wants to drive jj programmatically.

---

## 1. Thesis

Today, Python talks to jj by **shelling out to the `jj` CLI** and parsing template/text
output (this is what gitman's `jj.py` and the existing CLI-wrapper Pyjutsu do). That works
but has a hard ceiling: process-per-call overhead, a working-copy snapshot on every
invocation, brittle text/template parsing against an unstable CLI surface, no native
cross-call transaction, and `json()` template limitations that force hand-built JSON.

Pyjutsu removes that ceiling by binding **`jj-lib` in-process**: read the commit graph,
op log, working copy, revsets, and conflicts as native Rust data; perform mutations inside
**`jj-lib`'s real `Transaction`** (one atomic operation), with **no subprocess and no text
parsing**. The cost is a Rust build and tracking `jj-lib`'s (intentionally unstable) API —
which we tame by **pinning the jj version** and **differential-testing against the pinned
`jj` CLI binary**.

## 2. Why PyO3 over the CLI wrapper (and over jj-lib-from-scratch)

| Concern | CLI wrapper (current Pyjutsu/gitman) | PyO3 + jj-lib (this) |
|---|---|---|
| Per-op cost | process spawn + repo load + WC snapshot, ×N | in-process; load repo once, reuse |
| Data access | templates → text → parse → models | native graph access → models |
| `json()` limits | hand-built JSON, scalar-only | none — read fields directly |
| Transactions | simulated via op-id capture + `jj op restore` | **native `jj-lib` transaction** = 1 op |
| Parsing fragility | high (formats move per version) | none (typed Rust API) |
| API stability | CLI is *relatively* stable | `jj-lib` is **unstable** → must pin |
| Build/dist | pure Python | Rust toolchain, maturin wheels |

The instability of `jj-lib` is the price; pinning + differential tests pay it. The CLI
wrapper stays viable as a *fallback backend* (see §9).

## 3. Relationship to the existing Pyjutsu repo

The existing `../../Pyjutsu` (a CLI-wrapper over `sh`/`clinch`) is being **completely
replaced**. There is **no backwards-compatibility requirement** — not for its API, its
models, its method names, or its behavior. Design Pyjutsu fresh around jj-lib and what makes
a clean Pythonic binding; only glance at the old `models.py`/`SPEC.md` if a particular
model shape happens to be convenient, never as a constraint.

## 4. Architecture — three layers

```
┌─ pyjutsu (pure Python, public)  ─ Pydantic models, ergonomic facade, docs, typing ─┐
│      Workspace / Repo / Transaction facades · Commit/Change/Bookmark/Operation/…    │
│      converts native data ↔ Pydantic; owns all ergonomics & validation              │
├─ _pyjutsu (Rust, PyO3 native ext)  ─ THIN ───────────────────────────────────────── │
│      opaque handles: PyWorkspace, PyRepo, PyTransaction                              │
│      returns plain data (dicts/tuples/bytes) for values; no business logic          │
├─ jj-lib (Rust crate, pinned)  ─ the engine ──────────────────────────────────────── │
│      Workspace, ReadonlyRepo/MutableRepo, Commit, RevsetExpression, Transaction,    │
│      OpStore/Operation, LocalWorkingCopy, git backend (gix/git2)                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

**Design rule: keep the Rust layer thin and dumb.** It mediates `jj-lib` ↔ Python with a
*minimal, stable, internal* surface (handles + plain data). All ergonomics, Pydantic
modeling, defaults, and the public contract live in the Python layer. This means:

- jj-lib churn is absorbed almost entirely in the thin Rust layer; the Python public API
  stays stable across jj upgrades.
- The Python layer is where consumers (gitman, others) integrate; it's `pip`-friendly,
  fully typed, and Pydantic-native.

### Value passing across the FFI

- **Stateful things** (`Workspace`, an open `Transaction`) → opaque `#[pyclass]` handles.
- **Values** (a commit, a list of commits, a diff stat) → **plain Python data**
  (dicts/lists of primitives) that the Python layer feeds to `Model.model_validate(...)`.
  This keeps the Rust surface tiny and gives Pydantic validation at the boundary (which
  also catches jj-lib drift). Hot paths (huge logs) may use `TypedDict` fast-construction
  to skip per-row validation — measure first (§10).

## 5. The public API (what a developer sees)

Object-oriented around a `Workspace`. Reads return Pydantic models; mutations happen inside
a transaction context that maps to exactly one jj operation.

```python
from pathlib import Path
from pyjutsu import Workspace, Commit, Bookmark, Operation

ws = Workspace.load(Path("my-repo"))            # or Workspace.init(path, colocate=True)

# --- reads (Pydantic models; no mutation with ignore_working_copy=True) ---
at = ws.working_copy()                           # Commit for @
trunk = ws.resolve("trunk()")                    # single-revision resolve → Commit
hist: list[Commit] = ws.log("trunk()..@", limit=50)
bms: list[Bookmark] = ws.bookmarks()             # local + remote tracking
ops: list[Operation] = ws.operations(limit=20)   # op log (id, time, description, tags)
stat = ws.diff_stat(at.commit_id)                # files / insertions / deletions
conflicts = at.conflicts                          # first-class, N-sided

# --- mutations: one transaction == one jj operation (native, atomic) ---
with ws.transaction("start feature") as tx:
    child = tx.new(parents=[trunk.change_id])
    tx.describe(child, "Add feature")
    tx.set_bookmark("feature", child)
op_id = ws.head_operation()                       # the op the tx produced

# --- undo / time travel (native op log) ---
ws.undo()                                          # revert the last operation
ws.restore_operation(op_id)                        # restore to any op
repo_then = ws.at_operation(op_id)                 # read a historical state

# --- git interop (jj-lib git backend) ---
ws.git_fetch(remote="origin")
ws.git_push(bookmark="feature", remote="origin", allow_new=True)
ws.git_export(); ws.git_import()                   # colocated sync
```

### Surface (v1)

- **Workspace lifecycle:** `init`, `load`, `clone`, `workspaces()`, `add_workspace`,
  `forget_workspace`.
- **Reads:** `working_copy`, `resolve`, `log` (revset + limit), `commit(id)`,
  `bookmarks`, `operations`, `diff_stat`, `diff` (later), conflicts on a `Commit`.
- **Revsets:** evaluate any jj revset string → `list[Commit]`; a `Revset` builder is a
  later nicety. The revset string *is* jj's, so power users transfer knowledge directly.
- **Mutations (in a `Transaction`):** `new`, `describe`, `edit`, `abandon`, `rebase`,
  `squash`, `restore`, `set_bookmark`/`create_bookmark`/`delete_bookmark`, `snapshot`.
- **Operations:** `undo`, `restore_operation`, `at_operation`, `head_operation`.
- **Git:** `git_fetch`, `git_push`, `git_import`, `git_export`, `remotes`.

### Models (Pydantic v2)

`Commit` (change_id, commit_id, description, author/committer `Signature`, parents,
empty, conflict, bookmarks), `ChangeId`/`CommitId` (validated str newtypes), `Bookmark`
(name, remote, target, tracked), `Operation` (id, parents, time, description, tags),
`Conflict` (path, sides/`Merge`), `DiffStat`, `Signature`, `RepoState` (optional
convenience aggregate). Mirror the existing CLI-wrapper Pyjutsu's shapes where sensible.

## 6. Build, toolchain & pinning

- **maturin** builds `_pyjutsu` into an **abi3** wheel; `pyproject.toml` uses the maturin
  backend. Pure-Python `pyjutsu/` ships alongside the compiled `_pyjutsu`.
- **`Cargo.toml` pins `jj-lib` exactly** (`=X.Y.Z`, with `Cargo.lock` committed). This is
  the real API pin.
- **`devenv.nix` pins** the Rust toolchain, `maturin`, **and the matching `jj` CLI binary**
  (same X.Y.Z) so differential tests compare against the exact CLI of the bound library.
- **Version contract:** Pyjutsu's version encodes the jj it targets (e.g. `pyjutsu
  0.38.*` ↔ jj 0.38). Consumers (gitman) pin accordingly. Bumping jj = a deliberate
  Rust-side port + a Pyjutsu minor bump.

## 7. Testing strategy (the safety net for jj-lib instability)

1. **Differential tests vs the pinned `jj` CLI.** For each operation, run it through both
   Pyjutsu and `jj` (from devenv) on a scratch repo and assert equivalence (same change
   graph, bookmarks, op log effect). This validates correctness *and* turns a jj upgrade
   that changes behavior into a loud test failure.
2. **Property/round-trip tests:** build a repo via Pyjutsu, read it back, assert invariants
   (parents, ids stable across rewrites, conflicts faithful).
3. **Rust unit tests** for the thin layer; **Python tests** for models + facade.
4. **Golden fixtures** for model parsing (as gitman does), regenerated against the pin.

## 8. What to keep an eye on (risks)

1. **`jj-lib` is explicitly unstable.** Mitigate: hard pin, thin Rust layer, differential
   tests, deliberate upgrade cadence. Never expose `jj-lib` types to Python directly.
2. **Panic safety across FFI.** A Rust panic over the boundary aborts the process. Wrap
   fallible paths; map `jj-lib` errors → a `pyjutsu` exception hierarchy
   (`PyjutsuError`, `RevsetError`, `ConflictError`, `BackendError`, …). Use
   `catch_unwind` at the boundary.
3. **GIL.** Release it (`Python::allow_threads`) during I/O-heavy ops (working-copy
   snapshot, fetch/push, large revset eval) so callers stay responsive.
4. **`Send`/`Sync` + lifetimes.** `#[pyclass]` must be `Send`. jj-lib uses `Arc` widely
   (good), but some types/handles may not be `Send`/`Sync`; wrap in `Arc`/`Mutex` or keep
   thread-affine. Don't hand Python a borrowed handle that outlives its repo.
5. **Working-copy snapshot mutates.** Many "reads" snapshot @ (creating an op). Provide an
   explicit `snapshot()` and an `ignore_working_copy=True` read mode (jj's
   `--ignore-working-copy`) plus `at_operation` for consistent, side-effect-free reads.
6. **Backend generality.** Support git backend **colocated and non-colocated**, and jj's
   native backend. Don't bake gitman's "always colocated" assumption into Pyjutsu — that's
   a consumer policy.
7. **Build/distribution.** maturin wheels across {linux, macOS} × {x86_64, aarch64}; abi3
   to limit the matrix; the git backend (gix/libgit2) is a heavy dependency → watch wheel
   size and build times. Ship sdist (needs Rust to build) + prebuilt wheels.
8. **Concurrency / op log.** Surface operation-based consistency; concurrent writers create
   divergent operations — expose them (jj reconciles via the op log), don't hide.
9. **Conflicts are first-class.** Model the N-sided `Merge` faithfully (path + sides +
   sources), not just a bool. This is jj's headline feature.
10. **Stay un-opinionated.** No lanes, no workflow policy, no "trunk is frozen." Pyjutsu =
    faithful jj primitives. Policy lives in gitman and other consumers.
11. **Async.** Ship a **sync** API (jj-lib is blocking). Offer an optional async facade
    later via `asyncio.to_thread`; don't bake async into the core.
12. **Validation cost.** Per-row Pydantic validation on huge logs can dominate. Offer a
    fast path (TypedDict / lazy models) where it matters; benchmark before optimizing.

## 9. Backend: jj-lib only

Pyjutsu is **jj-lib via PyO3, period** — no CLI backend, no migration shim, no compat layer
(the old CLI-wrapper repo is discarded, §3). A subprocess fallback could be revisited *much*
later only if wheel distribution proves impractical on some platform; it is explicitly **not**
a v1 goal and carries no compatibility obligation.

## 10. Relationship to gitman

Gitman's `jj.py`/`git.py`/`templates.py` collapse into a thin adapter over Pyjutsu (or
Pyjutsu becomes a direct dependency). Gitman keeps its lane policy, transactional invariant
checks, and reports; it stops parsing templates. Pyjutsu's native transaction replaces
gitman's op-id-capture/op-restore simulation. Pyjutsu must land its read + transaction API
before gitman migrates.

## 11. Workspaces (jj's "worktrees")

jj's analog of git worktrees is the **workspace**: multiple working copies that **share one
underlying repo** (one commit store, one op log). This is the substrate for parallel agents
(gitman's "lane = workspace"), so Pyjutsu must model it faithfully.

### The jj-lib model

- A **repo** is the shared store under `.jj/repo` (backend + op log + view). A **workspace**
  is a working copy under its own root with its own `@` (working-copy commit) and its own
  on-disk tree state. The default workspace is named `default`.
- The repo `View` holds `wc_commit_ids: {WorkspaceId → CommitId}` — i.e. **every
  workspace's `@` is recorded in the shared repo**, readable from any workspace.
- In `jj-lib`: a `Workspace` (Rust) is **bound to one working-copy path**; it carries a
  `WorkspaceId` (the name) and a `LocalWorkingCopy`, and loads the shared repo via its
  `RepoLoader`. Reading the graph/op-log/bookmarks is shared and identical from any
  workspace handle; reading/snapshotting `@` is **per-workspace** (needs that path's files).

### How Pyjutsu exposes it

A `Workspace` handle in Pyjutsu == one working copy (one path). The repo behind it is shared.

```python
ws = Workspace.load(Path("repo"))            # the workspace whose WC is at this path
ws.workspaces()        # -> list[WorkspaceInfo]{name, path, wc_commit} for ALL workspaces (from the shared view)
ws.working_copy()      # -> the @ of THIS workspace only
ws.name                # this workspace's id (e.g. "default")

# create a second working copy sharing the same repo (one jj operation)
other = ws.add_workspace(path=Path("../repo-feat"), name="feat", at="trunk()")
#   -> returns a Workspace handle bound to ../repo-feat with its own new @

other.snapshot()       # snapshots ../repo-feat's working copy (that path's files)
ws.forget_workspace("feat")   # removes the record from the shared view; does NOT delete the dir
```

**Decision — `add_workspace` is eager.** It returns a ready-to-use `Workspace` handle, with
the working copy **checked out inside the call**: the Rust layer performs the
record-creation transaction (allocate the `WorkspaceId`, set its `@` in the shared view)
**and** the working-copy checkout at `path` as one operation from the caller's view, then
hands back a handle bound to that path. (Internally this is jj-lib's two steps — create the
workspace record + check out files — but the binding sequences them so the caller never sees
a half-created workspace.) If `path` already exists and is non-empty, `add_workspace` errors
rather than clobbering it.

Key guarantees/semantics to surface (don't hide them — Pyjutsu is faithful, gitman adds policy):

- **One repo, N working copies.** Graph/bookmark/op-log reads are consistent across all
  handles; only `@` and snapshots are path-local.
- **A handle is path-bound.** To touch another workspace's files/`@`, load (or hold) that
  workspace's handle — you can hold several in one process, but each snapshot needs its path.
- **`forget_workspace` ≠ delete dir.** It removes the workspace's record + its `@` from the
  view; the directory is left for the caller to remove (mirrors `jj workspace forget`).
- **Stale working copy.** If a transaction (from this or another workspace) rewrites the
  commit another workspace's `@` points to, that workspace becomes **stale**. Pyjutsu must
  (a) detect it (`ws.is_stale()` / a `StaleWorkingCopy` signal) and (b) offer
  `ws.update_stale()` (the `jj workspace update-stale` equivalent) — never silently operate
  on a stale `@`.
- **Two concurrency layers.** Snapshotting takes a **per-workspace working-copy lock**;
  repo writes use the **op log's optimistic concurrency** (concurrent writers from different
  workspaces produce *divergent operations* that jj reconciles). Surface divergence; don't
  pretend it can't happen.

### Why this is much nicer than the CLI for parallel agents

With the CLI, every workspace command reloads the repo and re-snapshots. With Pyjutsu the
**shared repo store is loaded in-process and reused**: a coordinator process can hold one
repo view and many lightweight `Workspace` handles, reading all lanes' state cheaply and
opening transactions without per-call process/load cost. Each parallel agent can still run
in its own process against its own workspace path; the shared op log coordinates them
exactly as jj intends.

### Watch-outs specific to workspaces

- **Stale-`@` detection is mandatory** before mutating a workspace whose base moved.
- **`#[pyclass]` workspace handles must be `Send`** and must not outlive their repo; the
  shared repo is `Arc`-shared, individual working copies are path-affine.
- **WorkspaceId uniqueness** within a repo; default name is `default`.
- **`at_operation` reads** give a historical repo view while the working copy stays current
  — document the split so callers don't conflate "repo at op X" with "files on disk now".

## 12. Scope — v1 vs later

**v1:** load/init, reads (working_copy/resolve/log/bookmarks/operations/diff_stat +
conflicts), transactions (new/describe/edit/abandon/rebase/squash/bookmarks/snapshot),
op log (undo/restore/at_operation), git fetch/push/import/export, the Pydantic model set,
maturin build + devenv pin + differential tests.

**Later:** revset builder, full diffs/hunks, native backend polish, async facade, CLI
fallback backend, streaming/iterator log for huge repos, Windows.
