# 12 — Pyjutsu repo review & catch-up

> Snapshot written 2026-07-01 by reading the repo (README, `docs/PYJUTSU_CONCEPT.md`, the
> `.scratch/projects/*` history, and the actual `src/` + `python/` + `tests/` tree) and verifying
> every claim against code + a live test run. Nothing here is aspirational: each "NOW" line cites a
> real module/function or a command that was run.

---

## 1. What Pyjutsu IS (and the desired final concept)

**Pyjutsu is a general-purpose, Pythonic + Pydantic binding to jujutsu's Rust engine (`jj-lib`) via
PyO3/maturin** — native graph, op-log, working-copy, conflict, and git-interop access **in-process**,
with **no subprocess and no text parsing**. It replaces the old "shell out to `jj` + parse
templates" ceiling (process-per-call, a working-copy snapshot per invocation, brittle text parsing,
no native cross-call transaction) by binding `jj-lib` directly: reads return frozen Pydantic models;
mutations run inside jj-lib's real `Transaction` (one atomic operation).

- **Import:** `import pyjutsu`. **Binds:** `jj-lib` (hard-pinned `=0.42.0` in `Cargo.toml`).
- **Versioned independently of jj** — its own semver cadence; `pyjutsu.JJ_VERSION` reports the linked
  jj-lib at runtime. Canonical spec: `docs/PYJUTSU_CONCEPT.md`.
- **Architecture (design rule: keep Rust thin & dumb).** Three layers (CONCEPT §4): pure-Python
  public facade (Pydantic models, ergonomics, typing) over a **thin** `_pyjutsu` PyO3 extension
  (opaque handles + plain data, no business logic) over pinned `jj-lib`. jj-lib churn is absorbed in
  the thin Rust layer; the Python public API stays stable across jj upgrades.
- **Safety net for jj-lib's intentional instability:** hard version pin + **differential tests
  against the pinned `jj` CLI** turn any behavioural drift into a loud test failure.
- **Un-opinionated on purpose (CONCEPT §10, §8.10):** faithful jj primitives only — **no lanes, no
  workflow policy, no "trunk is frozen."** Policy lives in consumers. **gitman is the primary
  consumer** (its `jj.py`/`git.py`/`templates.py` collapsed into direct pyjutsu use; it keeps lane
  policy + invariant checks and stops parsing templates).

