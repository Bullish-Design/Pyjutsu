# Pyjutsu M2 — Slice 8 Implementation Guide (commit-rewrite mutations: `rebase` / `squash` / `restore`)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec; mutation list at §131–132) →
> `M2_CONTINUATION_GUIDE.md` (the corrected spine for slices 2–11; §1 corrections + §2 conventions +
> §4 slice plans — slice 8 plan at §4 "Slice 8") → **this document** (the detailed, verified plan for
> slice 8 specifically) → `M2_IMPLEMENTATION_GUIDE.md` (original plan; §3.3 rewrite primitives + the
> error taxonomy + API table — but the continuation guide and this doc win where they disagree) → the
> code it produces.
>
> **Pin unchanged:** `jj-lib = "=0.38.0"`. Slices 0–7 are committed and pushed on `main`
> (slice 7 = `5a12d30`); the working tree is clean — start from `main`. Slices 0–1 shipped as pyjutsu
> `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2 bumps to `0.41.0`. API refs below
> are `file:line` into `~/.cargo/registry/.../jj-lib-0.38.0/src/`, **verified against the pinned
> source while writing this guide.**

---

## 0. Where slice 8 starts

Slices 0–7 built the mutation surface inside a `Transaction` (`describe`/`new`/`edit`/`abandon` +
five bookmark verbs), `snapshot` + auto-snapshot, the stale-WC surface, and the op-log writes
(`undo`/`restore_operation`). This slice adds the **three remaining commit-rewrite mutations** the
concept lists under "Mutations (in a `Transaction`)" (`docs/PYJUTSU_CONCEPT.md:131`):

1. **`tx.rebase(commit, *, onto) -> Commit`** — move `commit` **and its descendants** onto new
   parents `onto`. Matches `jj rebase -s <commit> -d <onto…>`.
2. **`tx.squash(source, into, *, message=None) -> Commit`** — move `source`'s changes into `into`,
   abandoning `source` when fully squashed. Matches `jj squash --from <source> --into <into>`.
3. **`tx.restore(commit, *, from_, paths=None) -> Commit`** — replace `commit`'s content (or just
   `paths`) with `from_`'s. Matches `jj restore --from <from_> --into <commit> [paths…]`.

All three are **`PyTransaction` methods** (transaction methods, *not* `Workspace`-level — unlike
slice 7), so they slot straight into the existing `#[pymethods] impl PyTransaction` block in
`src/transaction.rs` and reuse everything already there:

- **`resolve_single(&self, repo, revset_str)`** (`src/transaction.rs:101`) — resolve a one-revision
  revset against the open `MutableRepo` (sees in-flight rewrites). Use it for every revset arg.
- **The describe/abandon pattern** (`src/transaction.rs:128`, `:229`): take `tx.repo_mut()` on the
  GIL (`MutableRepo` is `!Send`), do the rewrite primitive, **`rebase_descendants()`**, read the
  result back with `CommitData::build(&*repo, &c)`, return the dict.
