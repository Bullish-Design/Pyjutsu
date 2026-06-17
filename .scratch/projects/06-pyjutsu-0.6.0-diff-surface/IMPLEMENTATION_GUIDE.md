# Pyjutsu 0.6.0 — Diff read surface Implementation Guide (3 slices)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec; §5 surface lists `diff` *(later)*, §12
> scope lists "full diffs/hunks" under **Later** — this milestone *is* that item) →
> `.scratch/projects/05-pyjutsu-0.5.0-fidelity-completion/REFINEMENTS_GUIDE.md` (the parent milestone
> whose patterns/discipline this mirrors) → **this document** (the verified plan for 0.6.0) → the code
> it produces.
>
> **Pins unchanged:** `jj-lib = "=0.38.0"` (default features include `git`); `gix = "=0.78.0"`.
> `JJ_LIB_TARGET` stays `"0.38.0"`. Pyjutsu uses **independent semver**: this milestone bumps
> `0.5.0 → 0.6.0` (see §6). **Start from a clean `main` with 0.5.0 already landed @ `v0.5.0`** (the
> diff surface builds on no 0.5.0 code, but sequencing keeps the tree linear). Every jj-lib API ref
> below is `file:line` into
> `~/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/jj-lib-0.38.0/src/`, **verified against the
> pinned source while writing this guide** (2026-06-16). Facts still needing runtime confirmation
> against the CLI are marked **VERIFY**.

---

## 0. What this milestone is, and why now

Pyjutsu can read commits, trees, files, conflicts, and a **diff *stat*** (per-file +/- line counts,
`src/diff_stat.rs` + `RepoView.diff_stat`), but it cannot read the diff itself: *which* paths
changed, *how* (add/modify/delete/rename), and the *content hunks*. That is the canonical "later"
item in the concept doc (§5 `diff` *(later)*, §12 "full diffs/hunks"). 0.6.0 delivers it as a tight,
**read-only** surface that extends the existing `diff_stat` machinery.

| Slice | Item | Builds on | jj-lib API status |
|---|---|---|---|
| **1** | `diff(revset)` → **name-status** (changed paths + change kind) | `MergedTree::diff_stream` (already used in `diff_stat::compute`) | clean — `diff_stream` (merged_tree.rs:276) + `Diff<MergedTreeValue>` (merge.rs:53) + `TreeValue` (backend.rs:292) |
| **2** | per-file **content hunks** (unified-diff lines) | `ContentDiff::by_line` (already used in `diff_stat::count_line_changes`) | clean — `ContentDiff` (diff.rs:600) + `by_line` (diff.rs:752) + `hunks` (diff.rs:771) + `DiffHunk`/`DiffHunkKind` (diff.rs:868/894) |
| **3** | **copy/rename** detection (the VERIFY / flag-it slice) | slice 1's stream → copy-aware stream | mostly clean — `diff_stream_with_copies` (merged_tree.rs:306) + `Store::get_copy_records` (store.rs:105) + `CopiesTreeDiffEntry`/`CopyOperation` (copies.rs:105/96); **VERIFY the git backend actually emits records** |

**Why read-only is the headline property.** None of these slices rewrites a commit or touches the
snapshot → tree → commit-id path, so **no slice can move a commit id**. There is no fencing risk like
0.4.0 slice 4 / 0.5.0 slices 2–3 had. The differential net for each slice is the new diff oracle
plus the *unchanged* existing suite. Slices are still done **in order** and each is independently
committable and must leave the suite green before the next.

**Sequencing.** Slice 1 establishes the `diff()` binding + the `FileChange`/`FileDiff` Python models
+ the `JjCli` diff oracle helpers; slice 2 enriches each changed *text* file with hunks (the meatiest
slice); slice 3 layers copy/rename onto slice 1's path pairs **or is cleanly flagged** if the git
backend returns no copy records by default (the explicit valve — see §4.2).

