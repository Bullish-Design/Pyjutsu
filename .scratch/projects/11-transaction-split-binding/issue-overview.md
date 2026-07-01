# 11 — `tx.split`: a native transaction binding for hunk-level / partial-file commit splitting

> **Found:** 2026-06-29, while building **`gitman split`** (gitman round-08, PR #26 — carve one
> lane's change into two sibling lanes). gitman composed the feature entirely from the existing
> pyjutsu surface (`tx.new` + `tx.restore` + bookmarks), which works beautifully **but only at
> whole-file granularity**: every changed file goes wholly to one side or the other. The moment two
> concerns entangle **inside the same file** (same file, different hunks), there is no pyjutsu
> primitive to divide them, so gitman explicitly deferred its "S3 hunk-level / interactive split"
> tier as *blocked on pyjutsu*. This is that pyjutsu-side item: the missing **partial-tree**
> transaction binding.

## TL;DR — what pyjutsu should add

| # | Item | Kind | Severity |
|---|------|------|----------|
| S1 | A **non-interactive, hunk-level** `PyTransaction.split(commit, selection)` binding that divides one commit's change by a *partial* (sub-file) selection — the building block jj-lib already supports via `CommitWithSelection`, but pyjutsu never exposes | Feature / power-surface | **medium** (only missing piece blocking sub-file split in any consumer) |
| S2 | A lower-level **`select_tree(commit, selection) -> tree_id`** (or `CommitWithSelection` constructor) so consumers can build a partial tree and reuse it across `split`/`squash`/`restore` | API shape decision | low–medium |
| S3 | Decide the **selection vocabulary** — reuse the structured `Hunk` shape pyjutsu's `diff()` already emits (`old_start`/`new_start`/`lines`) as the round-trip selection input, vs a fileset/patch-text format | Design decision | medium (locks the consumer contract) |

**Explicitly *not* asked for:** an *interactive* terminal diff-editor (jj CLI's `jj split -i` runs
`$EDITOR` / a builtin TUI). pyjutsu is a library with no terminal; **"interactive" is the consumer's
job** (gitman builds the picker UI). pyjutsu's contract is a **programmatic, non-interactive**
selection API that a consumer drives. See §"Scope boundary".

---

## Status — SHIPPED (2026-06-30, pyjutsu 0.9.0, branch `feat/11-tx-split`)

All three items landed in `src/transaction.rs` (`select_tree` + `split` + the S3-A vocabulary),
`python/pyjutsu/transaction.py`, `_pyjutsu.pyi`, `docs/PYJUTSU_CONCEPT.md`, and `tests/test_split.py`
(17 tests; full suite 224 green). gitman's `08-split-lane-capability` S3 tier is **unblocked**.

| # | What shipped |
|---|--------------|
| **S1** `tx.split(commit, selection, mode=…)` | Two-commit split. **`mode="siblings"` (default):** `first` = a **new** sibling (fresh change id, no descendants) holding the selected change; `second` = the original commit **rewritten in place** to the remainder — keeps its change id, bookmarks, descendants, and `@`. Both children of the original parent(s). This is gitman's carve-into-two-lanes topology (`first` = carved, `second` = kept, mirroring `do_split`'s `A`/`C`). **`mode="stacked"` (jj's own `jj split`):** `first` (selected) on the original parent(s), `second` reparented onto `first` with its tree unchanged. Root split → `ImmutableCommitError`; unknown mode → `PyjutsuError`. |
| **S2** `tx.select_tree(commit, selection) -> tree_id` | The primitive `split` composes on: builds the partial `selected_tree` (parent + selected hunks) via `MergedTreeBuilder` and returns its resolved tree-id hex. Permissive (no empty/full guard) — `split` layers those on. |
| **S3** selection vocabulary | **(A) as recommended.** `selection: dict[str, list[int] \| None]` — `None` = whole file, a list = 0-based hunk indices into that file's `diff(commit)` output. No patch-header grammar. Pinned contract: indices come from a `diff()` of the *same* commit. |

### What was confirmed / corrected against jj-lib 0.42 while building

- **CONFIRMED (the doc flagged this as *inferred*):** an arbitrary **partial `selected_tree` can be
  assembled non-interactively** — no diff editor needed. The build reconstructs each selected file's
  bytes from `jj_lib::diff::ContentDiff::by_line([parent_bytes, commit_bytes])` (the *same*
  decomposition `RepoView.diff()` uses, so hunk indices line up exactly), taking the *after* side for
  selected hunks and the *before* side for the rest, then writes the blob via `Store::write_file` and
  sets it in a `MergedTreeBuilder` over the parent tree. `CommitWithSelection::is_empty_selection` /
  `is_full_selection` (comparing `tree_ids()`) supply the empty/full guards for free.
- **DECISION — did *not* route the two-commit write through `squash_commits`.** `squash_commits` moves
  selections *between* existing commits; a split is cleaner as `new_commit(selected)` +
  `rewrite_commit(original).set_tree(remainder)` (siblings) or `+ set_parents([first])` (stacked),
  which gives exact control over the sibling topology gitman wants and preserves the original change
  id on `second`. `CommitWithSelection` is still constructed — but only to call the two
  `is_*_selection` validators. The remainder tree is assembled symmetrically (base = commit tree,
  override each listed path with parent + *unselected* hunks), so `first ⊕ second` reassembles the
  original commit (verified by disjoint-hunk tests).
- **API notes:** `Merge::normal(value)` (not `resolved(Some(..))`) builds a resolved present
  `MergedTreeValue`; `Store::write_file(path, &mut &content[..])` returns a `FileId`;
  `DiffHunkContentVec = SmallVec<[&BStr; 2]>` (deref-coerces to `&[u8]`); `MergedTree::path_value` is
  async and yields the `MergedTreeValue` at a path.
- **Edge cases (decided + tested):** hunk-level selection requires a **resolved text file present in
  the commit** — binary, symlink, conflicted, and removed files raise a typed error and must be
  selected whole-file (`None`); an out-of-range hunk index is a typed error (not a silent no-op); an
  unchanged path in the selection is rejected. A **renamed/copied** path (its `diff()` `source` is
  set) must be selected whole-file — its hunk indices are computed against the *source* path, so they
  don't align with `split`'s same-path reconstruction (documented in the `split` docstring, not
  auto-detected inside `split`).

---

## Background — why this surfaced, and exactly where the wall is

`gitman split --paths <sel> --into <lane>` (gitman `src/gitman/core.py:do_split`) partitions a lane's
single change into two sibling commits on trunk. Its engine is **`PyTransaction.restore`** only:

```
tx.new([trunk])                                  # empty child of trunk = carved lane A
tx.create_bookmark(into, "@")
tx.restore(into, from_=C)                         # A := C's full content
tx.restore(into, from_=trunk, paths=remainder)    # A := carved-only (revert the remainder files)
tx.restore(C,    from_=trunk, paths=carved)        # C := remainder-only (revert the carved files)
tx.edit(C)
```

Every carve is a `restore(..., paths=…)`, and `paths` is jj's **`FilesMatcher`** — it matches
**exact whole files** (`src/transaction.rs:449`, `FilesMatcher::new(&repo_paths)`). gitman even
expands user globs/prefixes against the changed-file set itself and passes exact file paths, because
the matcher does nothing else. **There is no way to express "only lines 10–15 of `src/app.py`."** So:

- **Two concerns in different files** → `gitman split` handles it today. ✅
- **Two concerns in the same file's diff** (a config edit and a drive-by bugfix tangled in one file)
  → **no front door.** The user must hand-unedit one set, split, and re-edit — exactly the
  off-canonical temptation gitman exists to remove. ❌

This is not a gitman limitation that gitman can fix; it is a **missing pyjutsu primitive**. jj-lib
*can* do sub-file splitting — pyjutsu just never binds it.

---

## S1 — `PyTransaction.split(commit, selection)`: the missing partial-tree op

**Severity:** medium — it is the single primitive that unblocks sub-file split in *any* pyjutsu
consumer, and it is the only split variant that cannot be composed from today's surface.

### What we want (consumer-facing shape)

A transaction method that takes one commit and a **partial selection of its diff** and rewrites the
history into **two commits**: the selected change and the remainder. Two orientations are both
reasonable (pyjutsu should pick one as the binding's contract and let the consumer rebase/bookmark to
taste):

```python
# A) sibling split (what gitman wants): both children of the same parent.
first, second = tx.split(commit, selection, mode="siblings")
#   first  = parent + selected hunks
#   second = parent + remainder hunks
#   (consumer bookmarks each, rebases as needed)

# B) stacked split (jj's own `jj split` default): linear parent ← first ← second.
first, second = tx.split(commit, selection)   # first = selected; second = first + remainder
```

`selection` is a **partial** description of `commit`'s diff vs its parent — at minimum per-file +
per-hunk (see S3). A **whole-file** selection must remain expressible (so `split` subsumes today's
path-scoped carve), and an **empty** or **full** selection must error clearly (the carve would be a
no-op / a rename).

### Why it can't be composed today (root cause)

pyjutsu's mutation surface only moves **whole trees or whole files**:

- `restore(commit, from_, paths)` — `EverythingMatcher` or `FilesMatcher` only (`transaction.rs:431-457`).
- `squash(source, into)` — constructs a `CommitWithSelection` whose `selected_tree` is the **entire**
  source tree (`transaction.rs:376-380`), with the in-code comment: *"Whole-commit selection: the
  entire source tree is moved (partial/interactive selection is the out-of-scope refinement)."*

That comment is the crux: jj-lib's selection abstraction is **already** `CommitWithSelection`, and
pyjutsu already uses it — but only ever with a full-tree `selected_tree`. The unbuilt piece is
**constructing a *partial* `selected_tree`** from a hunk-level selection.

### The jj-lib hooks that already exist (so this is a binding, not new jj-lib work)

- **`jj_lib::rewrite::CommitWithSelection { commit, selected_tree, parent_tree }`**
  (`jj-lib-0.42.0/src/rewrite.rs:1254`). Carries exactly "this commit, the chosen sub-tree, and the
  base." Helpers `is_full_selection()` / `is_empty_selection()` (`:1262`,`:1271`) give the
  whole-change / empty-change guards **for free** at the jj-lib level.
- **`jj_lib::merged_tree_builder::MergedTreeBuilder`** (`rewrite.rs:49`, already used inside
  `restore_tree` at `rewrite.rs:160`) — the primitive that builds a tree from a chosen set of
  path/content entries. This is what assembles a partial `selected_tree`.
- **`squash_commits(repo, &[CommitWithSelection], dst, keep_emptied)`** (`rewrite.rs:~1305`) already
  consumes selections and even handles the empty-selection branch (`rewrite.rs:1329`). jj's own
  `split` is two tree-rewrites over the same `CommitWithSelection` machinery.

So the work is: **accept a structured selection across the FFI, build the `selected_tree` /
`parent_tree` with `MergedTreeBuilder`, wrap it in `CommitWithSelection`, and write the two commits**
— all on the GIL like `squash`/`restore` already do (`MutableRepo` is `!Send`, `transaction.rs:3-13`).

### Test plan

- A repo with **one file** carrying two disjoint hunks (lines near the top vs near the bottom).
  `tx.split(commit, <top hunk only>)` → assert: `first` contains only the top change, `second` only
  the bottom; reassembling them equals the original tree; change-ids/commit parity vs the equivalent
  `jj split` over the same selection (mirror the `test_restore_*_matches_cli` parity style in
  `tests/test_rewrite.py`).
- Whole-file selection → identical result to the path-scoped `restore` carve (subsumes today's path).
- Empty selection and full selection → clear errors (lean on `is_empty_selection()`/`is_full_selection()`).
- Binary / conflicted / renamed file in the selection → defined behavior (reject with a typed error,
  or fall back to whole-file — decide and test).

---

## S2 — expose the selection as a reusable building block (`select_tree`)

**Severity:** low–medium.

Rather than (or in addition to) a monolithic `split`, expose the **partial-tree construction** itself
so consumers can compose:

```python
tree_id = tx.select_tree(commit, selection)   # build a MergedTree from a hunk selection → its id
# then reuse via the existing surface:
#   tx.new([...]) / set the tree, or feed a CommitWithSelection into a squash/restore variant
```

This keeps the FFI honest (one well-tested "selection → tree" primitive) and lets gitman build
whatever lane topology it wants (siblings, stacks, move-into-existing-lane) on top, the same way it
composes `new`/`restore`/`rebase`/bookmarks today. **Recommendation:** ship S1 (`split`) as the
ergonomic verb *and* expose S2 (`select_tree`) as the primitive — `split` implemented in terms of it.

---

## S3 — the selection vocabulary (the consumer contract)

**Severity:** medium — this is the API surface gitman (and any consumer) codes against; changing it
later is a breaking change.

pyjutsu **already emits** a structured, lossless hunk shape from `RepoView.diff()`
(`src/repo_view.rs:294-346`, `python/pyjutsu/models.py:Hunk`/`HunkLine`):

```
Hunk { old_start, old_lines, new_start, new_lines, lines: [HunkLine{kind: added|removed, content}] }
```

The natural, round-trippable design is: **diff out, select, hand the selected hunks back in.** A
selection is then just `{path: [hunk indices | hunk ranges]}` referencing the very hunks `diff()`
produced — no new patch grammar, no fragile `@@` header parsing on either side. Options:

- **(A) Reuse the emitted `Hunk` identity (recommended).** Selection = a list of `(path, hunk
  selector)` where the selector identifies hunks from `diff(commit)`'s output (by index, or by
  `(old_start,new_start)` key). Symmetric with the read surface; trivial for a consumer that already
  rendered the diff to a picker. Pin the diff↔selection contract (same op, same commit) so indices
  are stable.
- **(B) Accept unified-diff / patch text.** Maximally general and tool-interop-friendly, but pyjutsu's
  `diff()` deliberately does **not** emit byte-exact `@@` unified headers (`models.py:Hunk` docstring),
  so this would mean *adding* a patch emitter+parser pair purely for split. Heavier, more failure modes.
- **(C) Fileset/path only.** Rejected for S3 — that's exactly today's whole-file capability; it adds
  nothing over the `restore`-based carve gitman already ships.

**Recommendation:** **(A)**. It composes with the existing read surface and matches how a consumer
actually drives this (render `diff` → user/agent picks hunks → feed the picks back).

---

## Scope boundary — why "interactive" stays out of pyjutsu

jj's CLI `jj split -i` runs an **interactive diff editor** (`$EDITOR`, `meld`, or jj's builtin TUI).
That is a *terminal/UX* concern and lives in the **CLI crate**, not `jj-lib`. pyjutsu is an
in-process library with no terminal and no business spawning an editor. So:

