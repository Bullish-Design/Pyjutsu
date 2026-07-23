# 14 — Remaining pyjutsu bindings to drive gitman's raw-git surface to zero

**Date:** 2026-07-22
**Status:** SCOPING — proposed, not built. Successor to project 13 (which shipped the trunk-model
bindings across 0.10.0 + 0.11.0). Everything here is **consumer-driven** (gitman); pyjutsu is
intrinsically feature-complete against its own v1 spec as of 0.11.0.
**Driver:** gitman `.scratch/projects/25-review-survivors/` (the git-interop audit) + gitman's live
`src/gitman/` raw-`git` subprocess call sites. gitman owns pyjutsu, so its "we shell out to git here"
spots are pyjutsu scope by choice, not constraint — the same stance as project 13.
**Version target:** pyjutsu `0.11.0` → `0.12.0`.
**jj-lib pin:** `=0.42.0` (`Cargo.toml:16`) — every item below is expressible against 0.42; no bump
expected (confirm P1's `merge_commit_trees` signature during build).

---

## Context — what 0.11.0 already closed

Project 13's promotion triggers fired: **P4** (`is_ancestor` / `patch_id`) and **P5** (`create_tag` /
`push_tag` / `git_default_branch`) both shipped in **0.11.0** (git `f9f9337` / `2a2a17d`, the
"feat/14" batch), alongside two-revset diff (`diff_between` / `diff_stat_between`). That makes
**gitman's entire `tags.py` retireable today** with no new pyjutsu work:

| gitman `tags.py` surface | replaced by | binding |
|---|---|---|
| `create_annotated_tag` (`git tag -a`) | `Workspace.create_tag(name, target, message, force)` | 0.11.0 |
| `push_tag` (`git push … refs/tags`) | `Workspace.push_tag(name, remote)` | 0.11.0 |
| `remote_default_branch` (`git symbolic-ref`) | `Workspace.git_default_branch(remote)` | 0.11.0 |
| `tag_exists` (`git rev-parse refs/tags`) | `tags()` revset / `create_tag(force=False)` clash-`GitError` | 0.11.0 |

That retirement is **gitman-side** and needs nothing here — it is tracked as the first consumer task,
not a pyjutsu item.

## What remains — five items

`is_ancestor` + `patch_id` deliver *ancestry* and *single-commit content identity* but deliberately
stopped short of a **3-way merge / merge-tree** primitive. Together with three small colocated-git
interop reads/writes (P2–P4) and a colocate-time exclude fix (P5), that is the whole remaining
raw-`git` surface in gitman plus the one colocation rough edge. Closing P1–P4 drives gitman's
git-subprocess count to **zero**.

Priority: **P1 is the must-have** (retires two call sites + the tree rev-parse; no clean gitman
workaround). **P2–P4 are small interop reads/writes** — low effort, each retires one call site. **P5 is
a one-line-of-behavior colocation fix** — trivial, and it removes a manual step every consumer hits.

| # | Binding | Retires (gitman) | jj-lib / gix mechanism | Size |
|---|---------|------------------|------------------------|------|
| P1 | `RepoView.try_merge(a, b, base=None) -> {tree_id, has_conflict}` | `state.py:153,201` merge-tree ×2 + `state.py:176` tree rev-parse | `merge_commit_trees` / `MergedTree` | **M** |
| P2 | `Workspace.git_refs(prefix="refs/heads/") -> dict[str,str]` | `state.py:266` `for-each-ref` | linked gix `Repository` ref read | S |
| P3 | `Workspace.tracked_ignored_paths() -> list[str]` | `state.py:290` `ls-files --cached --ignored` | `GitIgnoreFile` + `@`-tree walk | S |
| P4 | `Workspace.write_git_ref(name, target)` / `delete_git_ref(name)` | `reconcile.py:40,43` `update-ref [-d]` | gix direct ref write (as `create_tag`) | S |
| P5 | colocate writes `/.jj/` to `.git/info/exclude` | manual `.git/info/exclude` edit after every colocate | gix/std file write in `adopt_existing_git` | XS |

---

## P1 — `try_merge`: a 3-way merge / merge-tree primitive  *(must-have)*

**Motivation.** gitman shells `git merge-tree --write-tree` in two places (`state.py:_merge_tree_relation`
`:153`, `_merge_tree_conflicts` `:201`) for two jobs `is_ancestor`/`patch_id` cannot do:
1. The **general content relation** `(forge_has_new, local_has_new)` over arbitrary multi-commit
   divergence — the twin-vs-real-divergence test behind gitman's content-aware `status` (kills the
   15-RC2 `adopt` data-loss hint). `patch_id` now covers the *single-commit re-hash-twin* case, but
   not the general multi-commit tree comparison.
