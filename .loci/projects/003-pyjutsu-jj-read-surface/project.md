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