- **pyjutsu** owns the **non-interactive, programmatic** selection→commits primitive (S1/S2) and the
  selection vocabulary (S3).
- **The consumer** (gitman) owns the **interaction**: `gitman split --interactive` would render
  `diff()`'s hunks, let the agent/user choose, and call `tx.split(commit, selection)`. gitman already
  plans this as its S3 tier; it is unblocked the moment pyjutsu ships S1.

This split of responsibilities mirrors how pyjutsu already handles everything else: jj-lib semantics
in pyjutsu, policy/UX in the consumer.

---

## Suggested build order (for the pyjutsu round that takes this)

1. **`select_tree` (S2)** — accept the structured selection (S3-A), build `selected_tree`/`parent_tree`
   via `MergedTreeBuilder`, validate against `is_full_selection`/`is_empty_selection`. Unit-test
   tree identity vs `jj split` selections.
2. **`split` (S1)** — compose `select_tree` + two commit writes (pick the sibling-vs-stacked contract;
   sibling is what the first consumer wants). Parity tests vs the CLI.
3. **Docs/stub** — `_pyjutsu.pyi` + `transaction.py` docstrings; a `PYJUTSU_CONCEPT.md` note that the
   power surface now covers sub-file rewrites; bump the version + `JJ_LIB_TARGET` discipline.
