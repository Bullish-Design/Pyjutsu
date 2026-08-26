---
title: Pyjutsu jj read surface
type: project
status: active
loci:
  schema: 1
  id: 01a03f7d-f500-7000-9685-52321ad557fa
  projects: []
---

# PROJECT: Pyjutsu jj read surface

Bind the jj-lib reads Pyjutsu cannot yet perform: conflict content, file
content, short id prefixes, evolution, and the rewrite verbs that follow from
them.

This is Phase C of
[[.loci/projects/002-pyjutsu-refactor-jj044/IMPLEMENTATION_PLAN.md]]. Project
002 delivered the pre-bump removals, the jj-lib 0.44 upgrade, and the 0.17.0
release. Read its `project.md` for the working method and the standing rules,
and `LIBRARY_DESIGN_REVIEW.md` for the analysis this project implements.

Do not implement Phase D here. That is project 004.

## Why this project exists

Pyjutsu's mutation surface is close to complete. Its read surface stops at
metadata and diffs. Every Tier 1 lane below is a read the library cannot
perform, and each one forces callers out to a subprocess today.

## Lanes

C1, C2, and C3 are independent. C1 is the largest and the highest value; start
it first so it has the most review time.

```text
C1 conflict content and resolution   L   highest priority
C2 file content and listing          S
C3 short id prefixes                 M   carries an open design decision
C4 evolution and predecessors        M
C5 duplicate and backout             S
C6 absorb                            M
C7 fix                               M
C8 commit signing                    L   adoption-blocking for some users
C9 Tier 3 backlog                    ranked, not scheduled
```

The plan holds each lane's jj-lib entry points, proposed surface, steps, and
test oracle. Re-read every line number from the pinned source at implementation
time; line numbers move between releases.

## The open decision in C3

`IdPrefixContext` is scoped by a revset, and jj-cli reads
`revsets.short-prefixes`, a key Pyjutsu does not vendor. Two options:

1. Disambiguate across the whole repository. Simple, always correct, slower,
   and adds no configuration surface.
2. Disambiguate within a configured revset, defaulting to `visible()`. Closer
   to the CLI, and needs a new vendored configuration key.

The plan recommends option 1 for the first release. Record whichever you pick
and the reason.

## Implementation log

### 2026-08-26 — baseline

Pyjutsu 0.17.0 on `main` at `6d88c0ee6646`, pushed to `origin`. The working copy
sits directly on `main` (two empty description-less commits above it were
abandoned). jj-lib 0.44.0, gix 0.85.0 (one resolved version), devenv pins the
jj 0.44.0 CLI.

Real baseline numbers:

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: 401 collected, exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
```

The parallel reporter suppresses pytest's summary line; exit code 0 is the
recorded evidence. Evidence is in
`artifacts/20260826T..Z-baseline-full/` (project 004's copy; the two projects
share the gate run).

### 2026-08-26 — C1 conflict content and resolution

Lane `003/c1` binds jj-lib's whole conflict read/resolve path, so callers stop
shelling out to `jj resolve` / reading the working copy by hand.

**Rust.** New `src/conflicts.rs`. `conflict_content` reads the tree value at
`path`, materializes it via `conflicts::materialize_tree_value` (with the
tree's own conflict labels, so the marked text is byte-identical to the CLI's),
and renders with `materialize_merge_result_to_bytes` in the requested
`ConflictMarkerStyle`; a plain file yields its raw content. `conflict_sides`
round-trips through `materialize_merge_result_to_bytes` + `parse_conflict`
with an explicit marker length, exactly like `update_from_content` does, and
returns one string per merge term in jj's conflict term order — each add with
its preceding base, so a regular 3-way conflict is `[side_a, base, side_b]`.
`resolve_conflict` (in `transaction.rs`) runs `update_from_content` with the
**unsimplified** file ids (preserving the tree-conflict shape) inside the open
transaction, rewrites `@`, and returns the new commit. The resolved-value
branch mirrors jj-lib's own working-copy snapshot: a fully resolved result
replaces the whole merge with one normal file value (resolved executable bit
and copy id preserved), a still-conflicted result keeps the merge shape via
`with_new_file_ids`.

Flat native methods: `PyRepoView::conflict_content` /
`PyRepoView::conflict_sides` (both in `repo_view.rs`) and
`PyTransaction::resolve_conflict`. Python: `RepoView.conflict_content(path,
rev, style)`, `RepoView.conflict_sides(path, rev)`, `Transaction.resolve_conflict(path,
content)`. `_pyjutsu.pyi` tracks all three.

**Observation that shaped the resolve test.** After a resolve, `jj status`
still lists the path as modified and `jj resolve --list` exits 2 with "No
conflicts found at this revision" — and the pinned CLI does exactly the same
thing after `jj resolve --tool :ours`. The oracle assertions therefore check
the real contract: the commit has no conflict, `jj file show -r @` holds the
resolution, and `jj resolve --list` reports no conflicts (as a non-zero exit).

Validation (full gate, then the SHA-256 matrix):

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
PYJUTSU_TEST_OBJECT_HASH=sha256 pytest -q PASS: exit 0
```