**Explicitly still out of scope (do NOT implement; keep flagged):** diff *between two arbitrary
revsets* (`diff(from, to)` — this milestone diffs a single commit against its parent(s), exactly like
`diff_stat`); word-level/inline diff; `--git`-format *rendering* in Rust (the binding returns
structured hunks; rendering is the caller's job); whitespace/context-line-count options; streaming
iterator diffs for huge trees; async. These remain *flagged, not faked*. Also untouched: every 0.5.0
backlog flag (force-push, `--change` push, tag fetch, `--all-remotes`, interactive squash,
sparse/`-r` workspace, revset/fileset builders).

---

## 1. Carried structural facts (true for every slice; re-verify, don't assume)

- **Thin Rust, rich Python.** `_pyjutsu` returns opaque dicts / plain scalars / `None` only. **No
  jj-lib type crosses the FFI.** Models (`FileChange`, `FileDiff`, `Hunk`, `HunkLine`) and ergonomics
  live in pure-Python `pyjutsu`, exactly like `DiffStat`/`FileStat` (models.py:67/77).
- **The diff is single-commit-vs-parent, like `diff_stat`.** `diff_stat::compute` (diff_stat.rs:41)
  resolves `revset` to **exactly one** commit, builds `from_tree = merge_commit_trees(parents)`
  (empty tree for a root commit), and diffs against `commit.tree()`. The diff surface reuses this
  framing verbatim — *do not* generalize to an arbitrary `(from, to)` pair (flagged above).
- **Off the GIL.** The whole tree-diff + content-read walk is `Send` async over the store; wrap it in
  `py.allow_threads(|| pollster::block_on(async { … }))` exactly as `diff_stat::compute` does
  (diff_stat.rs:42). The `RepoView` read binding already holds `Arc<dyn Repo>` — no `!Send`
  transaction is involved (this is a pure read; no op is published).
- **Reuse `diff_stat`'s text/binary discipline.** `read_text` (diff_stat.rs:86) already encodes the
  rule the whole milestone needs: `Some(None)` ⇒ absent side (empty); resolved `File{id}` ⇒ read
  bytes, `None` if it contains a NUL (binary); anything else (symlink/submodule/tree/conflict) ⇒
  `None` (not line-diffable). Slice 2's hunks reuse this; slice 1's *kind* derivation inspects the
  same `Diff<MergedTreeValue>` before/after. **Factor the shared "classify a `Diff<MergedTreeValue>`
  side" logic so `diff_stat`, name-status, and hunks agree** (a file that `diff_stat` lists with
  zero counts must also appear in `diff()` with the right kind and an empty/binary hunk marker).
- **Differential oracle = the pinned `jj` 0.38.0 CLI**, via `tests/diff/jj_cli.py::JjCli` against the
  fixture repos in `tests/conftest.py` (`diffstat_repo`, `linear_repo`, or a new `rename_repo`). Add
  oracle helpers to `JjCli` (`diff_summary`, `diff_git`) mirroring `diff_stat_totals` (jj_cli.py:118).
  Assert **structured equality** (path→kind maps; per-file added/removed line multisets), not exact
  byte-rendering — the binding returns structure, the CLI renders text, so normalize both to the same
  shape. **Everything runs through devenv** — never bare `cargo`/`maturin`/`pytest`/`jj`.
- **Build/verify per slice:** `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` (task names
  confirmed in `nix/pyjutsu.nix`: `build` = `maturin develop --uv`, `test` = `pytest -q && cargo
  test`, `lint` = `ruff check python tests && cargo clippy --all-targets -- -D warnings`). **No AI
  attribution** anywhere.

---

## 2. Slice 1 — `diff(revset)` name-status

### 2.1 What it is
A read returning the **changed paths and how each changed** for the single commit named by `revset`,
diffed against its merged parent(s) — the same framing as `diff_stat`. Each entry:

```python
class FileChange(BaseModel):           # models.py, next to FileStat/DiffStat
    path: str                          # target path (jj internal '/'-separated string)
    kind: Literal["added", "modified", "removed", "type_changed"]
    # executable-bit and file-type info are cheap to add now (see §2.4); keep minimal first.
```

`diff(revset) -> Diff` where `Diff.files: list[FileChange]` (and slice 2 adds `.hunks` per file). The
binding method on `ReadOnlyRepoView` mirrors `diff_stat` (repo_view.rs:218): resolve→single commit,
off-GIL stream, return an opaque dict.

