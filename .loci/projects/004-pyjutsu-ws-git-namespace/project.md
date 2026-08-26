---
title: Pyjutsu ws.git colocated namespace
type: project
status: active
loci:
  schema: 1
  id: 01a03f7d-f5d6-7000-bd90-910d40a731da
  projects: []
---

# PROJECT: Pyjutsu `ws.git` colocated namespace

Give the git half of a colocated repository its own namespace: annotated tags,
git configuration, `HEAD`, worktrees, objects, submodules, the reflog, and the
index.

This is Phase D of
[[.loci/projects/002-pyjutsu-refactor-jj044/IMPLEMENTATION_PLAN.md]]. Read
[[.loci/projects/002-pyjutsu-refactor-jj044/COLOCATED_GIT_SURFACE.md]] for the
argument: `gix` already ships in every wheel through jj-lib, so the real cost
is application programming interface **depth**, not call count. Minimising gix
call sites is not a goal.

Do not implement Phase C here. That is project 003.

## Why this project exists

Pyjutsu already reaches into `gix` from several places, with no shared shape.
A colocated repository has a git half that jj deliberately does not model —
annotated tags, git configuration, worktrees, submodules, the reflog — and
callers have no route to any of it. This project gathers those reads and writes
under one namespace instead of scattering them across `Workspace`.

Everything here is inside the free feature budget. jj-lib 0.44 already enables
`attributes`, `blob-diff`, `index`, `max-performance-safe`, `sha1`, `sha256`,
and `zlib-rs` on gix, and Cargo unifies features.

## Lanes

D1 blocks every other lane. D2 through D9 are independent of each other.

```text
D1 namespace scaffold      M   blocks D2..D9
D2 annotated tags          S   lands the verb A3's warning already names
D3 git config              S
D4 HEAD state              S   replaces the raw .git/HEAD write
D5 git worktrees           S
D6 object access           S
D7 submodules              M   read-only; declare the gix `attributes` feature
D8 reflog read             S
D9 git index read          S   read-only; declare the gix `index` feature
```

The plan holds each lane's gix entry points, proposed surface, steps, and test
oracle. Every lane's oracle is the `git` binary, not the `jj` binary.

## What this project must finish

Pyjutsu 0.17.0 shipped `ws.create_tag(message=...)` with a `DeprecationWarning`
that names `ws.git.create_tag` — a path that does not exist yet. D1 and D2
create it. Until they land, that warning points at nothing.

## Decided against

The plan records four rejected candidates with reasons: network transport via
gix, blame via gix, `git status` and dirwalk, and mailmap. Do not reopen them
without new evidence.

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
recorded evidence. Evidence is in `artifacts/<UTC>-baseline-full/gate.txt`.

### 2026-08-26 — D1 namespace scaffold

Lane `004/d1` creates the `ws.git` namespace and moves the four git-side reads
under it, with a deprecating alias for each. The `DeprecationWarning` that
0.17.0's `create_tag(message=...)` already ships now points at a path that will
exist once D2 lands.

**Rust.** `src/workspace/tags.rs` moves to `src/git/tags.rs` under a new
`src/git/mod.rs`. `PyWorkspace::locked`, `fresh_loader`, `finish_op`, and the
`revset_config` field widen to `pub(crate)` so the sibling module can use them
(they were private to the parent while tags.rs was `src/workspace/tags.rs`).
The native `#[pymethods]` surface is unchanged — D1 is a pure-Python move.

**Python.** New `python/pyjutsu/git.py` defines `GitView`, holding the same
`PyWorkspace` handle. `Workspace.git` is a lazily-cached property (a new `_git`
slot). Four moves, each keeping a deprecating alias that warns and delegates:

| Today (alias) | Becomes |
|---|---|
| `ws.git_refs(prefix)` | `ws.git.refs(prefix)` |
| `ws.write_git_ref(name, target)` | `ws.git.write_ref(name, target)` |
| `ws.delete_git_ref(name)` | `ws.git.delete_ref(name)` |
| `ws.remotes()` | `ws.git.remotes()` |

`git_import`, `git_export`, `sync_colocated`, `git_fetch`, `git_push` stay on
`Workspace`: they publish jj operations, they are not git-side reads.

**Tests.** `test_git_refs.py`, `test_git_ref_write.py`, and `test_git_interop.py`
use the new namespace; each keeps its whole body. One new test per alias asserts
the `DeprecationWarning` fires. `test_git_net.py` was re-pointed at
`ws.git.remotes()`. Also added `.loci/projects/*/artifacts/` to `.gitignore`:
the kickoff says the directory is git-ignored, but no rule existed, so raw gate
output would otherwise leak into lane commits.

Validation:

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
```

Evidence is in `artifacts/<UTC>-d1-focused/` and `artifacts/<UTC>-d1-gate/`.