- **Centralized commit tail** (`src/transaction.rs:379`): `PyTransaction::commit` already re-runs
  `rebase_descendants()` (idempotent) and drives the **post-commit on-disk checkout** when `@` moved
  ([[m2-slice2-new-checkout]]). So a `rebase`/`squash`/`restore` that moves `@` (e.g. squashing `@`,
  or rebasing `@`'s ancestor) gets the working-copy update **for free** — you write nothing new for
  the checkout. Just call `rebase_descendants()` inside the method (so the returned `Commit` already
  reflects moved bookmarks/descendants), exactly like `describe`.
- **`_complete_newline`** (`python/pyjutsu/transaction.py:24`) — the trailing-newline normalizer
  applied to every message that crosses FFI, so commit ids match the CLI ([[m2-differential-mutation-testing]]).
  Apply it to `squash`'s `message`.

> **The one structural rule that makes this slice safe** ([[m2-slice7-undo-restore]] §1, continuation
> guide §1.2): **rewriting the root commit panics** — `record_rewrites`/`record_rewritten_commit`
> `assert_ne!` on the root id (repo.rs), surfacing as a bare `PanicException` through PyO3. `abandon`
> and `edit` already guard this. **Guard the root in all three new methods** (the `commit`/target
> being rewritten) and raise `ImmutableCommitError`, matching the established pattern
> (`src/transaction.rs:237`). Only the root is enforced; jj's configurable `immutable_heads()` set is
> CLI workflow policy the thin layer deliberately does not replicate (carried decision).

---

## 1. Verified jj-lib APIs (against the pinned 0.38.0 source)

| What | Signature / fact | Ref |
|---|---|---|
| **Move/rebase a set** | `move_commits(&mut MutableRepo, loc: &MoveCommitsLocation, opts: &RebaseOptions) -> BackendResult<MoveCommitsStats>` | rewrite.rs:585 |
| Move location | `struct MoveCommitsLocation { new_parent_ids: Vec<CommitId>, new_child_ids: Vec<CommitId>, target: MoveCommitsTarget }` | rewrite.rs:525 |
| Move target | `enum MoveCommitsTarget { Commits(Vec<CommitId>), Roots(Vec<CommitId>) }` — **`Roots` = those commits + all descendants** | rewrite.rs:532 |
| Rebase options | `struct RebaseOptions { empty: EmptyBehavior, … }`; `Default` ⇒ `EmptyBehavior::Keep` (matches plain `jj rebase`) | rewrite.rs:490/469 |
| **Squash** | `squash_commits<'repo>(&'repo mut MutableRepo, sources: &[CommitWithSelection], destination: &Commit, keep_emptied: bool) -> BackendResult<Option<SquashedCommit<'repo>>>` | rewrite.rs:1268 |
| Squash input | `struct CommitWithSelection { commit: Commit, selected_tree: MergedTree, parent_tree: MergedTree }` — full-commit selection ⇒ `selected_tree = commit.tree()`, `parent_tree = commit.parent_tree(repo)?` | rewrite.rs:1207 |
| Squash result | `struct SquashedCommit<'repo> { commit_builder: CommitBuilder<'repo>, abandoned_commits: Vec<Commit> }` — caller sets description + `write()` | rewrite.rs:1258 |
| `None` result | `squash_commits` returns `Ok(None)` when nothing is selected to squash (e.g. empty source kept) | rewrite.rs:1303 |
| **Restore tree** | `restore_tree(source: &MergedTree, destination: &MergedTree, source_label: String, destination_label: String, matcher: &dyn Matcher) -> BackendResult<MergedTree>` (**async**) | rewrite.rs:119 |
| Restore orientation | result = `destination` with **matched paths taken from `source`**; `EverythingMatcher` short-circuits to `source.clone()` (whole-commit restore) | rewrite.rs:127, 172–186 |
| Commit tree | `Commit::tree(&self) -> MergedTree`; `Commit::parent_tree(&self, &dyn Repo) -> BackendResult<MergedTree>` | commit.rs:124/136 |
| Rewrite one commit | `MutableRepo::rewrite_commit(&mut self, &Commit) -> CommitBuilder` → `.set_tree(MergedTree)` → `.write() -> BackendResult<Commit>` | (used by `describe`/`snapshot`) |
| Builder (squash) | `CommitBuilder::set_description(self, impl Into<String>) -> Self`; `.write(self) -> BackendResult<Commit>` (consumes ⇒ releases the `&mut repo` borrow) | commit_builder.rs:116/163 |
| Rebase descendants | `MutableRepo::rebase_descendants(&mut self) -> BackendResult<usize>` | repo.rs:1428 |
| Whole-commit matcher | `EverythingMatcher` (unit struct) | matchers.rs |
| Path matcher | `FilesMatcher::new(impl IntoIterator<Item = impl AsRef<RepoPath>>)`; build paths via `RepoPathBuf::from_relative_path(p)` | matchers.rs:138, repo_path.rs:263 |

Notes verified in source:

- **`move_commits` argument shape.** Build one `MoveCommitsLocation { new_parent_ids: <onto>,
  new_child_ids: vec![], target: MoveCommitsTarget::Roots(vec![commit_id]) }` and call
  `move_commits(repo, &loc, &RebaseOptions::default())`. `Roots([X])` rebases **X and its
  descendants** onto `new_parent_ids` — exactly `jj rebase -s X -d <onto>`. (`Commits([X])` would
  rebase X alone and reattach its children to X's old parents = `jj rebase -r`; that is the
  documented refinement, **out of scope** for v1 — see §6.) Then call `rebase_descendants()` (the
  move records rewrites just like `merge` did in slice 7).
- **`squash_commits` borrow discipline (the trap this slice).** It takes `&'repo mut MutableRepo` and
  returns a `SquashedCommit<'repo>` whose `commit_builder` **holds that mutable borrow**. So: resolve
  `source`/`into` and build the `CommitWithSelection` **before** the call; after the call you may not
  touch `repo` until `commit_builder.write()` consumes the builder and releases the borrow; only then
  `repo.rebase_descendants()`. Sequence: `build sels → squash_commits → (Option) → set_description?
  → write() → rebase_descendants() → re-resolve into → return`.
- **`restore_tree` is async** (like `snapshot`'s `LockedWorkingCopy::snapshot`): wrap it in
  `pollster::block_on(...)` (already the pattern; `merge_commit_trees` is called that way at
  `src/transaction.rs:184`). **Orientation:** to restore `commit` *from* `from_`, pass
  `source = from_commit.tree()`, `destination = commit.tree()`, so matched paths come from `from_`
  and the rest stay as `commit` had them. With `paths=None` use `EverythingMatcher` ⇒ the whole tree
  becomes `from_`'s.
- **Reading the result back.** `move_commits`/`squash`/`restore` all **rewrite** the target commit
  (new commit id; change id stable). Re-resolve the *same revset string* after `rebase_descendants()`
  to get the rewritten commit — exactly as `edit` re-resolves (`src/transaction.rs:216`) — then
  `CommitData::build(&*repo, &c)`. For `rebase`, re-resolve `commit`; for `squash`/`restore`,
  re-resolve `into`/`commit` respectively.
- **No op-id determinism issues here** — these are `Transaction` mutations whose result is a *commit*
  (deterministic id under the pinned timestamp), so the differential tests assert **commit/change
  ids + graph + trees**, the same way `test_describe`/`test_new` do — not op ids.

Imports to add to `src/transaction.rs` (grep before adding duplicates):
`use jj_lib::rewrite::{move_commits, squash_commits, restore_tree, CommitWithSelection,
MoveCommitsLocation, MoveCommitsTarget, RebaseOptions};` (extend the existing
`use jj_lib::rewrite::merge_commit_trees;`), `use jj_lib::matchers::{EverythingMatcher, FilesMatcher};`
(only if path-scoped restore lands this slice), `use jj_lib::repo_path::RepoPathBuf;` (ditto).
`CommitId`, `ObjectId`, `Commit`, `Repo`, `CommitData`, `ImmutableCommitError`, `PyjutsuError`,
`map_backend_err` are already in scope.

---

## 2. Rust: three `#[pymethods]` on `PyTransaction` (`src/transaction.rs`)

Each opens with the **same preamble** the other mutations use (borrow the tx, error if closed, take
`repo = tx.repo_mut()` on the GIL) and **guards the root**. Sketches (adapt names/comments to match
the surrounding style — dense rationale comments, `&*repo` for the read-back):

```rust
/// Rebase `commit` and its descendants onto `onto` (each a single-revision revset) — matches
/// `jj rebase -s <commit> -d <onto…>`. Returns the rebased `commit` (its change id is preserved;
/// the commit id changes). The on-disk working copy follows when the tx commits if `@` moved.
#[pyo3(signature = (commit, onto))]
fn rebase<'py>(&self, py: Python<'py>, commit: &str, onto: Vec<String>) -> PyResult<Bound<'py, PyDict>> {
    // preamble → repo = tx.repo_mut()
    let target = self.resolve_single(&*repo, commit)?;
    if target.id() == repo.store().root_commit_id() {
        return Err(ImmutableCommitError::new_err("cannot rebase the root commit"));
    }
    let new_parent_ids = onto.iter()
        .map(|r| Ok(self.resolve_single(&*repo, r)?.id().clone()))
        .collect::<PyResult<Vec<_>>>()?;
    let loc = MoveCommitsLocation {
        new_parent_ids,
        new_child_ids: vec![],
        target: MoveCommitsTarget::Roots(vec![target.id().clone()]),
    };
    move_commits(repo, &loc, &RebaseOptions::default()).map_err(map_backend_err)?;
    repo.rebase_descendants().map_err(map_backend_err)?;
    let rebased = self.resolve_single(&*repo, commit)?; // re-read post-rebase (id changed)
    CommitData::build(&*repo, &rebased)?.to_dict(py)
}

/// Squash `source`'s changes into `into` (single-revision revsets) — matches `jj squash`.
/// `source` is abandoned when fully squashed; its descendants rebase. With `message`, the squashed
/// commit takes it; without, `into`'s description is kept. Returns the squashed `into`.
#[pyo3(signature = (source, into, message=None))]
fn squash<'py>(&self, py: Python<'py>, source: &str, into: &str, message: Option<&str>) -> PyResult<Bound<'py, PyDict>> {
    // preamble → repo = tx.repo_mut()
    let src = self.resolve_single(&*repo, source)?;
    let dst = self.resolve_single(&*repo, into)?;
    let root = repo.store().root_commit_id();
    if src.id() == root || dst.id() == root {
        return Err(ImmutableCommitError::new_err("cannot squash the root commit"));
    }
    if src.id() == dst.id() {
        return Err(PyjutsuError::new_err("cannot squash a commit into itself"));
    }
    let sel = CommitWithSelection {
        selected_tree: src.tree(),
        parent_tree: src.parent_tree(&*repo).map_err(map_backend_err)?,
        commit: src,
    };
    let squashed = squash_commits(repo, &[sel], &dst, /*keep_emptied=*/ false)
        .map_err(map_backend_err)?
        .ok_or_else(|| PyjutsuError::new_err("nothing to squash"))?;
    let mut builder = squashed.commit_builder;          // holds &mut repo until write()
    if let Some(msg) = message {
        builder = builder.set_description(msg);          // message already newline-normalized in Python
    }
    builder.write().map_err(map_backend_err)?;           // consumes builder → releases borrow
    repo.rebase_descendants().map_err(map_backend_err)?;
    let result = self.resolve_single(&*repo, into)?;     // re-read squashed `into`
    CommitData::build(&*repo, &result)?.to_dict(py)
}

/// Restore `commit`'s content (or just `paths`) from `from_` (single-revision revsets) — matches
/// `jj restore --from <from_> --into <commit> [paths…]`. Returns the rewritten `commit`.
#[pyo3(signature = (commit, from_, paths=None))]
fn restore<'py>(&self, py: Python<'py>, commit: &str, from_: &str, paths: Option<Vec<String>>) -> PyResult<Bound<'py, PyDict>> {
    // preamble → repo = tx.repo_mut()
    let target = self.resolve_single(&*repo, commit)?;
    if target.id() == repo.store().root_commit_id() {
        return Err(ImmutableCommitError::new_err("cannot restore the root commit"));
    }
    let from = self.resolve_single(&*repo, from_)?;
    let from_tree = from.tree();
    let target_tree = target.tree();
    // matched paths come from `from_`; the rest stay as `commit` had them (rewrite.rs:172).
    let new_tree = match &paths {
        None => pollster::block_on(restore_tree(
            &from_tree, &target_tree, "from".into(), "into".into(), &EverythingMatcher,
        )),
        Some(ps) => {
            let repo_paths = ps.iter()
                .map(|p| RepoPathBuf::from_relative_path(p).map_err(/*→ PyjutsuError*/ …))
                .collect::<PyResult<Vec<_>>>()?;
            let matcher = FilesMatcher::new(&repo_paths);
            pollster::block_on(restore_tree(
                &from_tree, &target_tree, "from".into(), "into".into(), &matcher,
            ))
        }
    }.map_err(map_backend_err)?;
    repo.rewrite_commit(&target).set_tree(new_tree).write().map_err(map_backend_err)?;
    repo.rebase_descendants().map_err(map_backend_err)?;
    let restored = self.resolve_single(&*repo, commit)?;
    CommitData::build(&*repo, &restored)?.to_dict(py)
}
```

**Verify while implementing (don't assume — the slice-5/-7 lesson):**
- Grep `RepoPathBuf::from_relative_path`'s exact return/error type (repo_path.rs:263) and map its
  error to `PyjutsuError` (it's user-supplied path input, like a bad revset). If `from_relative_path`
  is awkward, `RepoPathBuf::from_internal_string` (251) is the fallback for already-`/`-separated
  paths — **pick one and confirm it round-trips a plain `"a.txt"` against `jj restore a.txt`.**
- Confirm `FilesMatcher::new` accepts `&Vec<RepoPathBuf>` (`AsRef<RepoPath>` bound) without a clone
  dance; the borrow must outlive the `restore_tree` call.
- Confirm the `squash_commits` borrow timing compiles (builder consumed by `write()` **before** any
  further `repo.*`). If the borrow checker fights, that's the signal you reordered something.
- `move_commits` on `Roots([@])` / squashing `@` moves `@`; **don't add a checkout here** — the
  centralized `commit` tail does it. Just confirm a test exercises it (squash `@` into `@-`).

---

## 3. Python facade + stubs

### `python/pyjutsu/transaction.py` (next to `abandon`)

```python
def rebase(self, commit: str, *, onto: str | list[str]) -> Commit:
    """Rebase ``commit`` **and its descendants** onto ``onto`` → the rebased :class:`Commit`.

    ``commit`` and each entry of ``onto`` are single-revision revsets. Matches ``jj rebase -s``.
    The change id is preserved; the commit id changes. Rebasing the root raises
    :class:`~pyjutsu.errors.ImmutableCommitError`. Must be called inside the ``with`` block.
    """
    targets = [onto] if isinstance(onto, str) else list(onto)
    return Commit.model_validate(self._require_open().rebase(commit, targets))

def squash(self, source: str, into: str, *, message: str | None = None) -> Commit:
    """Move ``source``'s changes into ``into`` → the squashed :class:`Commit` (matches ``jj squash``).

    ``source`` is abandoned when fully squashed; its descendants rebase onto its parent(s). With
    ``message`` the squashed commit takes it; without, ``into``'s description is kept. ``source``
    and ``into`` are single-revision revsets and must differ; squashing the root raises
    :class:`~pyjutsu.errors.ImmutableCommitError`. Must be called inside the ``with`` block.
    """
    msg = _complete_newline(message) if message is not None else None
    return Commit.model_validate(self._require_open().squash(source, into, msg))

def restore(self, commit: str, *, from_: str, paths: list[str] | None = None) -> Commit:
    """Replace ``commit``'s content (or just ``paths``) with ``from_``'s → the rewritten
    :class:`Commit` (matches ``jj restore --from <from_> --into <commit>``).

    ``commit`` and ``from_`` are single-revision revsets; ``paths`` (repo-relative) scope the
    restore, else the whole tree is restored. Restoring the root raises
    :class:`~pyjutsu.errors.ImmutableCommitError`. Must be called inside the ``with`` block.
    """
    return Commit.model_validate(self._require_open().restore(commit, from_, paths))
```

### `python/pyjutsu/_pyjutsu.pyi` (in `PyTransaction`, next to `abandon`)

```python
    def rebase(self, commit: str, onto: list[str]) -> dict[str, object]: ...
    def squash(self, source: str, into: str, message: str | None = ...) -> dict[str, object]: ...
    def restore(self, commit: str, from_: str, paths: list[str] | None = ...) -> dict[str, object]: ...
```

---

## 4. Differential tests (`tests/test_rewrite.py` — or three files; match the repo's convention)

Reuse the harness (`_copy_repo`, the `jj`/`linear_repo`/`scratch_repo`/`diffstat_repo`/`conflict_repo`
fixtures, `jj.commit_id`, `jj.change_id`, `jj.change_ids`, `jj.parent_commit_ids`, `jj.is_empty`,
`jj.diff_stat_totals`). **These results are commits with deterministic ids** (pinned timestamp), so
assert **commit/change ids + graph + trees** across two byte-identical copies (binding vs CLI), the
way `test_describe`/`test_new` do — *not* op ids.

> **Colocated caveat (slice 7 lesson, [[m2-slice7-undo-restore]]):** these mutations move `@`/rewrite
> commits but they go **forward** (new commit ids), so the colocated git-HEAD re-import that bit
> backward `undo`/`restore_operation` does **not** apply — reading the binding repo with `jj` is
> fine here, same as `test_new`/`test_describe`. Keep repos local-only anyway.

Suggested cases (apply the op to `other = _copy_repo(...)` via the CLI and compare):

- **`test_rebase_subtree_matches_cli`** *(headline)*: on `linear_repo`, rebase commit **B** (and its
  descendant **C** + `@`) onto **A**: binding `tx.rebase("<B change>", onto="<A change>")`; CLI
  `jj rebase -s <B> -d <A>`. Assert the returned commit's parent is **A** (`created.parent_ids ==
  [jj.commit_id(other, "<A>")]`), `jj.commit_id(scratch, "<B>") == jj.commit_id(other, "<B>")` (state
  parity), and `jj.change_ids(repo, "::@")` match across both. Confirms `Roots` carries descendants.
- **`test_rebase_root_raises`**: `tx.rebase("root()", onto="@")` → `ImmutableCommitError`.
- **`test_squash_into_parent_matches_cli`** *(headline)*: on `diffstat_repo` (`@-` edits files), make
  a change then squash `@-` into `@--` (or squash a described child into its parent). Binding
  `tx.squash("<src>", "<dst>", message="combined")`; CLI `jj squash --from <src> --into <dst> -m
  combined`. Assert `jj.commit_id` of the squashed `into` matches across both, the source change id
  is **gone** from `::@` on both (`<src> not in jj.change_ids(...)`), and the squashed commit's tree
  carries the source's file changes (`jj.diff_stat_totals` or file contents on disk match).
- **`test_squash_into_self_raises`** / **`test_squash_root_raises`**: `PyjutsuError` /
  `ImmutableCommitError` respectively.
- **`test_squash_no_message_keeps_destination_description`**: squash without `message`; assert the
  result's description equals `into`'s, matching `jj squash … --use-destination-message` (**verify
  that flag exists in the pinned 0.38 CLI** with `jj squash --help`; if not, pass `-m <dst desc>`).
- **`test_restore_whole_commit_matches_cli`** *(headline)*: on `linear_repo`, restore `@`'s content
  from **A** (so `@`'s tree becomes A's): binding `tx.restore("@", from_="<A change>")`; CLI
  `jj restore --from <A> --into @`. Assert the rewritten `@`'s tree matches A's across both
  (`jj.commit_id`/tree parity or on-disk files: only `a.txt` present after commit), change id stable.
- **`test_restore_paths_matches_cli`** *(if path-scoped restore lands)*: on `diffstat_repo`, restore
  only `a.txt` in `@-` from its parent; CLI `jj restore --from <p> --into <@-> a.txt`. Assert just
  that path reverted (other files unchanged) and state matches. **If `RepoPathBuf`/`FilesMatcher`
  proves fiddly, ship whole-commit restore only and defer path-scoping to a follow-up — note it in
  the module docstring** (it's the documented refinement, §6).
- **`test_*_outside_with_block_raises`**: each method raises `RuntimeError` when called on a
  non-entered transaction (mirror `test_new_outside_with_block_raises`).

Re-run the **whole** suite — these are additive `PyTransaction` methods; nothing in slices 0–7
changes, so every prior test (incl. `test_undo`/`test_snapshot`/`test_new`) must stay green. Confirm.

---

## 5. Build / verify / report

In the devenv shell:

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint
```

All green (pytest + `cargo test` + clippy `-D warnings` + ruff), then **stop and report at the slice
boundary** before slice 9 (workspace management: `init` / `add_workspace` / `forget_workspace`, a new
`WorkspaceInfo` model — continuation guide §4). Commit on `main`
(`Implement M2 slice 8: rebase / squash / restore`); **no AI attribution** anywhere.

---

## 6. Guardrails (carried)

- **Thin Rust, rich Python:** all three return a plain commit dict (via `CommitData`); the `Commit`
  model is built in Python. Never leak jj-lib types.
- **GIL discipline:** `MutableRepo`/`Transaction` are `!Send`, so the rewrite primitives run **on the
  GIL** (in-memory graph work + small object writes), exactly like `describe`/`new`. The heavy off-GIL
  path is the post-commit checkout, already centralized in `commit` ([[m2-transaction-not-send]]).
- **`rebase_descendants()` after every rewrite** (landmine #1: a violation aborts the process). Each
  method calls it before reading back; `commit` re-runs it idempotently (continuation guide §1.2).
- **Faithful primitive, simplest form:** `rebase` = `move_commits(Roots)` (`-s` semantics); `squash` =
  `squash_commits` whole-commit selection; `restore` = `restore_tree` + `set_tree`. Conflicts stay
  **first-class N-sided** — a squash/rebase/restore that produces a conflict is *allowed* (it is not
  an error; `jj` records the conflict), so do **not** map "result conflicts" to `ConflictError`.
- **Documented refinements, out of scope (flag, don't fake):** `jj rebase -r` (single-commit reattach
  via `MoveCommitsTarget::Commits`) and `-b` (whole-branch); `jj squash`'s description-combining
  default and partial/interactive selection; path-scoped restore if `FilesMatcher` is deferred;
  `EmptyBehavior` other than `Keep`. Note each in a docstring/test-module comment rather than
  hardcoding a half-version.
- **Root is the only immutable guard** (raise `ImmutableCommitError`); `immutable_heads()` policy is
  not replicated. Every fallible jj-lib call maps to a `PyjutsuError` subclass; only its `Display`
  crosses FFI. **Pin stays `=0.38.0`; `Cargo.lock` committed; everything through devenv** — never bare
  `cargo`/`maturin`/`pytest`/`jj`. The pinned `jj` 0.38.0 CLI is the differential oracle only.

> **Slice-8 traps (grepped against the pinned source while writing this guide; re-grep if doubted):**
> (a) `squash_commits` hands back a builder holding `&mut repo` — `write()` it before any further
> `repo.*`; (b) `restore_tree` is **async** and its `source`/`destination` orientation is
> "matched paths from `source`" (so `source = from_`, `destination = commit`); (c) `MoveCommitsTarget::
> Roots` (not `Commits`) gives the `-s` "commit + descendants" semantics; (d) rewriting the **root**
> panics — guard it. See [[m2-slice7-undo-restore]], [[m2-slice5-snapshot]].