### 2.2 jj-lib APIs (verified)
| What | Signature / fact | Ref |
|---|---|---|
| The diff stream (already used) | `MergedTree::diff_stream(&self, other, &dyn Matcher) -> TreeDiffStream` | merged_tree.rs:276 |
| Stream item | `TreeDiffEntry { path: RepoPathBuf, values: BackendResult<Diff<MergedTreeValue>> }` | merged_tree.rs:360 |
| The before/after pair | `Diff<T> { before: T, after: T }` (here `T = MergedTreeValue`) | merge.rs:53 |
| Resolve a side | `MergedTreeValue::as_resolved() -> Option<&Option<TreeValue>>` (used in diff_stat.rs:91) | merge.rs (Merge::as_resolved) |
| File/type variants | `enum TreeValue { File { id, executable, .. }, Symlink(_), Tree(_), GitSubmodule(_), .. }` | backend.rs:292 |
| Parent merge (already used) | `merge_commit_trees(repo, &parents) -> MergedTree` (empty over zero parents) | rewrite.rs (used diff_stat.rs:49) |

### 2.3 The change-kind derivation (define precisely)
For each `TreeDiffEntry`, take `diff = entry.values?` then classify `(diff.before, diff.after)` via
`as_resolved()`:
- `before` resolves to `Some(None)` **and** `after` to `Some(Some(_))` ⇒ **added**.
- `before` `Some(Some(_))` **and** `after` `Some(None)` ⇒ **removed**.
- both `Some(Some(a))` / `Some(Some(b))` ⇒ **modified**, unless the `TreeValue` *variant* differs
  (e.g. file↔symlink, file↔submodule) ⇒ **type_changed**. (Executable-bit-only changes are still
  `modified`; jj's `--summary` shows `M` — **VERIFY** the CLI's letter for a pure mode change.)
- either side unresolved (`as_resolved()` is `None`, i.e. a conflict in that tree) ⇒ classify as
  **modified** for name-status (a conflicted path *is* a change vs parent); record nothing finer here
  (conflict detail is the existing `conflicts()` read). **VERIFY** against `jj diff --summary` on a
  conflicted commit; if the CLI omits it, omit it.

> The stream never yields unchanged paths (it is a *diff* stream), and `diff_stream` (not
> `diff_stream_with_trees`, merged_tree.rs:285) already excludes pure-tree entries — so every entry is
> a real file-level change. Keep `EverythingMatcher` (matchers — already imported in diff_stat.rs:16).

### 2.4 Rust sketch (extend `src/diff_stat.rs` or add `src/diff.rs`)
Prefer a **new `src/diff.rs`** module (sibling to `diff_stat.rs`) that *shares* the text-classifier
helper with `diff_stat` (move `read_text`/`count_line_changes` into it, or a small common `fn` both
call). Register it in `lib.rs` (next to `mod diff_stat;`).

```rust
pub(crate) struct FileChangeData { pub path: String, pub kind: &'static str }   // "added" | …
pub(crate) struct DiffData { pub files: Vec<FileChangeData> }                    // slice 2 adds hunks

pub(crate) fn compute(repo: &dyn Repo, commit: &Commit) -> Result<DiffData, PyErr> {
    pollster::block_on(async {
        let parents: Vec<Commit> = commit.parents().collect::<Result<_, _>>().map_err(map_backend_err)?;
        let from_tree = merge_commit_trees(repo, &parents).await.map_err(map_backend_err)?;
        let to_tree = commit.tree();
        let mut stream = from_tree.diff_stream(&to_tree, &EverythingMatcher);
        let mut files = Vec::new();
        while let Some(entry) = stream.next().await {
            let diff = entry.values.map_err(map_backend_err)?;
            let kind = classify_change(&diff.before, &diff.after);   // §2.3 → &'static str
            files.push(FileChangeData { path: entry.path.as_internal_file_string().to_owned(), kind });
        }
        Ok(DiffData { files })
    })
}
```