**Desired final concept (the v1 surface, CONCEPT §5/§12):** load/init + clone; reads
(`working_copy`/`resolve`/`log`/`iter_log`/`bookmarks`/`operations`/`diff_stat`/`diff` + first-class
N-sided conflicts); transactions (`new`/`describe`/`edit`/`abandon`/`rebase`/`squash`/`restore`/
`split`/`select_tree` + bookmark CRUD + `snapshot`); op log (`undo`/`restore_operation`/
`at_operation`/`head_operation`); git fetch/push/import/export + remotes; faithful **workspaces**
(jj's worktrees: one shared repo, N path-bound working copies, stale-`@` detection); a typed
Pydantic model set; maturin abi3 build + devenv pin + the differential test suite. Async stays
`to_thread`-only by design (jj's `!Send` transaction model — CONCEPT §8.11, README "Async usage").

---

## 2. Where it is NOW (verified)

- **Version: 0.9.0.** `pyproject.toml` + `Cargo.toml` both `0.9.0`; `python/pyjutsu/__init__.py`
  `__version__ = "0.9.0"`. Live import in devenv reports **`pyjutsu 0.9.0 jj-lib 0.42.0`**
  (`devenv shell -- python -c "import pyjutsu; print(pyjutsu.__version__, pyjutsu.JJ_VERSION)"`).
  > **README is stale:** its header still says *"Status: 0.8.0 — tracks jj-lib 0.42.0."* The code is
  > 0.9.0. `docs/PYJUTSU_CONCEPT.md` is even staler (its **Status** line still says M1/M2 = 0.2.0/
  > 0.3.0 binding jj-lib 0.38 — never updated past the milestone bootstrap, though its §5/§12 spec
  > body is current and describes `split`/`select_tree`). See the gap table below.
- **Test status: GREEN.** `devenv shell -- python -m pytest tests/ -q` → **224 passed, exit 0** (run
  2026-07-01). 32 test files under `tests/`, incl. `test_split.py` (14 cases) and `test_build.py`
  (version-guard invariants). Rust unit tests live in `src/lib.rs` (`version_is_pinned`,
  `pyjutsu_version_matches_crate`) and `diff_stat.rs`.
- **Public API surface** (`python/pyjutsu/__init__.py __all__`, all backed by real modules):
  - Facades: `Workspace` (`workspace.py`), `RepoView` (`repo_view.py`), `Transaction`
    (`transaction.py`), revset builder `Revset` + `Pattern` (`revset.py`).
  - Models (`models.py`): `Commit`, `Signature`, `Operation`, `Bookmark`, `WorkspaceInfo`, `Remote`,
    `Conflict`, `Diff`, `DiffStat`, `FileChange`, `FileStat`, `Hunk`, `HunkLine`, `JjResult`,
    `ChangeId`, `CommitId`.
  - Errors (`errors.py`): `PyjutsuError`, `RevsetError`, `ConflictError`, `BackendError`,
    `WorkspaceError`, `WorkingCopyError`, `StaleWorkingCopyError`, `ImmutableCommitError`, `GitError`,
    `JjCliError`.
  - Version data: `__version__`, `JJ_VERSION`, `JJ_LIB_TARGET` (now an alias of the build-derived
    `JJ_VERSION`).
- **Transaction verbs bound** (`python/pyjutsu/transaction.py`): `describe`, `new`, `edit`, `abandon`,
  `rebase`, `squash`, `restore`, **`select_tree`** (`:220`), **`split`** (`:238`), bookmark CRUD
  (`create`/`set`/`delete`/`track`/`untrack`). Native side in `src/transaction.rs`
  (`select_tree` `:493`; the partial-tree assembler `:853`).
- **Rust modules** (`src/`): `lib.rs`, `workspace.rs`, `repo_view.rs`, `transaction.rs`, `revset.rs`,
  `diff.rs`, `diff_stat.rs`, `convert.rs`, `errors.rs`. Build helper `build.rs` derives the jj-lib
  version from the committed `Cargo.lock`.
- **Consumer status — gitman already migrated in-process.** `../gitman` depends on `pyjutsu>=0.8`
  (`gitman/pyproject.toml`) and imports it across 8 modules (`core.py`, `state.py`, `session.py`,
  `reconcile.py`, `invariants.py`, `doctor.py`, `init.py`, `cli.py`); no `jj.py` CLI-wrapper module
  remains. The CONCEPT §10 migration is **done**, not pending.
- **VCS state:** on branch **`feat/11-tx-split`**, working tree clean. `main` does **not** yet have
  0.9.0 (see the outstanding item). Prior landed rounds visible in `git log`: v0.7.0 power surface
  (`run_jj`, streaming `iter_log`, revset builder), 0.7.0 code-review fixes, port to jj-lib 0.42.0
  (0.8.0), adopt keep-ref prune (proj 10 P1), and the 0.9.0 split + version-guard commit.

### Seed items — all LANDED & verified against code

| Seed claim | Verified in |
|---|---|
| v0.7.0 power surface incl. `run_jj` escape hatch | `git log` (`405999a`, `75b941e`); README "Escape hatch: run_jj"; `Workspace.run_jj` |
| v0.9.0 native `tx.split` / `select_tree` (unblocks gitman sub-file split) | `transaction.py:220,238`; `src/transaction.rs:493,853`; `_pyjutsu.pyi:122,125`; `tests/test_split.py` (14 tests); proj 11 §Status "SHIPPED" |
| orphaned keep-ref pruning on re-adopt | `git log` (`ff0e2f0`); proj 10 §P1 "Fixed (shipped)"; `test_init_adopt.py::test_readopt_prunes_orphaned_keep_refs` |
| build-derived version guard | `build.rs` (parses `Cargo.lock` → `PYJUTSU_JJ_LIB_VERSION`); `src/lib.rs:32,37,50`; `__init__.py` stale-build tripwire (`__version__ == _ext.pyjutsu_version()`); `test_build.py` |
| H1 tx-slot leak (0.7.0 code review) | Fixed: `src/transaction.rs:797` `take()?` then `:803` `release_slot()` before fallible work |

---

## 3. Concept-vs-reality gap table

Legend: **✅ landed** · **📄 docs-drift** · **🔭 deferred (CONCEPT §12 "Later" / explicit non-goal)** ·
**🧹 polish (from the 0.7.0 code review, not yet applied)**.

| # | Concept / spec says | Reality (cited) | Gap |
|---|---|---|---|
| 1 | Full v1 read + transaction + op-log + git + workspace surface (§5, §12) | All present & green — see §2 API surface; `tests/` 224 pass | ✅ none |
| 2 | Sub-file hunk-level split (`split`/`select_tree`, §5 "power surface") | `transaction.py:220,238`; `src/transaction.rs:493,853`; `test_split.py` | ✅ landed (0.9.0) |
| 3 | Version is build-derived, no two hand-maintained copies (proj 10 §P3) | `build.rs` + `src/lib.rs:32`; `JJ_LIB_TARGET = JJ_VERSION` alias | ✅ landed |
| 4 | README **Status** header | README says **"0.8.0"**; code + live import say **0.9.0** | 📄 stale by one minor |
| 5 | `docs/PYJUTSU_CONCEPT.md` **Status** line | Still says M1/M2 = 0.2.0/0.3.0 binding **jj-lib 0.38**; actual 0.9.0 / **0.42.0**. (Spec body §5/§12 is current: it already documents `split`/`select_tree`.) | 📄 stale header only |
| 6 | `diff(from, to)` two-revset diff (§12 "Later"; README §12 ref) | `RepoView.diff(revset)` / `diff_stat(revset)` are **single-revset only** (`repo_view.py:84,92`); no `from_,to` overload | 🔭 deferred, unbuilt |
| 7 | Word/inline (color-words) diff (§12 "Later"; CLI-options §5 "needs engine-side word diff") | `diff()` emits structured `Hunk`/`HunkLine` line-level only (`models.py`, `repo_view.rs`) | 🔭 deferred, unbuilt |
| 8 | Native **async facade** (README "Async usage"; §8.11) | Intentionally **not** provided — `to_thread` is the story (GIL released per call) | 🔭 deliberate non-goal (documented) |
| 9 | CLI / TUI over the typed core (§12 "CLI fallback backend"; proj 08 options report) | None shipped; `run_jj` is the only CLI-shaped surface. Proj 08 recommends a thin separate `pyjutsu-cli` (Option B), no FFI needed | 🔭 deferred; scoped, not started |
| 10 | Git fetch imports **tags** (jj-standard `import_refs`) — off-main tagged commit becomes a visible head (proj 10 §P2) | Kept jj-standard (decision **A**); no `import_tags=False` flag; tag *fetching* still gated on jj #7528 (`workspace.rs:1144`) | 🔭 decided non-goal (revisit only if more consumers hit it) |
| 11 | Transaction failure-path / concurrency test coverage (0.7.0 review §7 gaps) | H1 fix landed; but review noted **no test forces `commit` to fail**, no L1 concurrency test, thin `run_jj` error-branch coverage | 🧹 test-seam / coverage polish, optional |
| 12 | 0.9.0 shipped → gitman's `08-split-lane-capability` S3 tier unblocked (proj 11 §Status) | 0.9.0 lives **only on `feat/11-tx-split`**; not on `main`, not released; gitman still pins `pyjutsu>=0.8` | ⛰️ **the outstanding item** — see PLAN §1 |

**Bottom line:** the library is feature-complete against its own v1 spec and fully green; the biggest
real gap is **process, not code** — 0.9.0 is finished on a feature branch but hasn't landed on `main`
or been released, so the `tx.split` work that unblocks gitman's sub-file split isn't yet consumable.
Everything else outstanding is either deliberate deferral (§12 "Later": two-revset diff, word diff,
CLI) or optional test polish.
