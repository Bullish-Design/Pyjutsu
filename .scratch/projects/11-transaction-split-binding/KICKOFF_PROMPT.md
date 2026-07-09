# Kickoff — pyjutsu 0.9.0: `tx.split` partial-tree binding (Project 11) + version-guard hardening (Project 10 P3)

> Paste this whole file as the opening prompt of a **fresh** session in
> `~/Documents/Projects/Pyjutsu`. It is self-contained; the two design docs it
> references live in `.scratch/projects/`.

## Your task

Ship **pyjutsu 0.9.0** with two items, in this order (P3 first — it's a small
warm-up that also cleans up version discipline you'll touch again when you bump):

1. **Project 10 §P3** — harden the import-time version guard so the hand-maintained
   version strings can't silently drift from the compiled artifact.
2. **Project 11 (S1+S2+S3)** — a native, non-interactive **hunk-level split**
   transaction binding (`tx.split` / `tx.select_tree`). This is the main event.

Read both design docs in full before writing code:
- `.scratch/projects/10-adopt-tag-visibility-and-keep-refs/issue-overview.md` (§P3)
- `.scratch/projects/11-transaction-split-binding/issue-overview.md` (S1/S2/S3, build
  order, jj-lib hooks, test plan, references — it is thorough; lean on it)

## Ground rules for this repo (important — read before running anything)

- **devenv for every in-repo command.** `devenv shell -- <cmd>`. Never bare
  `uv`/`python`/`pytest`/`cargo` — the shell is fresh and lacks the project env.
  Build the extension with `devenv shell -- maturin develop`; test with
  `devenv shell -- python -m pytest tests/ -q`; lint with `devenv shell -- cargo clippy`.
- **Do NOT run bare `cargo fmt`.** The committed Rust tree is *not* fmt-clean under
  this devenv's rustfmt (pre-existing drift in `diff_stat.rs`, `errors.rs`,
  `repo_view.rs`, `transaction.rs`, `workspace.rs`). Running `cargo fmt` rewrites
  unrelated files and pollutes your diff. Hand-match the surrounding style instead;
  CI does not gate on `fmt --check`. If you want to verify only your own additions,
  `cargo fmt --check` then confirm your new symbols don't appear in the diff.
- **Version control:** gitman/jj are **not** on PATH here (jj lives inside devenv);
  this is a plain colocated `.git`, so use `git`. Branch off `main` first
  (e.g. `feat/11-tx-split`). Commit as you go. **Do not push** without an explicit
  ask — the last round pushed only when the user said so.
- **No AI-authorship trailers** in commits/PRs/docs/comments.
- **jj-lib 0.42 source is unpacked locally** — read it to confirm behaviour rather
  than trusting prose:
  `~/.cargo/registry/src/index.crates.io-*/jj-lib-0.42.0/src/` (see `rewrite.rs`).
- Line-number citations below are **approximate** (from docs written earlier) —
  re-grep to confirm before editing; the code moves.

---

## Item 1 — Project 10 §P3: version-guard hardening

### Current state (three hand-maintained version strings)

- `src/lib.rs:~31` — `const JJ_LIB_VERSION: &str = "0.42.0";`, returned by the
  `version()` PyO3 fn (`_ext.version()`). **Hardcoded**, not derived from the linked
  jj-lib crate. A `version_is_pinned` unit test asserts it equals `"0.42.0"`.
- `python/pyjutsu/__init__.py` — `__version__ = "0.8.0"` and
  `JJ_LIB_TARGET = "0.42.0"`, both hand-maintained; `JJ_VERSION = _ext.version()`.
- The guard: `if JJ_VERSION != JJ_LIB_TARGET: raise PyjutsuError("broken build: …")`.
  It compares the Python-side `JJ_LIB_TARGET` against the compiled `version()` — so it
  only ever catches drift between *two hand-maintained numbers*, and it fires
  spuriously during editable installs when Python metadata is bumped before a rebuild
  (the P3 footgun).

### Goal

Make the jj-lib version a **build-derived fact** so it can't drift, and make the
guard protect something real (a genuinely stale/mixed compiled extension), without
false positives in the normal `maturin develop` workflow.

### Recommended approach (decide + implement — confirm the mechanics first)