`classify_change` matches on `before.as_resolved()`/`after.as_resolved()` per §2.3. The path string
matches `diff_stat`'s `as_internal_file_string()` (diff_stat.rs:70) so the two reads agree on naming.

### 2.5 Binding + Python facade + model
- **Rust binding** (`src/repo_view.rs`, mirror `diff_stat` at repo_view.rs:218): `fn diff<'py>(&self,
  py, revset_str) -> PyResult<Bound<'py, PyDict>>`. Resolve→single-commit (reuse the exact
  `revset::evaluate` + `len()!=1 ⇒ RevsetError` block from diff_stat.rs:228), off-GIL `diff::compute`,
  build `{"files": [ {"path","kind"}, … ]}`.
- **`.pyi`** (next to `diff_stat`, _pyjutsu.pyi:51): `def diff(self, revset_str: str) -> dict[str, object]: ...`
- **Facade** (`python/pyjutsu/repo_view.py`, next to `diff_stat` at repo_view.py:62):
  `def diff(self, revset: str) -> Diff: return Diff.model_validate(self._handle.diff(revset))`
- **Models** (`python/pyjutsu/models.py`, next to `FileStat`/`DiffStat`): `FileChange` + `Diff` (with
  `model_config = ConfigDict(frozen=True, extra="forbid")` like its neighbors). Export both from
  `python/pyjutsu/__init__.py` (`from .models import …` + `__all__`, mirroring `DiffStat`/`FileStat`
  at __init__.py:28/70).

### 2.6 Differential tests (`tests/test_diff.py`, new)
Add a `JjCli.diff_summary(repo, revset) -> dict[str, str]` helper parsing `jj diff -r <revset>
--summary` (lines `"<letter> <path>"`, letters `A`/`M`/`D`/`R`/`C`; map to the kind strings). Tests:
- **`test_diff_name_status_matches_cli`** *(headline)*: on `diffstat_repo` (conftest.py:80 — `@-`
  modifies `a.txt`, adds `b.txt`), assert `{f.path: f.kind for f in ws.diff("@-").files}` equals the
  CLI summary map (`{"a.txt": "modified", "b.txt": "added"}`).
- **`test_diff_added_and_removed`**: a fixture that adds then deletes a file across two commits; assert
  `added` then `removed` kinds vs the CLI.
- **`test_diff_empty_and_root`**: `diff("@")` (empty WC) and `diff("root()")` ⇒ `files == []` (mirror
  `test_empty_and_root_commits_have_empty_stat`, test_diff_stat.py:35).
- **`test_diff_requires_single_revision`**: `diff("all()")` ⇒ `RevsetError` (mirror test_diff_stat.py:58).

---

## 3. Slice 2 — per-file content hunks

### 3.1 What it is
Enrich each **modified/added/removed text** file from slice 1 with its **content hunks** — the
line-level changes, grouped into unified-diff hunks with old/new line ranges. The Python shape:

```python
class HunkLine(BaseModel):
    kind: Literal["context", "added", "removed"]
    content: str                       # one line incl. its trailing '\n' if present; lossy-utf8 decoded
class Hunk(BaseModel):
    old_start: int; old_lines: int     # 1-based unified-diff ranges (@@ -old_start,old_lines …)
    new_start: int; new_lines: int
    lines: list[HunkLine]
# FileChange gains:  hunks: list[Hunk] = []   and   binary: bool = False
```

Binary/symlink/submodule/conflict files (the `read_text → None` cases, diff_stat.rs:97) carry
`binary=True` (or a `text=False` flag) and **no** hunks — exactly how `diff_stat` lists them with
zero counts.

### 3.2 jj-lib APIs (verified — all already in use by `diff_stat`)
| What | Signature | Ref |
|---|---|---|
| Line-level content diff | `ContentDiff::by_line(inputs: [&[u8]; 2]) -> ContentDiff` | diff.rs:752 |
| Iterate hunks | `ContentDiff::hunks(&self) -> DiffHunkIterator` | diff.rs:771 |
| A hunk | `DiffHunk { kind: DiffHunkKind, contents: DiffHunkContentVec }` (`contents[0]`=before side, `[1]`=after) | diff.rs:868 |
| Hunk kind | `enum DiffHunkKind { Matched, Different }` | diff.rs:894 |
| Read a side's bytes (reuse) | `read_text(store, path, &MergedTreeValue) -> Option<Vec<u8>>` | diff_stat.rs:86 |