2. **Conflict prediction before a destructive trunk rebase** — gitman uses merge-tree because
   branch-mode `tx.rebase(...).has_conflict` is unreliable under a descendant `@` (the stale-commit-id
   footgun already documented in gitman's sync path).

**Proposed API.**
```python
# on PyRepoView (a read — no transaction, no op)
def try_merge(self, a: str, b: str, base: str | None = ...) -> dict[str, object]:
    # -> {"tree_id": str, "has_conflict": bool}
```
- `base=None` → auto merge-base of `a` and `b` (jj `merge_commit_trees` behaviour); an explicit
  `base` does a fixed 3-way merge.
- `tree_id` lets the caller compare the merged tree to each tip's tree (answering `forge_has_new` /
  `local_has_new` by tree-equality — no separate `rev-parse ^{tree}` needed if `Commit` also carries
  `tree_id`; see note).
- `has_conflict` is the pre-rebase conflict predicate.

**jj-lib mechanism.** `merge_commit_trees(store, [a, b])` (auto base) → `MergedTree`; `.has_conflict()`
for the flag, `.id()` for the tree oid. Confirm the exact 0.42 entry point and whether an explicit
base needs `merge_trees` at the tree layer instead. No git subprocess — pure jj-lib.

**Note — `Commit.tree_id`.** The `Commit` model exposes no tree oid (`models.py:46` — only
`empty` / `has_conflict`). Add `tree_id` to the projected commit dict so a caller can compare
`try_merge().tree_id` against each tip without a `rev-parse ^{tree}` shell-out (retires `state.py:176`).
Trivial in `convert.rs`.

**Tests.** (a) two divergent lanes, content-equal → `has_conflict False`, merged tree equals both
tips. (b) genuine divergence → merged tree differs from each tip. (c) overlapping edits → `has_conflict
True`. Mirror gitman's `_merge_tree_relation` truth table.

**gitman consumer.** Replaces both merge-tree call sites in `state.py` and the tree rev-parse; content
relation + pre-rebase conflict check both go in-process.

---

## P2 — `git_refs`: read colocated `refs/heads/*`  *(small)*

