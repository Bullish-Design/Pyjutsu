# Pyjutsu M2 — Implementation Guide (the write layer)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec) → this guide → the code it produces.
> **Status of this document:** approved scoping plan. Design decisions below are **signed off**
> (see §0). Build in the vertical order of §4; do not deviate from the verified API in §3 without
> re-reading the pinned source.
>
> **Pin:** `jj-lib = "=0.38.0"`, matching `jj` 0.38.0 CLI in devenv. Pyjutsu bumps **`0.39.0 →
> 0.40.0`** (independent of jj). All API refs below are `file:line` into
> `~/.cargo/registry/.../jj-lib-0.38.0/src/`, verified against the pinned source.

---

## 0. Decisions for sign-off — RESOLVED

| # | Decision | Resolution | Rationale |
|---|---|---|---|
| 1 | **Snapshot policy** | **Auto-snapshot on transaction open (CLI parity)**, plus explicit `ws.snapshot()`. | The pinned CLI snapshots a dirty `@` before every command. Matching it keeps differential tests trivially valid and matches user expectation. |
| 2 | **Mutation return shape** | **Return a full `Commit` model immediately**, read back from the open `MutableRepo` (it impls `Repo`). | Data is already in hand; reuses M1's `CommitData → Pydantic` path; no second round-trip. |
| 3 | **Error taxonomy** | **Add targeted subclasses**: `WorkingCopyError`, `StaleWorkingCopyError` (⊂ `WorkingCopyError`), `ImmutableCommitError`; reuse `ConflictError`/`BackendError`/`RevsetError`/`WorkspaceError`. | Callers can catch precisely; small, faithful surface. |
| 4 | **M2 scope** | **Everything in concept §12 v1**: mutations + bookmarks + snapshot/checkout + undo/restore + stale-WC + **workspace management** (`init`/`add_workspace`/`forget_workspace`) + **git interop** (`git_fetch`/`push`/`import`/`export`/`remotes`). | User chose the full write surface. Sequenced so the heavy/network git slice lands last (§4, slices 9–11) and can ship incrementally. |

### 0.1 Decisions that were mechanical (settled, not asked)

- **Where the transaction lives.** `MutableRepo` *borrows* the repo (`new_commit`/`rewrite_commit`
  return `CommitBuilder<'_>`, repo.rs:948/954), so a `Transaction` **cannot** be a free-standing
  `Send` `#[pyclass]` holding a borrow. The native `Transaction` is **owned inside `PyWorkspace`**
  behind its existing `Mutex<Workspace>`; the Python `tx` object is a **thin token** whose methods
  re-enter the workspace handle. One open transaction per workspace at a time (enforced).
- **`@` advances after commit.** `Transaction::commit` returns a fresh `Arc<ReadonlyRepo>` at the
  new op (transaction.rs:120). `PyWorkspace` caches this as its current repo so subsequent reads
  (and the next tx) see the new head, exactly like M1's `load_at_head` but without re-walking the
  op heads.
- **Verified CLI behavior (empirical, jj 0.38.0):** a mutation on a **dirty** `@` produces **two**
  operations — `snapshot working copy` then the command op; on a **clean** `@`, **one**. So
  "1 tx == 1 op" is a statement about the **mutation** transaction; the auto-snapshot is a
  **separate preceding operation**, faithful to the CLI. Op-count tests assert this split (§6).

---

## 1. One-sentence thesis

Expose jj's writes as explicit, atomic mutations inside a native `jj-lib` `Transaction` (one
mutation transaction == exactly one published jj operation), with `@` auto-snapshotted as a
separate preceding operation just like the CLI, the on-disk working copy checked out after any
rewrite of `@`, and **every** path differentially tested against the pinned `jj` 0.38.0 CLI for
graph + bookmarks + op-log effect.

## 2. Architecture (the spine)

### 2.1 Layering (unchanged from M1)

```
pyjutsu (pure Python) ── Workspace facade · Transaction facade · Pydantic models · ergonomics
  _pyjutsu (Rust, THIN) ── PyWorkspace (owns Mutex<Workspace> + optional open Transaction)
                           PyRepoView (reads, M1) · plain-data dicts only · error mapping
    jj-lib 0.38 (pinned) ── ReadonlyRepo/MutableRepo · Transaction · CommitBuilder ·
                            LocalWorkingCopy · git backend
```

Same rules as M1: **thin Rust, rich Python**; **no `jj-lib` type crosses the FFI**; mutations
compute off the GIL (`Python::allow_threads`) and return plain dicts the Python layer validates
into Pydantic models; every fallible path maps a `jj-lib` error to a `PyjutsuError` subclass.

