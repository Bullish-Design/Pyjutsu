# Pyjutsu M2 — scoping kickoff prompt

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → this prompt (orientation + the
> scoping task) → the `M2_IMPLEMENTATION_GUIDE.md` you are about to **produce**. Read the concept
> and skim the shipped M1 code before scoping. **This is a scoping session: the deliverable is a
> plan, not code — do not implement until the design decisions below are approved.**

---

You are scoping **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic + Pydantic
binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no subprocess, no
text parsing. **M0 (build spike) and M1 (the read layer) are done, released as `pyjutsu 0.39.0`,
and on `main`.** Your job is to design M2 — mutations, transactions, snapshotting, and op-log
writes — grounded in the **verified jj-lib 0.38 API**, and to surface the architecture decisions
that need the user's sign-off before any code is written.

## The deliverable

1. **`M2_IMPLEMENTATION_GUIDE.md`** in this folder (the analog of M1's guide): a concrete,
   jj-lib-0.38-grounded plan — a **verified API reference with file:line refs** (transactions,
   `MutableRepo`, `CommitBuilder`, rewrite/rebase/squash, working-copy snapshot + checkout +
   locking, op-log/undo, stale-WC), the **architecture (spine)**, a **vertical build order** with
   a differential test per slice, the **Pydantic/facade surface**, and the **testing strategy**.
2. **A short "decisions for sign-off" section** (top of the guide): the open design choices listed
   below, each with a recommended option + rationale. Use `AskUserQuestion` to get the user's calls
   on the load-bearing ones **before** finalizing the guide. Do not start implementing.

## One-sentence thesis

Expose jj's writes — `new`/`describe`/`edit`/`abandon`/`rebase`/`squash`/`restore`, bookmark
writes, and `snapshot()` — as **explicit, atomic mutations inside a native `jj-lib` Transaction
(one transaction == exactly one jj operation)**, plus op-log writes (`undo`/`restore_operation`),
with the on-disk working copy updated correctly and **differentially tested against the pinned
`jj` 0.38 CLI**.

## Non-negotiable constraints (carried from M0/M1 — do not relitigate)

- **jj-lib via PyO3, in-process. No subprocess/CLI backend, no compat shim.** Pin stays
  `jj-lib = "=0.38.0"`; `Cargo.lock` committed.
- **Everything runs inside the `devenv.sh` shell** — `devenv shell -- <cmd>`. Never bare
  `cargo`/`maturin`/`python`/`pytest`/`jj`. The devenv pins Rust, maturin, and the matching `jj`
  0.38.0 CLI used for differential tests.
- **Thin Rust, rich Python.** `_pyjutsu` exposes opaque handles + **plain data only**
  (dicts/lists/strings); **never leak `jj-lib` types to Python**. All models, ergonomics,
  defaults, and the public contract live in pure-Python `pyjutsu`.
- **Differential testing against the pinned `jj` CLI is the primary correctness + drift net.**
  Assert equivalence of the resulting change graph, bookmarks, **and op-log effect**.
- **Faithful, un-opinionated primitives.** No workflow policy (no lanes/frozen trunk). Mirror jj.
  Conflicts stay first-class N-sided. Surface divergence; don't hide it.
- **Versioning is independent of jj** (concept §6, decided during M1): pyjutsu has its own semver;
  the jj-lib pin lives in `Cargo.toml`/`devenv.nix`; the package exposes `JJ_VERSION` (linked) and
  `JJ_LIB_TARGET` (targeted) and checks them only as a broken-build tripwire. **M2 is a normal
  pyjutsu minor bump (→ `0.40.0`), NOT tied to the jj version.**
- **Panic safety + error mapping**: every fallible path maps a jj-lib error to the right
  `PyjutsuError` subclass; no jj-lib error type crosses the FFI.
- **No AI-generated attribution** in commits/PRs/docs.

## What M1 already established (build on this; don't redo it)

- **Rust split** (`src/`): `lib.rs` (pymodule wiring), `errors.rs` (exception hierarchy
  `PyjutsuError`/`RevsetError`/`ConflictError`/`BackendError`/`WorkspaceError` + `map_*` helpers),
  `convert.rs` (plain-data intermediates + `*_to_dict`), `revset.rs` (parse→resolve→evaluate),
  `repo_view.rs` (`PyRepoView`), `workspace.rs` (`PyWorkspace`), `diff_stat.rs`.
- **Handles & `Send`/`Sync`:** `PyWorkspace { Mutex<Workspace>, user_email }` (jj `Workspace` is
  `Send`, not `Sync`). `PyRepoView { Arc<ReadonlyRepo>, workspace_name, workspace_root, user_email }`
  (`Arc<ReadonlyRepo>` is `Send + Sync`, GIL-releasable). `PyWorkspace`:
  `load`/`name`/`workspace_root`/`head_view`/`head_operation`/`at_operation`.
- **Read pattern:** evaluate jj-lib into plain Rust structs **off the GIL** (`Python::allow_threads`),
  convert to dicts on the GIL, validate into frozen `extra="forbid"` Pydantic models in Python.
- **Models:** `Commit` (enriched), `Signature`, `Bookmark`, `Operation`, `Conflict`,
  `DiffStat`/`FileStat`. ids: change_id = `ChangeId::reverse_hex()` (z-k), commit_id = `hex()`.
- **Read split (approved M1 decision):** all reads live on `RepoView` (immutable, at one op);
  `Workspace` delegates reads to a fresh head view; `at_operation` returns a historical `RepoView`.
  **Reads never snapshot** (M1 contract, enforced by an op-count invariant test).
- **Differential harness:** `tests/diff/jj_cli.py` (`JjCli` driver, isolated `JJ_CONFIG`),
  `tests/conftest.py` fixtures (`scratch_repo`, `linear_repo`, `bookmarked_repo`, `diffstat_repo`,
  `conflict_repo`), per-read test modules, golden `tests/golden/model_fields.json`, op-count
  invariant test. Tasks: `devenv tasks run pyjutsu:{build,test,lint}`.

## M2 scope (what to design)

In scope (concept §5, §12 "v1"):

- **Transactions:** `with ws.transaction("description") as tx: …` mapping to **exactly one jj
  operation**, committed atomically on context exit (and rolled back / not committed on exception).
- **Commit mutations on `tx`:** `new(parents=…)`, `describe(commit, msg)`, `edit(commit)`,
  `abandon(commit)`, `rebase(...)`, `squash(...)`, `restore(...)`. Return updated `Commit` models.
- **Bookmark writes on `tx`:** `create_bookmark`/`set_bookmark`/`delete_bookmark` (+ move/forget as
  jj distinguishes them).
- **Working copy:** explicit `ws.snapshot()` (capture on-disk edits into `@` as an operation), and
  correct **working-copy update/checkout** after a transaction rewrites `@`.
- **Op-log writes:** `ws.undo()` (revert the last operation) and `ws.restore_operation(op)`.
- **Stale working copy:** detect (`is_stale()` / a `StaleWorkingCopy` signal) and `update_stale()`
  (concept §11) — never silently operate on a stale `@`.

Decide whether to include now or defer to M3 (recommend in the guide):

- **Workspace management:** `init`, `clone`, `add_workspace`/`forget_workspace` (concept §11 —
  `add_workspace` is eager). **Git interop:** `git_fetch`/`git_push`/`git_import`/`git_export`,
  `remotes`. These are heavier and may warrant their own milestone — make a recommendation.

## Load-bearing design decisions to resolve (get user sign-off via AskUserQuestion)

1. **Transaction & mutable-repo model.** How the facade maps to jj-lib's `start_transaction` →
   `MutableRepo` → `tx.commit(description)`. Where does it live — on `Workspace` (it owns the
   `Mutex<Workspace>` and the working copy), not on the immutable `RepoView`. How does the
   `PyWorkspace`'s cached/loaded repo advance after commit (reload `ReadonlyRepo` at the new op)?
   Is the `Transaction` handle a separate `#[pyclass]`, and how is its `Send`/lifetime managed
   given `MutableRepo` borrows the repo?
2. **Snapshot policy.** M1 reads never snapshot. For M2, does opening a transaction (or specific
   mutations) **auto-snapshot `@`** (capturing on-disk edits, like the jj CLI), or is snapshot
   **always explicit** (`ws.snapshot()`), with mutations operating on the last-snapshotted `@`?
   This is the biggest behavioral choice — recommend one, with the jj-CLI-parity tradeoff spelled
   out, and note how differential tests stay valid either way.
3. **Working-copy update after rewrite.** When a transaction rewrites the commit `@` points to,
   the on-disk tree must be checked out. Define the mechanism (working-copy lock → checkout →
   finish), GIL release for the I/O, and what happens on conflict/locked-WC.
4. **Atomicity & failure semantics.** Guarantee: a transaction that raises commits **nothing**.
   Define rollback, and how partial-mutation errors surface. Confirm "1 tx == 1 op" holds even for
   multi-step transactions.
5. **Concurrency / divergence.** Op-log optimistic concurrency: concurrent writers create divergent
   operations. Decide how Pyjutsu surfaces this (don't hide it) and how `undo`/`restore_operation`
   behave with divergent heads.
6. **Error taxonomy additions.** New failure modes (immutable-commit edits, stale WC, WC lock
   contention, divergent-op conflicts) — which existing `PyjutsuError` subclasses map, and whether
   to add new ones (e.g. `StaleWorkingCopyError`).
7. **Mutation return shape.** Do `tx` methods return updated `Commit` models immediately (requires
   reading back inside the open transaction's `MutableRepo`), or only after commit? Define it.

## jj-lib 0.38 API areas to verify (read the source; cite file:line in the guide)

Source tree: `~/.cargo/registry/src/index.crates.io-*/jj-lib-0.38.0/src/`. Confirm signatures —
do not trust names from memory:

- **Transactions / mutable repo:** `transaction.rs` (`Transaction`, `repo_mut()`/`mut_repo()`,
  `commit(description)`); `repo.rs` (`ReadonlyRepo::start_transaction`, `MutableRepo` mutators:
  new/rewrite/abandon commit, `set_wc_commit`/`edit`, `set_local_bookmark_target`,
  `remove_local_bookmark`, `rebase_descendants`).
- **Commit construction:** `commit_builder.rs` (`CommitBuilder`: set_parents/description/tree_id,
  `write`).
- **Rewrite primitives:** `rewrite.rs` (`rebase_commit`/`CommitRewriter`, `merge_commit_trees`,
  `restore_tree`, `EmptyBehaviour`, move/squash helpers — find what's in `jj-lib` vs CLI-only).
- **Working copy:** `working_copy.rs` (the `WorkingCopy` trait, `LockedWorkingCopy`,
  `SnapshotOptions`) and `local_working_copy.rs` (`start_mutation`/`snapshot`/`check_out`/`finish`,
  locking); `workspace.rs` (how `Workspace` ties a transaction commit to a working-copy update,
  stale detection — compare WC operation id vs repo head).
- **Op log / undo:** `op_store.rs`/`op_walk.rs`/`op_heads_store.rs` and how the CLI implements
  `undo` (operation revert) and `op restore` — replicate the lib-level building blocks.

## How to work

1. **Read first:** `docs/PYJUTSU_CONCEPT.md` (esp. §5 surface, §8 risks 2–5/8, §11 workspaces,
   §12 scope), then skim the shipped M1 Rust + Python + tests so the M2 plan reuses the off-GIL +
   plain-data + differential patterns rather than reinventing them.
2. **Verify the jj-lib 0.38 API in the source tree** (above), citing file:line — this is the spine
   of the guide and the thing that prevents churn.
3. **Resolve the load-bearing decisions with the user** (`AskUserQuestion`) before finalizing.
4. **Write `M2_IMPLEMENTATION_GUIDE.md`:** decisions-for-sign-off section, architecture/spine,
   verified API reference, a vertical build order (suggested: transactions+`describe` first to
   bring up the tx machinery on the simplest mutation → `new`/`edit`/`abandon` → bookmark writes →
   `snapshot` + working-copy checkout → `rebase`/`squash`/`restore` → `undo`/`restore_operation` →
   stale-WC), the Pydantic/facade surface, and the testing strategy (per-mutation differential
   tests asserting graph + bookmarks + **op-log effect**; a "1 tx == 1 op" invariant; round-trip
   property tests; rollback-on-exception test).
5. **Stop at the plan.** Do not implement until the user approves the guide.

## Guardrails

- Don't add a CLI/subprocess backend, a migration shim, or old-Pyjutsu compatibility.
- Don't leak `jj-lib` types across the FFI or put business/workflow logic in Rust.
- Don't bake workflow policy (lanes, frozen trunk) into Pyjutsu — faithful primitives only.
- Don't silently operate on a stale `@`; don't hide operation divergence.
- Don't run bare host tooling — everything through devenv. No AI attribution in commits/docs.
- Don't tie the pyjutsu version to jj (independent versioning — M2 is `→ 0.40.0`).

**Start by reading `docs/PYJUTSU_CONCEPT.md` and skimming the M1 code, verify the jj-lib 0.38
transaction/working-copy/op-log APIs in the pinned source, resolve the design decisions with the
user, then write `M2_IMPLEMENTATION_GUIDE.md`. Produce the plan; do not implement yet.**