4. **Notify the consumer** — gitman's `08-split-lane-capability` backlog references this; once shipped,
   gitman adds `gitman split --interactive`/`--hunks` on top (no further pyjutsu work).

---

## Confidence & caveats

- **Certain (observed building gitman round-08):** `restore`'s matcher is whole-file only
  (`FilesMatcher`); a bare dir / prefix / glob matches nothing (probed). `squash` constructs a
  full-tree `CommitWithSelection` and its own comment flags partial selection as out of scope. `diff()`
  already emits structured hunks. So the *capability gap* (no sub-file mutation) is real and exactly
  located.
- **Inferred (confirm against jj-lib before building):** that `CommitWithSelection` +
  `MergedTreeBuilder` are sufficient to assemble an arbitrary partial `selected_tree` non-interactively
  (the structs and the `squash_commits` consumer exist; the CLI builds the tree via its diff editor —
  verify the same tree can be built from a programmatic hunk list without the editor). Also confirm the
  cleanest commit-writing path for the *two-commit* result (jj's CLI `split` rewrites in place + adds a
  child; decide sibling vs stacked for the binding).
  > **CONFIRMED + DECIDED (2026-06-30, shipped):** yes — `MergedTreeBuilder` over the parent tree,
  > with each selected file's bytes reconstructed from `ContentDiff::by_line` (same decomposition
  > `diff()` uses), assembles the partial tree with no diff editor. The two-commit write is
  > `new_commit(selected)` + `rewrite_commit(original)` (**not** `squash_commits`); **both** sibling
  > (default) and stacked modes are exposed. See the Status section above.