### 2.2 New Rust handle: the open transaction

`PyWorkspace` (src/workspace.rs) gains an owned, optional in-flight transaction. Because
`jj_lib::Transaction` borrows nothing with a named lifetime *as a value* (it owns its
`MutableRepo`, transaction.rs:62–67), it **is** storable — the borrow problem is only with the
`CommitBuilder<'_>` returned by mutators, which we consume within a single method call. So:

```rust
pub(crate) struct PyWorkspace {
    inner: Mutex<Workspace>,                 // jj Workspace: Send, !Sync  (M1)
    repo: Mutex<Arc<ReadonlyRepo>>,          // NEW: current head repo, advanced on commit
    tx: Mutex<Option<Transaction>>,          // NEW: at most one open transaction
    user_settings: UserSettings,             // NEW: real identity (see §2.3)
    user_email: String,                      // M1 (revset context)
}
```

- `Transaction` is `Send` (it owns `MutableRepo` + `Vec<Operation>` + metadata, all `Send`); the
  `Mutex` makes the whole handle `Sync` for `#[pyclass]`. Each tx method: lock `tx`, `as_mut()` the
  `Option`, call a mutator on `tx.repo_mut()` (repo.rs:94), build a `CommitData` from the same
  `MutableRepo` (it impls `Repo`), return the dict.
- **Token model.** Python `Transaction` holds a back-reference to the `Workspace` facade + a
  generation/open flag. Entering `with ws.transaction(...)` calls `PyWorkspace::begin_transaction`
  (auto-snapshot, then `start_transaction`); exit calls `commit_transaction` (success) or
  `rollback_transaction` (exception). Mutation methods raise if the tx is already closed.

### 2.3 Authoring identity (new requirement)