`diff_stat::count_line_changes` (diff_stat.rs:106) already walks exactly this: `by_line` →
`hunks()` → `DiffHunkKind::Different` ⇒ side `[0]` removed, `[1]` added. Slice 2 keeps the *positions*
instead of just counting: walk hunks in order, tracking `old`/`new` line cursors, emit `context`
lines for `Matched` hunks (or coalesce/window them — see §3.3) and `removed`/`added` for `Different`.

### 3.3 The hunk grouping (match `jj diff --git`, then VERIFY)
`ContentDiff::by_line` yields an alternating sequence of `Matched`/`Different` spans over the whole
file. To produce unified-diff hunks you must group `Different` spans with a small window of
surrounding `Matched` context (default git context = 3 lines) and split where the gap between changes
exceeds `2*context`. **Two valid strategies — pick the simpler that passes the oracle:**
1. **One hunk per `Different` span, no context** (`old_start/new_start` = the line numbers, context
   `lines == 0`). Simplest; assert against the CLI by comparing the *added/removed line multisets per
   file* rather than hunk boundaries. **Recommended first** — it sidesteps context-windowing entirely
   and is still a faithful structured diff.
2. **Full git-style 3-line-context hunks.** Match `jj diff --git` hunk headers exactly. More code;
   only do this if a test genuinely needs hunk-boundary parity.

> **VERIFY:** decide the assertion granularity *first*, then implement the minimum that satisfies it.
> The headline test asserts **per-file (added-lines, removed-lines) as multisets** (decode each
> `Different` side to lines), which strategy 1 satisfies with no windowing. **If exact `@@` hunk
> headers are wanted, that is the line where you ship multiset-parity and re-flag header-exact
> grouping** — don't hand-roll a fragile windowing algorithm to chase byte-parity with the renderer.

### 3.4 Rust sketch (extend `src/diff.rs::compute`)
Inside the stream loop, after classifying kind, read both sides via the shared `read_text`:
```rust
let before = read_text(store, &entry.path, &diff.before).await?;   // Option<Vec<u8>>
let after  = read_text(store, &entry.path, &diff.after).await?;
let (binary, hunks) = match (before, after) {
    (Some(b), Some(a)) => (false, content_hunks(&b, &a)),          // §3.2 by_line walk
    _ => (true, Vec::new()),                                       // non-text: flag, no hunks
};
```
`content_hunks(before, after) -> Vec<HunkData>` reuses `ContentDiff::by_line` + the
`DiffHunkKind::Different` split from `count_line_changes`, but records line ranges/contents. Decode
bytes to `String` with `String::from_utf8_lossy` at the FFI boundary (the model is `str`).

### 3.5 Differential tests (extend `tests/test_diff.py`)
Add `JjCli.diff_git(repo, revset) -> dict[str, tuple[list[str], list[str]]]` parsing `jj diff -r
<revset> --git` into `{path: (added_lines, removed_lines)}` (collect `+`/`-` body lines, skip headers
and `+++/---`). Tests:
- **`test_diff_hunks_match_cli`** *(headline)*: on `diffstat_repo`, for each changed text file assert
  the binding's per-file added/removed line *multisets* (flatten `HunkLine`s) equal the CLI's. (`a.txt`
  removes `l2`, adds `CHANGED`+`l4`; `b.txt` adds `b1`,`b2`.)
- **`test_diff_binary_has_no_hunks`**: mirror `test_binary_file_listed_with_zero_counts`
  (test_diff_stat.py:43) — a NUL-containing file ⇒ `binary=True`, `hunks == []`.
- **`test_diff_hunk_line_kinds`**: a pure-addition file ⇒ all `HunkLine.kind == "added"`; a deletion ⇒
  all `removed`.
- **Re-run the full suite** (shared `read_text`/classifier with `diff_stat`).

---