- **Decision, not bug:** none of this is a defect in pyjutsu or jj-lib 0.42 — it is an unbuilt power-
  surface refinement that pyjutsu intentionally deferred (the `squash` comment is the paper trail).

## References

- **pyjutsu:** `src/transaction.rs:411` (`restore`, `FilesMatcher` whole-file), `:352` (`squash`,
  `:376-380` full-tree `CommitWithSelection` + the "partial/interactive… out-of-scope" comment),
  `:24-43` (imports incl. `CommitWithSelection`, `squash_commits`, `restore_tree`, `MergedTreeBuilder`
  is reachable via `rewrite`), `:3-13` (`!Send`/GIL constraint); `src/repo_view.rs:294-346` (`diff`
  hunk surface); `python/pyjutsu/models.py` (`Hunk`/`HunkLine`); `python/pyjutsu/_pyjutsu.pyi:105-125`
  (transaction stub — where `split`/`select_tree` would be added).
- **jj-lib 0.42:** `src/rewrite.rs:1254` (`CommitWithSelection`), `:1262`/`:1271`
  (`is_full_selection`/`is_empty_selection`), `:49`/`:160` (`MergedTreeBuilder`), `~:1305`
  (`squash_commits` consuming selections, `:1329` empty-selection branch).
- **gitman consumer:** `.scratch/projects/08-split-lane-capability/` (PLAN.md §"Out of scope" defers
  S3 hunk-level split as needing this binding; `src/gitman/core.py:do_split` + `_match_paths` are the
  whole-file engine that would gain an `--interactive`/`--hunks` path on top of `tx.split`).
