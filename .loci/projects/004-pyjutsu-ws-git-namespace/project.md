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

_Append one dated entry per lane: what changed, the validation block, and every
decision made._