1. **Source the jj-lib version from the build.** Add a `build.rs` that reads the
   **resolved** jj-lib version (parse `Cargo.lock`, or shell `cargo metadata`) and
   emits `cargo:rustc-env=PYJUTSU_JJ_LIB_VERSION=<ver>`; change `src/lib.rs` to
   `const JJ_LIB_VERSION: &str = env!("PYJUTSU_JJ_LIB_VERSION");`. Now `_ext.version()`
   reflects the actually-built dependency, and the `version_is_pinned` test becomes
   "matches the Cargo pin" rather than a second hardcoded copy.
2. **Keep a guard that catches a stale *compiled* extension.** The check worth having
   is "the installed Python package matches the compiled `.so`." Expose the compiled
   **pyjutsu** version too (e.g. a `pyjutsu_version()` fn returning
   `env!("CARGO_PKG_VERSION")`), and guard `__version__ == _ext.pyjutsu_version()`.
   That is the real "did you forget to rebuild after a bump" signal and it does *not*
   false-fire once you actually rebuild. Fold `JJ_LIB_TARGET` into
   `_ext.version()` (drop the separate hand-maintained constant, or keep it as an
   alias of the compiled value for back-compat — your call; note it in the docstring).
3. If a `build.rs` reading `Cargo.lock`/`cargo metadata` proves fragile under the
   nix/devenv sandbox, fall back to: keep `JJ_LIB_VERSION` hardcoded in `lib.rs` (one
   place), have Python read `JJ_LIB_TARGET = _ext.version()` (so Python stops
   duplicating it), and guard `__version__` against a compiled `pyjutsu_version()`.
   Document why. The non-negotiable outcome: **no two hand-maintained copies of the
   same version number**, and **no false guard trip on editable installs**.

### Acceptance

- Bumping only `python/pyjutsu/__init__.py`'s `__version__` without rebuilding raises
  a clear "stale build — rebuild the extension" error (the good signal), while a
  correctly built tree imports clean.
- No duplicated hand-maintained jj-lib version string.
- `test_build.py` updated to cover the new invariant; full suite green.

---

## Item 2 — Project 11: `tx.split` (S1) + `tx.select_tree` (S2) + selection vocab (S3)

The design doc is complete; this is the execution summary. **Read
`11-transaction-split-binding/issue-overview.md` first** — especially §"jj-lib hooks
that already exist", §"Suggested build order", and §"Confidence & caveats".

### The gap (one sentence)

pyjutsu's mutation surface moves whole trees or whole files only (`restore`'s
`FilesMatcher`, `squash`'s full-tree `CommitWithSelection`); there is no primitive to
divide a commit's diff **within a file** (hunk-level). jj-lib *can* — pyjutsu never
binds it.

### jj-lib hooks (already present — this is a binding, not new jj-lib work)

- `jj_lib::rewrite::CommitWithSelection { commit, selected_tree, parent_tree }` —
  already imported and used in `src/transaction.rs` `squash` (`:~376`, but only with a
  **full** `selected_tree`). Helpers `is_full_selection()` / `is_empty_selection()`
  give the no-op/empty guards for free.
- `jj_lib::merged_tree_builder::MergedTreeBuilder` — builds a tree from a chosen set of
  path/content entries; already used inside jj-lib's `restore_tree`. This is what
  assembles a **partial** `selected_tree`.
- `squash_commits(repo, &[CommitWithSelection], dst, keep_emptied)` — consumes
  selections and handles the empty branch. jj's own `split` is two tree-rewrites over
  this same machinery.

**Confirm before building (doc flags this as inferred):** that an arbitrary partial
`selected_tree` can be assembled from a programmatic hunk list via `MergedTreeBuilder`
*without* the CLI's interactive diff editor, and settle the two-commit write path
(sibling vs stacked — see below). Read `jj-lib-0.42.0/src/rewrite.rs` around
`CommitWithSelection` (~:1254), `is_full/empty_selection` (~:1262/:1271),
`MergedTreeBuilder` (~:49/:160), `squash_commits` (~:1305, empty branch ~:1329), and
the jj CLI crate's `split` command for the reference tree-build + write sequence.

### Selection vocabulary (S3) — decision: **(A) reuse the emitted `Hunk` identity**

pyjutsu's `RepoView.diff()` already emits structured, lossless hunks
(`src/repo_view.rs:~294-346`; `python/pyjutsu/models.py` `Hunk`/`HunkLine`:
`{old_start, old_lines, new_start, new_lines, lines:[{kind, content}]}`). Make the
split selection reference **those very hunks**: `selection = {path: [hunk selector]}`
where a selector picks hunks from `diff(commit)`'s output (by index and/or by
`(old_start, new_start)` key). No new patch grammar, no `@@`-header parsing. Pin the
contract that the selection must come from a `diff()` of the *same* commit so indices
are stable. (Rejected: unified-diff text — pyjutsu deliberately doesn't emit byte-exact
`@@` headers; fileset/path-only — that's just today's whole-file `restore`.)

