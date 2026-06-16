# Pyjutsu M2 — Slice 8 kickoff prompt (commit-rewrite mutations: `rebase` / `squash` / `restore`)

> Paste this as the first message in a clean session **inside the Pyjutsu repo**.
> Authority order: `docs/PYJUTSU_CONCEPT.md` (canonical spec) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_SLICE8_GUIDE.md` (**your spine for this slice — verified, detailed**) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_CONTINUATION_GUIDE.md` (the corrected slices 2–11 spine: §1 corrections, §1.2 centralized rebase_descendants, §2 conventions, §4 slice plans) → `.scratch/projects/03-pyjutsu-m2-mutations/M2_IMPLEMENTATION_GUIDE.md` (original plan; §3.3 rewrite primitives + build order/error taxonomy — but the continuation guide and slice guide win where they disagree) → this prompt (orientation + cadence). **This is an implementation session: the deliverable is shipped, tested code, one vertical slice.**

---

You are continuing **M2 (the write layer)** of **Pyjutsu** (`import pyjutsu`): the Pythonic +
Pydantic binding to **jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**, in-process, no subprocess
backend, no text parsing. **M0, M1 (read layer, released `0.39.0`), and M2 slices 0–7 are done,
committed, and pushed on `main`** (slice 7 = `5a12d30`; working tree clean — start from `main`).
Slices 0–1 shipped as pyjutsu `0.40.0`; slices 2+ ride under `0.40.0` until the **completed** M2
bumps to **`0.41.0`** (versioned independently of jj; pin stays `jj-lib = "=0.38.0"`).