M1's `load` used `UserSettings::from_config(StackedConfig::with_defaults())` — **empty
name/email** (workspace.rs:45, comment: "no user name/email needed until we author commits, in
M2"). M2 **must** load the real user config so `CommitBuilder` author/committer signatures
(`settings.signature()`, settings.rs:200) match the CLI. Load the full stacked config (user +
repo) via jj-lib's config loader, mirroring how the CLI builds `UserSettings`, and carry it on
`PyWorkspace`. Differential tests run under the isolated `JJ_CONFIG` (tests already do this,
`tests/diff/jj_cli.py`) so the binding and CLI share one identity → identical commit ids.

> **Open implementation note (verify at build, slice 1):** confirm the exact config-load entry
> jj-lib exposes for "user + repo stacked config" in 0.38 (`config.rs` / `config_resolver.rs`).
> If only `with_defaults()` + manual file layering is public, replicate the CLI's layering. This
> is the one spot where the binding reproduces a CLI behavior not handed to it as a single call.

---

## 3. Verified jj-lib 0.38 API reference

All refs verified in the pinned source. Methods that touch the backend/disk run **off the GIL**.

### 3.1 Transactions / mutable repo

| What | Signature (abbrev.) | Ref |
|---|---|---|
| Start a tx | `ReadonlyRepo::start_transaction(self: &Arc<Self>) -> Transaction` | repo.rs:326 |
| Tx struct | `Transaction { mut_repo, parent_ops, op_metadata, end_time }` | transaction.rs:62 |
| Mutable repo (mut) | `Transaction::repo_mut(&mut self) -> &mut MutableRepo` | transaction.rs:94 |
| Mutable repo (ref) | `Transaction::repo(&self) -> &MutableRepo` | transaction.rs:90 |
| Tag op | `Transaction::set_tag(&mut self, key, value)` | transaction.rs:86 |
| Mark snapshot op | `Transaction::set_is_snapshot(&mut self, bool)` | transaction.rs:115 |
| **Commit + publish** | `Transaction::commit(self, description) -> Result<Arc<ReadonlyRepo>, TransactionCommitError>` | transaction.rs:120 |
| Write unpublished | `Transaction::write(self, description) -> Result<UnpublishedOperation, _>` | transaction.rs:130 |
| Publish later | `UnpublishedOperation::{publish, leave_unpublished}` | transaction.rs:224/232 |

> ⚠️ **Critical invariant** (transaction.rs:136): `commit`/`write` `assert!(!mut_repo.has_rewrites())`.
> **You must call `rebase_descendants()` after any rewrite/abandon before committing**, or the
> process aborts. Every rewriting mutation ends with a `rebase_descendants()` (see §4 slices).

**`MutableRepo` mutators** (repo.rs):

| What | Signature | Ref |
|---|---|---|
| New commit builder | `new_commit(&mut self, parents: Vec<CommitId>, tree: MergedTree) -> CommitBuilder<'_>` | repo.rs:948 |
| Rewrite commit builder | `rewrite_commit(&mut self, predecessor: &Commit) -> CommitBuilder<'_>` | repo.rs:954 |
| Record abandon | `record_abandoned_commit(&mut self, old_commit: &Commit)` | repo.rs:1005 |
| Abandon onto parents | `record_abandoned_commit_with_parents(old_id, new_parent_ids)` | repo.rs:1019 |
| Record rewrite | `set_rewritten_commit(old_id, new_id)` | repo.rs:971 |
| **Rebase descendants** | `rebase_descendants(&mut self) -> BackendResult<usize>` | repo.rs:1428 |
| Set `@` pointer | `set_wc_commit(name: WorkspaceNameBuf, commit_id) -> Result<(), RewriteRootCommit>` | repo.rs:1458 |
| Remove `@` (abandons if discardable) | `remove_wc_commit(name) -> Result<(), EditCommitError>` | repo.rs:1470 |
| Edit (point `@` at existing) | `edit(name: WorkspaceNameBuf, commit: &Commit) -> Result<(), EditCommitError>` | repo.rs:1526 |
| Check out (new child of) | `check_out(name, commit) -> Result<Commit, CheckOutCommitError>` | repo.rs:1514 |
| Local bookmark get/set | `get_local_bookmark(name: &RefName) -> RefTarget` / `set_local_bookmark_target(name, target)` | repo.rs:1672/1676 |
| Remote bookmark set/track | `set_remote_bookmark` / `track_remote_bookmark` / `untrack_remote_bookmark` | repo.rs:~1700+ |
| Op-log **merge** (undo bldg block) | `merge(&mut self, base_repo: &ReadonlyRepo, other_repo: &ReadonlyRepo) -> Result<(), RepoLoaderError>` | repo.rs:1831 |
| Set view (restore bldg block) | `set_view(&mut self, data: op_store::View)` | repo.rs:1827 |

> `RefName`, `WorkspaceNameBuf`, `RemoteRefSymbol` live in `ref_name.rs`; `RefTarget`/`RemoteRef`
> in `op_store.rs` (already used by M1's `convert.rs`). Build `RefTarget::normal(commit_id)` for a
> non-conflicted bookmark; `RefTarget::absent()`/absent target to delete.

### 3.2 Commit construction — `CommitBuilder` (attached form)

`new_commit`/`rewrite_commit` return an attached `CommitBuilder<'_>` (commit_builder.rs:42).
Chainable setters consume `self`; `write` consumes and records the rewrite:

| Setter / action | Ref |
|---|---|
| `set_parents(Vec<CommitId>)` / `set_tree(MergedTree)` | commit_builder.rs:66/88 |
| `set_description(impl Into<String>)` | commit_builder.rs:116 |
| `set_author(Signature)` / `set_committer(Signature)` | commit_builder.rs:125/134 |
| `is_empty(&self) -> BackendResult<bool>` | commit_builder.rs:94 |
| **`write(self) -> BackendResult<Commit>`** | commit_builder.rs:163 |
| `abandon(self)` | commit_builder.rs:169 |

Author/committer default from `UserSettings` (§2.3). `rewrite_commit().write()` records the
rewrite so a following `rebase_descendants()` fixes up children + bookmarks + the `@` pointer.

### 3.3 Rewrite primitives (rebase / squash / restore)

| What | Signature | Ref |
|---|---|---|
| Merge commit trees | `merge_commit_trees(repo, commits: &[Commit]) -> BackendResult<MergedTree>` (async) | rewrite.rs:57 |
| Restore paths | `restore_tree(source, destination, src_label, dst_label, matcher) -> BackendResult<MergedTree>` (async) | rewrite.rs:119 |
| Rewriter | `CommitRewriter::{new, set_new_parents, rebase()/reparent()}` | rewrite.rs:200+ |
| Rebase one commit | `rebase_commit_with_options(...)` ; `EmptyBehavior` ; `RebaseOptions` | rewrite.rs:399/469/490 |
| **Move/rebase a set** | `move_commits(...)` / `compute_move_commits(...)`, `MoveCommitsTarget`, `MoveCommitsLocation` | rewrite.rs:585/593/532/525 |
| **Squash** | `squash_commits(repo, sources: &[CommitWithSelection], destination, keep_emptied) -> BackendResult<Option<SquashedCommit>>` | rewrite.rs:1268 |
| Squash result | `SquashedCommit { commit_builder, abandoned_commits }` (caller sets description + `write`) | rewrite.rs:1258 |
| Selection wrapper | `CommitWithSelection { is_full_selection, is_empty_selection, diff_with_labels }` | rewrite.rs:1207 |

- **`restore`** (concept's `tx.restore`): build a tree with `restore_tree(from_tree, to_tree, …,
  matcher)` then `rewrite_commit(target).set_tree(new_tree).write()` + `rebase_descendants()`.
  For whole-commit restore use an `EverythingMatcher`; for path-scoped, a `FilesMatcher`
  (`matchers.rs`).
- **`rebase`**: for the common "rebase commit (and descendants) onto new parents," use
  `move_commits` with `MoveCommitsTarget::Commits([…])` + a `MoveCommitsLocation` of new
  parents/children, then `rebase_descendants()`. Verify exact field names at slice 8 against
  rewrite.rs:525–600 (they are CLI-internal but public).
- **`squash`**: `squash_commits` returns a builder; set description, `write()`, then
  `rebase_descendants()`. Sources that are fully selected are abandoned.

### 3.4 Working copy (snapshot / checkout / lock / stale)

| What | Signature | Ref |
|---|---|---|
| WC trait | `WorkingCopy: { operation_id() -> &OperationId, tree() -> &MergedTree, start_mutation() }` | working_copy.rs:53/62/65/75 |
| Begin WC mutation (locks) | `Workspace::start_working_copy_mutation(&mut self) -> Result<LockedWorkspace<'_>, _>` | workspace.rs:427 |
| Locked WC | `LockedWorkspace::{locked_wc() -> &mut dyn LockedWorkingCopy, finish(OperationId)}` | workspace.rs:471/475 |
| Snapshot | `LockedWorkingCopy::snapshot(&mut self, &SnapshotOptions) -> Result<(MergedTree, SnapshotStats), SnapshotError>` (async) | working_copy.rs:118 |
| Check out | `LockedWorkingCopy::check_out(&mut self, &Commit) -> Result<CheckoutStats, CheckoutError>` (async) | working_copy.rs:124 |
| Old op/tree at lock | `old_operation_id()` / `old_tree()` | working_copy.rs:112/115 |
| Finish (unlock+save) | `LockedWorkingCopy::finish(self: Box<Self>, OperationId) -> Result<Box<dyn WorkingCopy>, _>` | working_copy.rs:152 |
| High-level checkout | `Workspace::check_out(op_id, old_tree: Option<&MergedTree>, commit) -> Result<CheckoutStats, CheckoutError>` | workspace.rs:437 |
| Snapshot opts | `SnapshotOptions { base_ignores, progress, start_tracking_matcher, force_tracking_matcher, max_new_file_size }` | working_copy.rs:212 |
| **Stale check** | `WorkingCopyFreshness::check_stale(locked_wc, wc_commit, repo) -> Result<Self, OpStoreError>` | working_copy.rs:363 |
| Freshness states | `Fresh / Updated(Box<Operation>) / WorkingCopyStale / SiblingOperation` | working_copy.rs:347 |

`Workspace::check_out` (workspace.rs:437) already wraps lock → `ConcurrentCheckout` guard (if
`old_tree` mismatches) → `check_out` → `finish(op_id)`. **Use it** for post-rewrite checkout; only
hand-roll the locked sequence for the auto-snapshot path (which needs the snapshot tree *before*
the tx exists).

### 3.5 Snapshot mechanism (the exact sequence)

`ws.snapshot()` / auto-snapshot reproduces the CLI's `snapshot working copy` op:

```
1. locked_ws = workspace.start_working_copy_mutation()?          // WC lock; off-GIL
2. freshness = WorkingCopyFreshness::check_stale(locked_ws.locked_wc(), &wc_commit, &repo)?
   - WorkingCopyStale | SiblingOperation  -> raise StaleWorkingCopyError (no snapshot)
   - Updated(op)                           -> reload repo at that op, retry (WC moved under us)
   - Fresh                                 -> continue
3. (new_tree, stats) = locked_ws.locked_wc().snapshot(&options).block_on()?   // off-GIL
4. if new_tree.id() == wc_commit.tree().id():  drop lock, return None         // clean: NO op
5. tx = repo.start_transaction()
6. new_wc = tx.repo_mut().rewrite_commit(&wc_commit).set_tree(new_tree).write()?
7. tx.repo_mut().rebase_descendants()?                            // satisfies has_rewrites assert
8. tx.set_is_snapshot(true)
9. new_repo = tx.commit("snapshot working copy")?                 // advances @, is_snapshot op
10. locked_ws.finish(new_repo.op_id().clone())?                   // save WC state at new op
11. self.repo = new_repo
```

`SnapshotOptions` fields come from settings/git-ignore; replicate the CLI's defaults
(`max_new_file_size`, base ignores from repo `.gitignore`). Confirm the CLI's defaulting at slice
5 (`local_working_copy.rs` + the cli's `snapshot` helper analog).

### 3.6 Post-rewrite checkout (when a tx rewrites `@`)

After a mutation transaction commits and `@`'s commit changed, the on-disk tree must be updated:

```
1. new_wc_commit = new_repo.store().get_commit(new_repo.view().get_wc_commit_id(name))   // off-GIL
2. stats = workspace.check_out(new_repo.op_id().clone(), Some(&old_wc_tree), &new_wc_commit)?
   // workspace.rs:437 -> ConcurrentCheckout if disk moved; CheckoutError mapped to WorkingCopyError
```

`check_out` takes the WC lock, guards concurrent disk changes, checks out, and `finish`es at the
new op id — keeping the WC's recorded op in lockstep with the repo head.

### 3.7 Op log: undo / restore

| What | Building block | Ref |
|---|---|---|
| Resolve op spec | `op_walk::resolve_op_for_load(loader, op_str)` (M1 already uses) | op_walk.rs:89 |
| Load repo at op | `RepoLoader::load_at(&op)` / `load_at_head()` | repo.rs:767/756 |
| Walk ancestors | `op_walk::walk_ancestors(&[op])` (M1 uses) | op_walk.rs:257 |
| **Undo** = reverse-merge | `tx.repo_mut().merge(&op_repo, &op_parent_repo)` | repo.rs:1831 |
| **Restore** = adopt past view | `tx.repo_mut().set_view(target_view)` | repo.rs:1827 |

- **`ws.undo()`** (revert the last op, default `@`): start a tx at head; load the to-undo op's repo
  and its single parent's repo; `merge(base = op_repo, other = parent_repo)` applies the *reverse*
  of that op into head; commit `"undo operation <id>"`. (Building block is `merge`; **exact view
  semantics are pinned by the differential test** against `jj undo` — assert identical resulting
  view, not internal steps. If `merge`-based undo drifts from the CLI's view-restore for partial
  cases, fall back to replicating the CLI's `view_with_desired_portions_restored`; flagged at
  slice 7.)
- **`ws.restore_operation(op)`**: start a tx at head; load target op; `set_view(target view)`;
  commit `"restore to operation <id>"`. Then run the §3.6 checkout so `@` on disk follows.
- After undo/restore, if `@`'s commit changed, run §3.6 checkout.

### 3.8 Workspace management (slice 9)

| What | Ref |
|---|---|
| `Workspace::init_internal_git` / `init_colocated_git` / `init_external_git` | workspace.rs:205/221/253 |
| `Workspace::init_workspace_with_existing_repo` (add_workspace substrate) | workspace.rs:358 |
| `MutableRepo::set_wc_commit` / `check_out` (allocate new workspace's `@`) | repo.rs:1458/1514 |
| `View::remove_wc_commit` via `MutableRepo::remove_wc_commit` (forget) | repo.rs:1470 |
| `MutableRepo::rename_workspace` | repo.rs:1506 |

`add_workspace` is **eager** (concept §11): one operation that allocates the `WorkspaceId` + sets
its `@` in the shared view, **then** checks out files at the new path, returning a `Workspace`
bound there. Error if the target path exists and is non-empty. (`init_workspace_with_existing_repo`
+ a checkout reproduces `jj workspace add`.)

### 3.9 Git interop (slices 10–11; heaviest, network)

| What | Signature | Ref |
|---|---|---|
| Import refs | `import_refs(mut_repo, options) -> Result<GitImportStats, GitImportError>` | git.rs:529 |
| Import HEAD | `import_head(mut_repo)` | git.rs:983 |
| Export refs | `export_refs(mut_repo) -> Result<GitExportStats, GitExportError>` | git.rs:1103 |
| Fetch | `GitFetch::new(mut_repo, GitSubprocessOptions, &GitImportOptions)` → `.fetch(remote, refspecs, callback, depth, tags)` → `.import_refs()` | git.rs:2756/2779/2883 |
| Push | `push_branches(...)` / `push_updates(mut_repo, …, &[GitRefUpdate])` → `GitPushStats` | git.rs:2945/3011/170 |
| Remotes | `get_all_remote_names` / `add_remote` / `remove_remote` / `rename_remote` / `set_remote_urls` | git.rs:2098/2116/2173/2230/2332 |
| Errors | `GitFetchError` / `GitPushError` / `GitImportError` / `GitExportError` / `GitRemoteManagementError` | git.rs:2401/2918/452/1022/1897 |

> Git fetch/push are **subprocess-driven** (`GitSubprocessOptions`, `GitSubprocessContext`,
> git_subprocess.rs) over the `gix` backend — they shell out to `git`, need a callback, and are
> network-bound. They are wrapped in `import_refs`/`export_refs` transactions. Treat slices 10–11
> as a sub-project: release the GIL for all of it, map each error enum to `BackendError` (or a new
> `GitError ⊂ BackendError` if the taxonomy proves too coarse — decide at slice 10), and gate the
> network tests behind a local bare-repo fixture (already in `bookmarked_repo`).

---

## 4. Vertical build order (one differential test per slice)

Each slice: thin Rust + Python facade + a differential test asserting **change graph + bookmarks
+ op-log effect** vs the pinned CLI, plus the "1 tx == 1 op" invariant. Ship after each.

| # | Slice | Brings up | Key API | Differential assertion |
|---|---|---|---|---|
| 0 | **Identity + tx scaffolding** | Real `UserSettings` (§2.3); `PyWorkspace.{repo,tx}` fields; `begin/commit/rollback_transaction`; Python `Workspace.transaction()` ctx mgr + `Transaction` token | start_transaction (326), commit (120) | Empty tx commits exactly 1 op with given description; tx that raises commits 0 ops |
| 1 | **`describe`** (simplest mutation) | tx machinery on one commit; immediate `Commit` return (decision 2) | `rewrite_commit` (954) → `set_description` (116) → `write` (163) → `rebase_descendants` (1428) | Description + commit_id match `jj describe`; change_id stable; 1 op |
| 2 | **`new`** | parentless/​multi-parent commit creation; `@` advance + checkout | `new_commit` (948) → `write`; `edit`/`set_wc_commit` (1526/1458); §3.6 checkout | New commit's parents + `@` match `jj new`; on-disk `@` checked out; 1 op |
| 3 | **`edit` / `abandon`** | point `@` at existing; abandon + descendant rebase | `edit` (1526); `record_abandoned_commit` (1005) → `rebase_descendants` | Graph after abandon == `jj abandon`; `@` after edit == `jj edit`; immutable-commit edit → `ImmutableCommitError` |
| 4 | **Bookmark writes** | create/set/move/delete/forget | `set_local_bookmark_target` (1676), `RefTarget::{normal,absent}`; remote track/untrack | Bookmark rows == `jj bookmark …` (reuse M1 bookmark diff); 1 op each |
| 5 | **`snapshot` + auto-snapshot** | §3.5 sequence; WC lock; `SnapshotOptions` defaults; auto-snapshot on tx open | start_working_copy_mutation (427), snapshot (118), finish (475), set_is_snapshot (115) | Dirty `@` + mutation ⇒ **2 ops** (`snapshot working copy` then op); clean ⇒ **1**; tree id matches CLI |
| 6 | **Stale-WC** | `is_stale()` + `update_stale()`; never operate on stale `@` | `WorkingCopyFreshness::check_stale` (363) | Cross-workspace rewrite ⇒ `is_stale()` true; mutation raises `StaleWorkingCopyError`; `update_stale()` ⇒ matches `jj workspace update-stale` |
| 7 | **`undo` / `restore_operation`** | reverse-merge + view-restore; post checkout | `merge` (1831), `set_view` (1827), load_at (767) | Resulting view == `jj undo` / `jj op restore`; op count +1; `@` on disk follows |
| 8 | **`rebase` / `squash` / `restore`** | rewrite primitives | move_commits (585), squash_commits (1268), restore_tree (119) → rewrite + `rebase_descendants` | Graph + trees == `jj rebase`/`jj squash`/`jj restore`; conflicts faithful (N-sided) |
| 9 | **Workspace mgmt** | `init`, `add_workspace` (eager), `forget_workspace` | workspace.rs:205/221/358; set_wc_commit/check_out; remove_wc_commit | New workspace's `@` + record == `jj workspace add`; forget == `jj workspace forget`; init colocated/internal |
| 10 | **Git import/export + remotes** | colocated sync; remote CRUD | import_refs (529), export_refs (1103), add_remote (2116) | Refs after import/export == `jj git import`/`export`; remotes == `jj git remote list` |
| 11 | **Git fetch / push** | network slice (subprocess) | GitFetch (2756), push_branches (2945) | Against local bare-repo fixture: bookmarks + remote refs == `jj git fetch`/`push` |

Slices 0–8 are the mutation core (shippable as `0.40.0a`/feature-complete writes); 9 adds
workspaces; 10–11 add git. The milestone can release incrementally at any slice boundary.

---

## 5. Pydantic / facade surface

### 5.1 Python facade (`python/pyjutsu/`)

```python
# workspace.py — new write surface on Workspace
class Workspace:
    def transaction(self, description: str, *, auto_snapshot: bool = True) -> Transaction: ...
    #   -> context manager; __enter__ snapshots @ (if auto_snapshot & dirty) then start_transaction

    def snapshot(self) -> Operation | None: ...      # explicit; None if @ was clean (no op)
    def is_stale(self) -> bool: ...
    def update_stale(self) -> Commit: ...            # checkout @ to its recorded commit

    def undo(self, op: str | None = None) -> Operation: ...          # default: last op
    def restore_operation(self, op: str) -> Operation: ...

    # slice 9
    @classmethod
    def init(cls, path, *, colocate: bool = False) -> "Workspace": ...
    def add_workspace(self, path, name: str, at: str = "@") -> "Workspace": ...
    def forget_workspace(self, name: str) -> None: ...
    def workspaces(self) -> list[WorkspaceInfo]: ...

    # slices 10-11
    def remotes(self) -> list[Remote]: ...
    def git_fetch(self, remote: str = "origin", *, branch: str | None = None) -> None: ...
    def git_push(self, *, bookmark: str, remote: str = "origin", allow_new: bool = False) -> None: ...
    def git_import(self) -> None: ...
    def git_export(self) -> None: ...
```

```python
# transaction.py — NEW. Thin token over the workspace's open native tx.
class Transaction:
    def new(self, parents: list[str] | None = None) -> Commit: ...
    def describe(self, commit: str, message: str) -> Commit: ...
    def edit(self, commit: str) -> Commit: ...
    def abandon(self, commit: str) -> None: ...
    def rebase(self, commit: str, *, onto: list[str]) -> Commit: ...
    def squash(self, source: str, into: str, *, message: str | None = None) -> Commit: ...
    def restore(self, commit: str, *, from_: str, paths: list[str] | None = None) -> Commit: ...
    def create_bookmark(self, name: str, commit: str) -> Bookmark: ...
    def set_bookmark(self, name: str, commit: str) -> Bookmark: ...        # create-or-move
    def delete_bookmark(self, name: str) -> None: ...
    # __enter__/__exit__: commit on clean exit, rollback on exception (decision: atomicity)
```

`commit`/`parents` args accept change ids **or** commit ids **or** revset strings → resolved
through the existing revset pipeline (revset.rs) inside the open `MutableRepo`. Mutations return
**full `Commit` models** (decision 2), validated via the existing `CommitData → dict → model`
path, read back from the `MutableRepo` (impls `Repo`).

### 5.2 New / extended models

- **`WorkspaceInfo`** (slice 9): `{name: str, path: Path, wc_commit_id: CommitId}` from the shared
  view's `wc_commit_ids` (concept §11).
- **`Remote`** (slice 10): `{name: str, url: str}`.
- **No change** to `Commit`/`Bookmark`/`Operation`/`Conflict`/`DiffStat` shapes — M2 reuses them
  for return values (a freshly-written commit validates through the same `Commit` model).
- Frozen, `extra="forbid"` everywhere (drift tripwire), as M1.

### 5.3 Error taxonomy additions (decision 3) — `src/errors.rs` + `python/pyjutsu/errors.py`

```
PyjutsuError                       (existing base)
├─ RevsetError                     (existing)
├─ ConflictError                   (existing) — conflict blocks an op (e.g. squash into conflict)
├─ BackendError                    (existing) — store/op-store/index/git backend
├─ WorkspaceError                  (existing) — load/init/forget
├─ WorkingCopyError                NEW — WC lock contention, checkout failure (CheckoutError::*)
│   └─ StaleWorkingCopyError       NEW — operate-on-stale-@ (WorkingCopyStale/SiblingOperation)
└─ ImmutableCommitError            NEW — rewrite/abandon of root or policy-immutable commit
```

Map: `RewriteRootCommit` (repo.rs:2028) + `EditCommitError::RewriteRootCommit` → `ImmutableCommitError`;
`CheckoutError::ConcurrentCheckout`/`Other` (working_copy.rs:278) → `WorkingCopyError`;
`WorkingCopyFreshness::{WorkingCopyStale,SiblingOperation}` → `StaleWorkingCopyError`;
`TransactionCommitError` (transaction.rs:45) → `BackendError`; git error enums → `BackendError`
(or `GitError ⊂ BackendError`, decided at slice 10). Add `map_*` helpers mirroring M1's style; no
`jj-lib` error type crosses the FFI (only `Display`).

---

## 6. Testing strategy

Reuse the M1 harness (`tests/diff/jj_cli.py`, `tests/conftest.py` fixtures, golden
`tests/golden/model_fields.json`, op-count invariant). Extend the `JjCli` driver with the write
verbs it lacks (it currently only reads).

1. **Per-mutation differential tests** (one module per slice). For each mutation: apply it via
   Pyjutsu **and** the pinned CLI to identical scratch repos; assert equal
   - **change graph** (`jj.change_ids("::@")` ordering + parents),
   - **bookmarks** (reuse `JjCli.bookmarks`),
   - **op-log effect** (`JjCli.op_log_ids` count + the new head op's description/tags/`is_snapshot`).
2. **"1 tx == 1 op" invariant** (extends M1's op-count test): a mutation transaction on a **clean**
   `@` adds exactly **1** operation; on a **dirty** `@` adds exactly **2** (snapshot + mutation),
   matching the verified CLI behavior (§0.1). A tx whose body raises adds **0**.
3. **Rollback-on-exception test**: open a tx, perform a mutation, raise inside the `with`; assert
   head op + graph + bookmarks are byte-identical to before (atomicity guarantee).
4. **Round-trip / property tests**: build a stack via Pyjutsu, read it back; assert change ids
   stable across `describe`/`rebase`, parents correct, conflicts faithfully N-sided after a
   conflicting `squash`/`rebase`.
5. **Stale-WC test** (slice 6): two `Workspace` handles on one repo; rewrite the base from one;
   assert the other reports `is_stale()` and refuses to mutate (`StaleWorkingCopyError`), then
   `update_stale()` reconciles to match `jj workspace update-stale`.
6. **Snapshot fidelity** (slice 5): dirty the WC, snapshot via both; assert equal tree id and that
   a clean WC snapshot produces **no** operation.
7. **Git slices (10–11)**: against a local bare-repo fixture (extend `bookmarked_repo`); assert
   refs/bookmarks parity after fetch/push/import/export. Network isolation: no real remotes.
8. **Golden model fields** regenerated for `WorkspaceInfo`/`Remote`; existing goldens unchanged.

Run everything through devenv: `devenv tasks run pyjutsu:{build,test,lint}`. Never bare
`cargo`/`maturin`/`pytest`/`jj`.

---

## 7. Risks & watch-outs (carried + new)

- **`has_rewrites` assert (transaction.rs:136):** forgetting `rebase_descendants()` before commit
  **aborts the process**. Centralize: every rewriting tx method ends by marking the tx "needs
  rebase," and `commit_transaction` runs `rebase_descendants()` once before `commit`. Covered by a
  panic-safety test (multi-rewrite tx).
- **GIL + blocking I/O:** snapshot, checkout, fetch/push are I/O/network-heavy → all under
  `Python::allow_threads` (M1 pattern). `block_on` (pollster) wraps the async WC/git calls, as
  jj-lib itself does (transaction.rs:21, workspace.rs:454).
- **Single open tx per workspace:** the `Mutex<Option<Transaction>>` enforces it; opening a second
  raises. Document that `Transaction` is not reentrant and not thread-shared.
- **Undo view semantics:** `merge`-based undo (§3.7) is the building block; the differential test
  is the contract. If a partial-restore case drifts from the CLI, replicate the CLI's
  view-portion restore — flagged at slice 7, not pre-solved.
- **Config/identity (§2.3):** the one place the binding reproduces CLI behavior (stacked config)
  not handed to it as a single call — verify the public loader at slice 0.
- **Git is a sub-project:** subprocess + `gix` + network; wheel-size/build-time concerns
  (concept §8.7). Land last; keep its error mapping and GIL discipline isolated.
- **Faithful primitives only:** no lane/frozen-trunk policy; conflicts stay first-class N-sided;
  divergent operations surfaced (concept §8.8/8.10), never hidden.

---

## 8. Definition of done (M2 → `0.40.0`)

- Slices 0–11 implemented, each with green differential tests (graph + bookmarks + op-log).
- "1 tx == 1 op" (clean) / "+ snapshot op" (dirty) / "0 ops on rollback" invariants enforced.
- New error subclasses raised on their failure modes, with tests.
- `Cargo.lock` committed; pin unchanged at `=0.38.0`; `JJ_VERSION == JJ_LIB_TARGET` tripwire green.
- Docs: concept §3/§5 status updated to "M2 implemented"; `__version__ = "0.40.0"`.
- No subprocess/CLI **backend**, no compat shim, no workflow policy, no AI attribution.