### Build order

1. **`tx.select_tree(commit, selection) -> tree_id` (S2).** Accept the S3-A structured
   selection across the FFI, build the partial `selected_tree` (and `parent_tree`) with
   `MergedTreeBuilder`, validate with `is_full_selection`/`is_empty_selection`. This is
   the one well-tested "selection → tree" primitive; everything else composes on it.
   Runs **on the GIL** like `squash`/`restore` (`MutableRepo` is `!Send`).
2. **`tx.split(commit, selection, mode=...) -> (first, second)` (S1).** Compose
   `select_tree` + two commit writes via `CommitWithSelection`/`squash_commits`. Pick
   the binding's contract: **sibling** split (both children of the same parent — what
   gitman wants) as the default; optionally support stacked (jj's default: parent ←
   first ← second). Whole-file selection must reproduce today's path-scoped `restore`
   carve (so `split` subsumes it); empty/full selection → clear typed errors.
3. **Surface + docs.** Add to `python/pyjutsu/_pyjutsu.pyi` (transaction stub,
   ~:105-125), `python/pyjutsu/transaction.py` docstrings, and a note in
   `docs/PYJUTSU_CONCEPT.md` that the power surface now covers sub-file rewrites.
4. **Version bump to 0.9.0** (do the Item-1 hardening *before* this so there's one
   place to change), and mention gitman's `08-split-lane-capability` is unblocked.

### Edge cases to define + test (don't leave undefined)

- Binary / conflicted / renamed file inside the selection → reject with a typed error
  *or* fall back to whole-file; decide, document, and test.
- Empty selection (no-op carve) and full selection (would be a rename) → distinct clear
  errors, leaning on jj-lib's `is_empty_selection`/`is_full_selection`.
- Multiple disjoint hunks in one file; hunks across multiple files in one selection.

### Test style — mirror the CLI-parity pattern

Follow `tests/test_rewrite.py`'s `test_restore_*_matches_cli` differential style:
build a repo, run `tx.split` over a selection, and assert commit-id / tree / change-id
parity against the equivalent `jj split` over the same selection where feasible; plus
structural asserts (first = selected only, second = remainder only, reassembly = the
original tree). Whole-file selection == the `restore` carve. Put tests in a new
`tests/test_split.py` (and extend `test_rewrite.py` if it fits there).

---

## Definition of done (both items)

- `devenv shell -- cargo clippy` clean; `devenv shell -- python -m pytest tests/ -q`
  fully green (currently 209 tests — add the new split + guard tests).
- Your diff contains **only** intentional changes (no `cargo fmt` churn on untouched
  files — verify with `git diff --stat`).
- `__version__` bumped to `0.9.0` with the version discipline from Item 1 applied;
  `_pyjutsu.pyi` + docstrings updated for the new verbs.
- Update `11-transaction-split-binding/issue-overview.md` and
  `10-…/issue-overview.md` §P3 with a "shipped" note (like project 10 §P1 got), and
  record any jj-lib mechanism you confirmed/corrected while building.
- Commit incrementally on the feature branch; **do not push** unless asked.

## Fast repo orientation (grep anchors — re-verify line numbers)

- `src/transaction.rs`: imports `CommitWithSelection`/`squash_commits`/`restore_tree`
  (~:27-35); `squash` full-tree selection (~:352-405, `selected_tree` ~:376);
  `restore` matcher (`EverythingMatcher`/`FilesMatcher`, ~:411-457); `!Send`/GIL note
  (~:3-13).
- `src/repo_view.rs`: `diff()` hunk surface (~:294-346).
- `python/pyjutsu/models.py`: `Hunk`/`HunkLine`. `python/pyjutsu/transaction.py`: the
  Python wrapper where `split`/`select_tree` get their public docstrings.
- `src/lib.rs`: `JJ_LIB_VERSION` const + `version()` (~:31-36) + `version_is_pinned`
  test (~:55). `python/pyjutsu/__init__.py`: the version guard.
- Prior art for a native binding round with parity tests: project 08's
  `09-pyjutsu-0.7.0-code-review` and `tests/test_rewrite.py`.
