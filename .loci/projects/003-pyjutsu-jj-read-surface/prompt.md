# Implementation prompt — Phase C, the jj read surface

Work in the Pyjutsu repository. Implement **Phase C** of
`.loci/projects/002-pyjutsu-refactor-jj044/IMPLEMENTATION_PLAN.md`, starting
with C1.

Read these first, completely, in this order:

1. `.loci/projects/002-pyjutsu-refactor-jj044/IMPLEMENTATION_PLAN.md` — Phase C is the scope.
2. `.loci/projects/003-pyjutsu-jj-read-surface/project.md` — this project.
3. `.loci/projects/002-pyjutsu-refactor-jj044/project.md` — the method, and what 0.17.0 already changed.
4. `.loci/projects/002-pyjutsu-refactor-jj044/LIBRARY_DESIGN_REVIEW.md` — the analysis behind these lanes.
5. `AGENTS.md` and the full `.agents/skills/my-ai/SKILL.md`.

Do **not** implement Phase D. Do not create `ws.git`. That is project 004.

## Objective

Bind the reads Pyjutsu cannot perform, so callers stop shelling out.

```text
C1 conflict content and resolution   L   start here
C2 file content and listing          S
C3 short id prefixes                 M
C4 evolution and predecessors        M
C5 duplicate and backout             S
C6 absorb                            M
C7 fix                               M
C8 commit signing                    L
```

C1, C2, and C3 are independent. Land each as its own verified lane. C9 is a
ranked backlog, not work to schedule.

## Baseline

The project starts from Pyjutsu 0.17.0 on `main`, binding jj-lib 0.44.0 with a
green gate: 7 Rust tests and 401 Python tests. Record the numbers you actually
observe before your first edit.

## Rules carried forward from project 002

1. Prefer jj-lib. Reach past it only where the audit records a gap.
2. Mark every such site with a `// jj-lib gap:` comment naming the missing API
   and the release checked.
3. Declare every direct dependency feature Pyjutsu itself calls. Never rely on
   a transitive crate's feature choice.
4. Re-verify every vendored copy at each jj-lib upgrade. The list is
   `src/config/revsets.toml` and the `git.object-hash` policy in
   `git_object_hash` (`src/workspace.rs`).
5. Rust stays a thin jj-lib binding. Python owns coercion and ergonomics. No
   `jj_lib` or `gix` type crosses the FFI. `_pyjutsu.pyi` stays synchronized
   with every native surface change.

Re-read every jj-lib line number in the plan from the pinned 0.44.0 source
before you use it. Line numbers move.

## Open decision — resolve it, do not guess

**C3 prefix scoping.** `IdPrefixContext` is scoped by a revset. jj-cli reads
`revsets.short-prefixes`, which Pyjutsu does not vendor. Either disambiguate
across the whole repository (no configuration surface, always correct, slower)
or within a configured revset defaulting to `visible()` (closer to the CLI,
needs a new vendored key). The plan recommends the first. Record your choice
and its reason in the docstring and in the project log.

## The gate

Every lane runs the full gate before it lands. No lane lands on a partial gate.

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
ruff check python tests scripts
pytest -q
devenv tasks run pyjutsu:verify
```

Run focused tests after each slice. Run the full gate at the end of each lane.
`devenv tasks run` suppresses inner stdout; run a task's exec line directly
inside `devenv shell` when you need pass or fail detail.

Every lane's oracle test compares against the pinned `jj` CLI, version 0.44.0.

## Delivery

Land each lane separately. Never commit on a red gate.

1. Run the full gate. When it is green, commit the lane and push to `origin`.
2. Inspect the diff and the status output before each commit.
3. Append a dated entry to
   `.loci/projects/003-pyjutsu-jj-read-surface/project.md` for each lane: what
   changed, the validation block, and every decision made.
4. Document every new verb in `docs/USER_GUIDE.md`.

## Non-goals

- Do not implement Phase D or create `ws.git`.
- Do not enable the gix `revision`, `blame`, `status`, `dirwalk`, or network
  features.
- Do not add `jj-cli` as a runtime dependency.
- Do not change the public `Revset` builder surface.
- Do not materialize every conflict during `conflicts()`. Content reads stay
  lazy and explicit.