## 4. Slice 3 — copy/rename detection (do LAST; the flag-it valve)

### 4.1 What it is
Git/jj can report a delete+add pair as a **rename** (`R old new`) or **copy** (`C old new`). jj's
diff machinery models this via `diff_stream_with_copies` over a `CopyRecords` set the *backend*
provides. If the git backend emits copy records for the commit, fold them into slice 1's name-status:
a `FileChange` gains `kind ∈ {"renamed","copied"}` and a `source: str | None` (the old path).

### 4.2 Why it is last / the explicit valve
This is the **only slice whose data source is backend-dependent**. jj computes copies from
`Store::get_copy_records(paths, root, head)` (store.rs:105 → `Backend::get_copy_records`,
backend.rs:516). **The git backend may return an empty stream unless rename detection is enabled** —
exactly the kind of fact the guide cannot assert without runtime check.

> **VERIFY (do this FIRST, before writing slice 3):** run `jj diff -r <rev> --summary` on a
> rename fixture (create `old.txt`, then in a child commit `jj` move its content to `new.txt` and
> delete `old.txt`) with the pinned CLI. **If the CLI shows `R old.txt new.txt`**, the backend emits
> records → implement below. **If it shows `D old.txt` + `A new.txt`** (no rename), then jj 0.38's
> git backend does *not* do rename detection by default → **ship slices 1–2 as the milestone and
> FLAG copies/renames entirely** (add one sentence to the `diff()` docstring + a memory note). That is
> the clean, honest boundary; name-status without rename detection is still correct and matches the
> CLI's own default output. **Do not fake renames** by hand-rolling similarity detection — jj doesn't,
> so the differential oracle would diverge.

### 4.3 jj-lib APIs (verified) — only if §4.2 confirms records exist
| What | Signature | Ref |
|---|---|---|
| Copy-aware stream | `MergedTree::diff_stream_with_copies(&self, other, &dyn Matcher, &CopyRecords) -> BoxStream<CopiesTreeDiffEntry>` | merged_tree.rs:306 |
| Copy records source | `Store::get_copy_records(Option<&[RepoPathBuf]>, root: &CommitId, head: &CommitId) -> BackendResult<BoxStream<BackendResult<CopyRecord>>>` | store.rs:105 |
| Accumulate records | `CopyRecords::add_records(impl IntoIterator<Item = BackendResult<CopyRecord>>)` | copies.rs:47 |
| Stream item | `CopiesTreeDiffEntry { path: CopiesTreeDiffEntryPath, values: BackendResult<Diff<MergedTreeValue>> }` | copies.rs:105 |
| Path + copy info | `CopiesTreeDiffEntryPath { source: Option<(RepoPathBuf, CopyOperation)>, target: RepoPathBuf }`; `copy_operation() -> Option<CopyOperation>` | copies.rs:114/134 |
| Copy vs rename | `enum CopyOperation { Copy, Rename }` | copies.rs:96 |

### 4.4 Rust sketch (extend `src/diff.rs::compute`, gated on §4.2)
Build `CopyRecords` from `store.get_copy_records(None, &from_id, &to_id)` (the merged-parent commit id
as `root`, `commit.id()` as `head`), then switch the stream to `diff_stream_with_copies`. Each entry's
`path.copy_operation()` ⇒ `"renamed"`/`"copied"` and `path.source` ⇒ the `source` string; otherwise
classify as in slice 1. **`get_copy_records` over a multi-parent (merge) commit is ill-defined** —
restrict copy detection to single-parent commits, falling back to plain name-status otherwise (VERIFY
what the CLI does for a merge commit).

### 4.5 Differential tests (`tests/test_diff.py`) — only if shipped
- **`test_diff_rename_matches_cli`** *(headline)*: the rename fixture from §4.2; assert the binding
  reports `kind="renamed"`, `path="new.txt"`, `source="old.txt"` **and** the CLI summary agrees. If
  §4.2 showed no rename, this test is *not added* and the flag note ships instead.

---

