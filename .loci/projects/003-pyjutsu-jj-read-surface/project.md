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