**Read `M2_SLICE8_GUIDE.md` end to end first** — it is the verified, detailed plan for this slice
(APIs at `file:line` into the pinned 0.38.0 source, the three Rust method bodies with their borrow/
async/orientation traps called out, the Python wiring, and the differential tests). Then skim the
code you build directly on in `src/transaction.rs`: the existing `#[pymethods] impl PyTransaction`
mutations — **`describe`** (`src/transaction.rs:128`, the rewrite → `rebase_descendants()` → read-back
pattern), **`edit`** (`:205`, re-resolves the revset post-rebase to read the rewritten commit),
**`abandon`** (`:229`, the **root guard** raising `ImmutableCommitError`), **`resolve_single`**
(`:101`, single-revision revset against the open `MutableRepo`), and **`commit`** (`:379`, which
already **centralizes `rebase_descendants()` + the post-commit on-disk checkout** — so a rewrite that
moves `@` gets the working-copy update for free; you write no checkout code). Also skim `src/convert.rs`
(`CommitData::{build,to_dict}`, the commit row all three methods return), `python/pyjutsu/transaction.py`
(where the three facades go; note `_complete_newline` at `:24` for `squash`'s message), and
`tests/test_new.py` + `tests/test_describe.py` + `tests/conftest.py` (the differential harness:
`_copy_repo`, `jj.commit_id`/`change_ids`/`parent_commit_ids`/`is_empty`/`diff_stat_totals`, the
`linear_repo`/`diffstat_repo`/`conflict_repo` fixtures).

## What's already done (slices 0–7 — do not redo)

- **Slice 0/1** — identity + tx scaffolding; `tx.describe` with commit-id parity.
- **Slice 2** — `tx.new(parents=None)` + the reusable post-commit on-disk checkout in `commit`.
- **Slice 3** — `tx.edit` / `tx.abandon` (`map_edit_err`; root-abandon panic guard).
- **Slice 4** — five bookmark verbs (no checkout/rebase).
- **Slice 5** — `Workspace.snapshot()` + auto-snapshot on `Transaction.__enter__`.
- **Slice 6** — `Workspace.is_stale()` / `update_stale()`; stale `@` raises `StaleWorkingCopyError`.
- **Slice 7** — `Workspace.undo()` / `Workspace.restore_operation()` (op-log writes); `checkout_wc`
  refactored into `checkout_locked`. **Correction learned:** `merge` *does* register rewrites →
  `rebase_descendants()` is required after it (the guide had said otherwise).

## This slice: `rebase` / `squash` / `restore` (per `M2_SLICE8_GUIDE.md`)

The three remaining commit-rewrite mutations the concept lists (`docs/PYJUTSU_CONCEPT.md:131`), each a
**`PyTransaction` method** (not `Workspace`-level), each following the `describe`/`abandon` pattern
(rewrite on the GIL → `rebase_descendants()` → re-resolve + `CommitData::build` → return dict):

1. **`tx.rebase(commit, *, onto) -> Commit`** — `move_commits` with
   `MoveCommitsTarget::Roots(vec![commit])` + `MoveCommitsLocation { new_parent_ids: <onto>,
   new_child_ids: vec![], … }` + `RebaseOptions::default()`, then `rebase_descendants()`. `Roots`
   carries **commit + descendants** (= `jj rebase -s`). (`Commits` = `-r` single-commit reattach is
   the out-of-scope refinement.)
2. **`tx.squash(source, into, *, message=None) -> Commit`** — build a whole-commit
   `CommitWithSelection { commit: src, selected_tree: src.tree(), parent_tree: src.parent_tree(repo)? }`,
   `squash_commits(repo, &[sel], &dst, keep_emptied=false) -> Option<SquashedCommit>`; set the
   description on `commit_builder` (with `message`, else keep `into`'s), **`write()` to release the
   `&mut repo` borrow**, then `rebase_descendants()`. `None` ⇒ "nothing to squash" `PyjutsuError`.
3. **`tx.restore(commit, *, from_, paths=None) -> Commit`** — `restore_tree(source = from_.tree(),
   destination = commit.tree(), …, matcher)` (**async ⇒ `pollster::block_on`**; matched paths come
   from `source`), `EverythingMatcher` for whole-commit (`FilesMatcher`+`RepoPathBuf` for paths — defer
   if fiddly), then `rewrite_commit(commit).set_tree(new).write()` + `rebase_descendants()`.
4. **Root guard:** rewriting the root commit **panics** — guard the rewritten `commit`/target in all
   three and raise `ImmutableCommitError` (like `abandon`, `src/transaction.rs:237`). Also guard
   `squash` source==into (`PyjutsuError`).
5. **Python facades** in `transaction.py` (`rebase`/`squash`/`restore`, `_complete_newline` on
   `squash`'s message) + **stubs** in `_pyjutsu.pyi` (`PyTransaction`).

- **Verify, don't assume (slice-5/-7 lesson):** grep the pinned source for every API the guide names.
  The four traps this slice (all grepped while writing the guide): (a) `squash_commits` returns a
  builder **holding `&mut repo`** — `write()` it before any further `repo.*`; (b) `restore_tree` is
  **async** and its orientation is "matched paths from `source`" (so `source = from_`,
  `destination = commit`); (c) `MoveCommitsTarget::Roots` (not `Commits`) gives the `-s`
  "commit + descendants" semantics; (d) rewriting the **root** panics — guard it.
- **Commit-id parity, not op-id parity:** these return *commits* (deterministic under the pinned
  timestamp), so assert **commit/change ids + graph + trees** across two byte-identical copies
  (binding vs CLI), like `test_describe`/`test_new`. Unlike slice 7, the colocated git-HEAD re-import
  does **not** bite (these move forward, new commit ids) — reading the binding repo with `jj` is fine.
- **Conflicts stay first-class:** a squash/rebase/restore that *produces* a conflict is **allowed**
  (jj records it N-sided) — do **not** map "result conflicts" to `ConflictError`.

- **Differential tests** (`tests/test_rewrite.py`, or three files — match the repo's convention) per
  guide §4: rebase a subtree onto a new parent matches `jj rebase -s … -d …` (descendants carried);
  rebasing root raises; squash into parent matches `jj squash --from … --into … -m …` (source change
  id gone, tree carries source changes); squash-into-self / squash-root raise; squash without message
  keeps `into`'s description (verify `jj squash --use-destination-message` exists, else use `-m`);
  whole-commit restore matches `jj restore --from … --into …` (tree becomes `from_`'s); path-scoped
  restore if it lands; each method raises `RuntimeError` outside the `with` block. **Re-run the whole
  suite** — additive `PyTransaction` methods, so all prior tests (incl. `test_undo`, `test_snapshot`,
  `test_new`) must stay green; confirm.

Run `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` green, then **stop and report at the
slice boundary** before slice 9 (workspace management: `init` / `add_workspace` / `forget_workspace`
+ a new `WorkspaceInfo` model — **verify workspace.rs:205/221/253/358 + repo.rs view APIs at that
slice**). Commit on `main` (`Implement M2 slice 8: rebase / squash / restore`).

## Non-negotiable constraints (carried)

- jj-lib via PyO3, in-process; pin `=0.38.0`; `Cargo.lock` committed; the pinned `jj` 0.38.0 CLI is
  **only** the differential oracle. Everything through devenv — never bare `cargo`/`maturin`/
  `python`/`pytest`/`jj`.
- Thin Rust, rich Python: `_pyjutsu` returns opaque handles + **plain data only** (dicts/lists/
  strings/bools/`None`); never leak jj-lib types; models/ergonomics/policy live in pure-Python `pyjutsu`.
- **GIL discipline:** `MutableRepo`/`Transaction` are `!Send`, so the rewrite primitives run **on the
  GIL** (like `describe`/`new`); the only off-GIL work (the post-commit checkout) is already
  centralized in `PyTransaction::commit`. Don't add checkout code in these methods.
- **`rebase_descendants()` after every rewrite** (landmine #1: a violation aborts the process); the
  method runs it for a faithful read-back and `commit` re-runs it idempotently.
- **Faithful primitive, simplest form:** `rebase`=`move_commits(Roots)`, `squash`=`squash_commits`,
  `restore`=`restore_tree`+`set_tree`. Root is the only immutable guard (`ImmutableCommitError`);
  `immutable_heads()` policy is not replicated. Documented refinements (`rebase -r`/`-b`, squash
  description-combining + interactive selection, deferred path-scoped restore, non-`Keep`
  `EmptyBehavior`) are **flagged, not faked**. Every fallible path maps a jj-lib error to a
  `PyjutsuError` subclass; only its `Display` crosses FFI. **No AI attribution** anywhere.

**Start by reading `M2_SLICE8_GUIDE.md` and skimming `describe`/`edit`/`abandon`/`resolve_single`/
`commit` in `src/transaction.rs` (the rewrite→rebase_descendants→read-back pattern, the root guard,
the centralized checkout you inherit), then implement `rebase`, `squash`, and `restore`, their facades
+ stubs, and the differential tests, with the whole suite green, and stop for review before slice 9.**