## 5. Build / verify / report (every slice)

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test     # pytest -q && cargo test
devenv shell -- devenv tasks run pyjutsu:lint     # ruff check + clippy -D warnings (NOT ruff format)
```
Per slice: build → full suite green → lint clean → commit on `main`. **No AI attribution** anywhere.
Commit messages, one per slice:
`Implement 0.6.0 slice 1: diff() name-status`,
`… slice 2: diff content hunks`,
`… slice 3: diff copy/rename detection` *(or, if flagged: `Implement 0.6.0: flag copy/rename (backend
emits no records)` folded into the version bump)*.

---

## 6. Version bump to 0.6.0 (after the last slice lands)
- `python/pyjutsu/__init__.py`: `__version__ = "0.6.0"` (leave `JJ_LIB_TARGET = "0.38.0"`).
- `Cargo.toml` + `pyproject.toml`: `version = "0.6.0"`.
- **Rebuild** so `Cargo.lock` / `uv.lock` refresh; **commit the lockfiles**.
- Tag `v0.6.0` (annotated, message `pyjutsu 0.6.0 — diff read surface (name-status + hunks[ +
  copies])`), matching the `v0.1.0…v0.5.0` convention; push `main` + tag.
- **Update memory:** new `[[pyjutsu-0-6-0-diff-surface]]` recording the models added, whether copies
  shipped or stayed flagged, and the hunk-grouping strategy chosen; update the concept §5/§12 "later"
  lines if you want them to reflect `diff` as landed (optional — concept edits are a separate call).

---

## 7. Guardrails (carried; non-negotiable)
- **Thin Rust, rich Python.** Only dicts / scalars / `None` cross FFI. No jj-lib type leaks.
- **Read-only.** No transaction, no op published, **no commit-id movement** — the easy property that
  makes this milestone low-risk. If any slice tempts you to write, you've left scope.
- **Differential, against the pinned `jj` 0.38.0 CLI only.** Assert **structured equality**
  (path→kind maps, per-file added/removed line multisets), normalizing the CLI's rendered text and the
  binding's structure to the same shape. Don't chase byte-for-byte renderer parity.
- **Reuse, don't duplicate.** Share `read_text` + the side-classifier between `diff_stat` and `diff`
  so the two reads never disagree about a file's text/binary status or path.
- **Pins frozen:** `jj-lib =0.38.0`, `gix =0.78.0`, `JJ_LIB_TARGET =0.38.0`. `Cargo.lock` committed.
- **Faithful primitive, simplest form.** Implement exactly these three (slice 3 may legitimately be a
  *flag*); keep every other diff nicety (two-revset diff, word/inline diff, `--git` rendering,
  context options, streaming, async) and every 0.5.0 backlog flag **flagged, not faked**.

> **Top traps (grepped against the pinned source while writing this guide; re-grep if doubted):**
> (1) **slice 1** — reuse `diff_stat::compute`'s framing (single commit vs `merge_commit_trees`
> parents; `diff_stream`, merged_tree.rs:276); classify via `Diff<MergedTreeValue>` `as_resolved()`
> (merge.rs:53 / backend.rs:292); the stream never yields unchanged or pure-tree paths. (2) **slice
> 2** — `ContentDiff::by_line` (diff.rs:752) + `hunks()` (diff.rs:771), `DiffHunkKind::Different`
> (diff.rs:894) is the changed span; **decide assertion granularity first** — per-file added/removed
> *multisets* need no context-windowing; ship that and flag header-exact `@@` grouping if asked.
> Reuse `read_text` (diff_stat.rs:86) for the binary/symlink/conflict ⇒ no-hunks rule. (3) **slice
> 3** — `diff_stream_with_copies` (merged_tree.rs:306) needs `CopyRecords` from
> `Store::get_copy_records` (store.rs:105); **VERIFY the git backend emits records at all** before
> writing it — if `jj diff --summary` shows `D`+`A` not `R`, ship 1–2 and flag copies. Don't fake
> rename detection. See [[m2-slice5-snapshot]] (off-GIL block_on tree walk),
> [[pyjutsu-0-5-0-fidelity-completion]] / [[pyjutsu-0-4-0-refinements-plan]] (verify-and-flag
> discipline), and `src/diff_stat.rs` (the pattern every slice extends).