Evidence is in `artifacts/<UTC>-c1-gate/` and `artifacts/<UTC>-c1-sha256/`.

### 2026-08-26 — C2 file content and listing

Lane `003/c2` binds `jj file show` and `jj file list`, so a caller reads one
file at one revision without a checkout.

**Rust.** `PyRepoView::file_content` materializes the tree value at `path`
(`materialize_tree_value`, with the tree's own labels) and returns the raw
bytes for a regular file, the target bytes for a symlink, a `ConflictError`
pointing at `conflict_content` for a conflicted path, and a clear error for an
absent/non-file path. `PyRepoView::file_list` walks `tree.entries_matching`
with either an `EverythingMatcher` (no `paths`) or a union of filesets parsed
through the same `FilesetParseContext` shape the snapshot uses
(`workspace.rs`), sorted like the CLI.

**Python.** `RepoView.file_content(path, rev="@"  ) -> bytes` and
`RepoView.file_list(rev="@", paths=None) -> list[str]`; `_pyjutsu.pyi` tracks
both.

**Test oracle details.** The CLI's `glob:*.txt` does not cross directory
boundaries (a root-only glob), and the binary oracle must run the CLI in
binary mode (the text-mode `jj` helper mangles bytes). Both are encoded in the
tests.

Validation (full gate, plus a focused SHA-256 run):

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
PYJUTSU_TEST_OBJECT_HASH=sha256 (focused) PASS
```

Evidence is in `artifacts/<UTC>-c2-gate/` (red Ruff run) and
`artifacts/<UTC>-c2-gate-green/`.

### 2026-08-26 — C3 short id prefixes

Lane `003/c3` binds jj-lib's prefix-disambiguation machinery, so callers stop
reimplementing shortest-unique-id with an index they do not have.

**The open decision — resolved: whole-repository disambiguation.** The plan
offered (1) disambiguate across the whole repo (no configuration surface,
always correct, slower) or (2) within a configured revset defaulting to
`visible()` (closer to the CLI, needs a new vendored `revsets.short-prefixes`
key). I chose option 1 and recorded it in `src/id_prefix.rs`:

- It has no configuration surface and no new vendored data.
- It can never return an ambiguous prefix: every id in the index is a
  neighbor, so a returned prefix resolves uniquely by construction.
- The cost is speed on very large repos (the whole index is consulted), which
  is acceptable for the first release.

The observable difference: commit-id prefixes can be **longer** than the
CLI's `visible()`-scoped answer when hidden (abandoned/rewritten) commits
exist, because the whole index includes them. Change-id prefixes agree with
the CLI, because rewritten commits keep their change id. The tests assert
this contract: change-id byte-equality with `change_id.shortest()`, commit-id
prefix+resolve-back (plus `len >=` the CLI's answer).

**Rust.** New `src/id_prefix.rs`: `shortest_commit_prefix` /
`shortest_change_prefix` (via `IdPrefixContext::new` without
`disambiguate_within`, then `shortest_*_prefix_len`, which also disambiguates
against bookmark/tag names — the CLI's own rule) and `shortest_prefix`
(dispatch: hex → commit id, `k-z` letters → change id; the two alphabets are
disjoint). `CommitData::build` now populates `short_commit_id` /
`short_change_id` on every commit read, since the repo is the context
everywhere (a superset of "populated when the view can supply a context").

**Python.** `RepoView.shortest_prefix(id)`; `Commit` gains the two optional
fields. `_pyjutsu.pyi` tracks the native method. The model-shape golden
(`tests/golden/model_fields.json`) was regenerated for the two new Commit
fields.

The first gate runs stopped on clippy (a dead helper and a
`manual-is-ascii-check` finding) and the golden guard; both were fixed and
the gate restarted. Red runs preserved.

Validation:

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
```

Evidence is in `artifacts/<UTC>-c3-gate/` (red) and
`artifacts/<UTC>-c3-gate-green/` + `-c3-gate-final/`.

### 2026-08-26 — C4 evolution and predecessors

Lane `003/c4` binds jj-lib's evolution read, so callers can follow a change
across its rewrites (gitman's rewrite-heavy workflow) without `jj evolog`.

**Rust.** New `src/evolution.rs`: `PyRepoView::evolution(change_id, limit)`
parses the z-k change id, resolves its **visible** target commits via
`repo.resolve_change_id`, and drives `evolution::walk_predecessors` (off the
GIL, via `pollster::block_on` around the stream) into plain `EvolutionEntryData`
rows. Each row nests the `CommitData` (with `predecessor_ids` filled from the
entry) and the `OperationData` that created/last rewrote it.

**Design decisions.**

- *Visible starts, hidden steps.* The walk starts from the visible commits of
  the change (like `jj evolog`), so hidden/older steps arrive as predecessors
  and are not double-counted; starting from every indexed target over-counted
  the chain in testing.
- *`Commit.predecessor_ids` populated only by evolution.* Ordinary reads leave
  it empty: finding a commit's creating operation requires an op-log walk, and
  paying that on every commit read is not acceptable. The evolution machinery
  knows the creating operation already, so it fills the field for free. The
  model field defaults to `[]`; the docstring says so.
- *Full-length change ids only.* A shorter z-k string is a prefix, and
  `resolve_change_id` panics on the ambiguity; the native layer rejects
  non-full-length ids with a clear error.
- *Abandoned changes vanish.* After abandoning the only commit of a change,
  `evolution()` returns `[]` — the CLI's `jj evolog -r <change>` errors
  "Revision doesn't exist" for the same reason. Test asserts the empty list.

**Python.** `RepoView.evolution(change_id, limit=None)`; new `EvolutionEntry`
model (`commit` + `operation`); `Commit.predecessor_ids`; `_pyjutsu.pyi`
tracked; the model-shape golden regenerated.

The oracle tests showed that the chain includes jj's auto-snapshot steps (a
`describe` after writing a file creates a snapshot commit first), so the
fixture chains are longer than a first guess — the tests compare
commit-for-commit against `jj evolog` and assert structure, not length.

Validation (full gate, plus a focused SHA-256 run):

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
PYJUTSU_TEST_OBJECT_HASH=sha256 (focused) PASS
```

Evidence is in `artifacts/<UTC>-c4-gate/` (red clippy run) and
`artifacts/<UTC>-c4-gate-final/`.

### 2026-08-26 — C5 duplicate

Lane `003/c5` binds `jj duplicate` (the lane title says "duplicate and
backout", but the plan's surface and jj-lib entry points cover only
`duplicate`; backout is a jj-cli composition over lower-level primitives and
has no listed jj-lib entry point, so it stays out of scope).

**Rust.** `PyTransaction::duplicate(revsets, onto=None)`. The target revsets
resolve (multi-revision, dedup by id), pass the immutable/root guard, then
order **reverse-topologically within the target set** (children before
parents — jj-lib's documented requirement) via
`dag_walk::topo_order_reverse_ok` with the neighbors filtered to the targets.
The first implementation walked every ancestor (the unfiltered topo sort
pulls in the whole ancestry) and panicked in jj-lib's `set_parents` on the
root's empty parents — the filter is the fix. `onto=None` calls
`rewrite::duplicate_commits_onto_parents`; `onto` (one or more single-revision
revsets) calls `rewrite::duplicate_commits` with the resolved parent ids.
Returns the duplicated commits in children-first order.

**Python.** `Transaction.duplicate(commits, onto=None)` accepting a revset or
a list; `_pyjutsu.pyi` tracked.

**Tests.** `tests/test_duplicate.py` against `jj duplicate`: single commit
(same tree/description, new change and commit ids; the CLI resolves the new
change to the same commit id), onto (parent is the destination, verified via
`jj change_ids`/`jj parent_commit_ids`), a two-commit chain onto (internal
structure preserved: B's duplicate on A's duplicate), originals untouched,
empty selection and immutable root refused.

Validation:

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
```

Evidence is in `artifacts/<UTC>-c5-gate/`.