**Motivation.** gitman's colocated-ref desync detection (`state.py:_git_refs_heads` `:266`,
`git for-each-ref refs/heads`) must read the **on-disk git refs that may differ from jj's last-imported
`@git`** — seeing that drift is the entire point, so `bookmarks()` (which reports jj's view) cannot
substitute.

**Proposed API.**
```python
def git_refs(self, prefix: str = "refs/heads/") -> dict[str, str]:
    # -> {"<short-name>": "<oid>"}  (prefix-stripped keys, hex oids)
```
**Mechanism.** Read the already-linked gix `Repository` refs directly (same machinery `create_tag`
uses). No jj view involved — deliberately the raw git side. **Test:** write a ref out-of-band, assert
`git_refs` sees it while `bookmarks()` still shows the stale `@git`. **Consumer:** `state.colocated_ref_desync`.

---

## P3 — `tracked_ignored_paths`: the gitignore-status query  *(small)*

**Motivation.** `untrack_paths` (0.10.0) fixes the *state* (a tracked-but-ignored file) but nothing
*detects* it. gitman shells `git ls-files --cached --ignored --exclude-standard` (`state.py:290`) to
warn on it. pyjutsu has **no gitignore/status query anywhere** in the surface (confirmed — no
`check_ignore`/`gitignore`/blob binding).

**Proposed API.**
```python
def tracked_ignored_paths(self) -> list[str]:
    # paths tracked in @ that the working-copy gitignore would also ignore
```
**Mechanism.** Intersect `@`'s tracked tree with the working-copy `GitIgnoreFile` matcher — jj already
builds both during snapshot, so this is a walk, not new machinery. **Test:** track a file, then
gitignore it; assert it appears. **Consumer:** the `untrack` warning path / `_tracked_but_ignored`.

---

## P4 — `write_git_ref` / `delete_git_ref`: heal colocated ref drift  *(small)*

**Motivation.** `reconcile.py:40,43` force-writes/deletes `refs/heads/*` via `git update-ref [-d]`
precisely **when `git_export()` is itself broken** by a leftover/conflicting ref — so
"`set_bookmark` + `git_export`" is not a substitute (it's the thing that's failing). This is a
last-resort colocated-git repair, kept raw for exactly that reason.

**Proposed API.**
```python
def write_git_ref(self, name: str, target: str) -> None:   # refs/heads/<name> := target
def delete_git_ref(self, name: str) -> None:               # rm refs/heads/<name>
```
**Mechanism.** gix direct ref write/delete against the linked `.git` (the create_tag ref-write path
already exists internally — expose it). Scoped to `refs/heads/*`; does **not** touch the jj view (the
caller re-imports/`sync_colocated` afterward as today). **Risk:** it deliberately bypasses jj — document
it as a reconcile-only escape hatch, never a normal-path writer. **Consumer:** `reconcile` drift heal.

---

## P5 — colocate writes `/.jj/` to `.git/info/exclude`  *(colocation rough edge)*

**Motivation.** `Workspace.init(path, colocate=True)` adopts an existing `.git` but does **not** add
`/.jj/` to `.git/info/exclude`, so immediately after colocation `git status` shows `.jj/` as an
untracked directory. The reference CLI (`jj git init --colocate`) writes this exclude as part of
colocation, precisely so the jj metadata dir stays invisible to git. **Found in the field** (2026-07-22)
re-colocating the gitman repo: the colocate succeeded and everything else was in sync, but `git status`
reported `?? .jj/` until `/.jj/` was hand-added to `.git/info/exclude`. Every consumer that colocates
hits this and must fix it manually.

**Why `.git/info/exclude`, not `.gitignore`.** The exclude is **local and uncommitted** — the correct
home for machine-local, per-clone ignores. `.jj/` must never be committed to `.gitignore` (it's not
shared project state; a non-colocated clone has no `.jj/`), which is exactly why jj uses `info/exclude`.

**Proposed behavior.** In the colocate path (`adopt_existing_git`, and the fresh-`.git` init path too),
after linking git, ensure `.git/info/exclude` contains a `/.jj/` line — append it if absent, idempotent
(never duplicate on re-colocate). Mirror `jj`'s own text if practical. No Python API surface change; it's
a side effect of `Workspace.init(colocate=True)`.

**Mechanism.** Read/append `.git/info/exclude` (plain std file I/O against the resolved git dir; gix can
give the git-dir path). Guard on the line already being present so re-adopting a colocated repo is a
no-op. Do it for both the adopt-existing and create-new `.git` colocate branches.

**Test.** Colocate a fresh work repo, then assert `.git/info/exclude` contains `/.jj/` and that
`git status --porcelain` does **not** list `.jj/`. Re-run colocate; assert the line isn't duplicated.

**Consumer.** Every colocation — gitman `ensure_colocated` / `init --colocate`, and the re-colocate
runbook (gitman project 27). Removes a manual post-colocation step.

**Rough size:** XS (a few lines in the Rust colocate path + one probe).

---

## Sequencing

1. **gitman-side, now (no pyjutsu work):** ~~delete `tags.py`~~ ✅ **done** (gitman `main`, commit
   `c4505d0` — `release`/tag flow routes through `create_tag`/`push_tag`/`git_default_branch`;
   validated the 0.11.0 surface end-to-end and removed the largest single subprocess module).
2. **pyjutsu 0.12.0:** P1 (`try_merge` + `Commit.tree_id`) first — highest leverage. Then P2/P3/P4 as
   one small interop batch (all gix-side reads/writes; ~a day together), and **P5** (the colocate
   exclude) folded in with them (XS). P5 has no API surface, so it can ship independently/first if
   convenient — it just makes every colocate clean.
3. **gitman-side follow:** retire the four remaining call sites; gitman's raw-`git` subprocess count
   reaches **zero** (`doctor`'s "git on PATH" check can then be relaxed to optional).

## Intrinsic pyjutsu items explicitly NOT in scope here

Called out by pyjutsu's own roadmap (project-12 gap table), no active consumer, left deferred:
tag *fetching* (gated on jj#7528), word/inline color-words diff (`diff()` is line-level; gitman renders
its own), a CLI/TUI over the typed core (→ separate `pyjutsu-cli`), file-content/blob read, and
conflict-*resolution* bindings. Build any of these only when a consumer needs them.

---

## Ground rules

jj-lib native, in-process — **no new `git` subprocess surface**; every item ships with an in-process
probe against a bare origin + colocated work repo (the established pyjutsu test pattern) and maps to a
specific gitman call site it retires. Tracked design doc under `.scratch/projects/` (commit it). No
AI-authorship trailers.
