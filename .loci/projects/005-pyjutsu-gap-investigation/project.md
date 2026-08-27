---
title: Pyjutsu gap investigation
type: project
status: active
loci:
  schema: 1
  id: 01a0409f-e275-7156-be32-1cb7bd9a6028
  projects: []
---

# PROJECT: Pyjutsu gap investigation

Map everything Pyjutsu does not do, rank it, cost it, and decide each item:
**bind**, **reject**, or **defer**.

This is an investigation. It lands documents, not bindings. It closes the C9
backlog, the D-reject loose end, and the three open questions in
[[prompt.md]].

Read [[.loci/projects/002-pyjutsu-refactor-jj044/IMPLEMENTATION_PLAN.md]] for
the lane format, [[.loci/projects/002-pyjutsu-refactor-jj044/LIBRARY_DESIGN_REVIEW.md]]
for the tiering method, and
[[.loci/projects/002-pyjutsu-refactor-jj044/COLOCATED_GIT_SURFACE.md]] for the
depth rule.

## Deliverables

| File | Holds |
|---|---|
| [[GAP_REPORT.md]] | every item in all six groups, tiered, with a verdict |
| [[PERFORMANCE.md]] | the method, the repositories, the numbers, the conclusion |
| [[IMPLEMENTATION_PLAN.md]] | lanes for the **bind** items only, sequenced |

## Method

- Read the pinned jj-lib 0.44.0 source for every jj-lib claim. The registry
  holds 0.42.0 and 0.44.0 side by side; 0.44.0 is the pin.
- Use the pinned `jj` 0.44.0 binary as the oracle for anything jj-cli owns.
  jj-cli is not published to crates.io.
- Preserve raw output under `artifacts/<UTC-timestamp>-<topic>/`. That
  directory is git-ignored.

## Implementation log

### 2026-08-26 — baseline

Pyjutsu 0.19.0 on `main` at `5c5f9ed9` (`fff168fa` plus the kickoff commit),
pushed to `origin`. jj-lib 0.44.0, gix 0.85.0, one resolved gix version. The
working copy sits directly on `main` with no changes.

Observed baseline, matching the kickoff's claim exactly:

```text
cargo fmt --check                         PASS  exit 0
cargo clippy --all-targets -- -D warnings PASS  exit 0
cargo test                                PASS  7 passed, 0 failed
ruff check python tests scripts           PASS  exit 0
pytest -q                                 PASS  544 collected, exit 0
devenv tasks run pyjutsu:verify           PASS  exit 0
```

Evidence: `artifacts/20260826T235734Z-baseline-full/gate.txt`.

### 2026-08-26 — the jj-lib and jj-cli evidence sweep

Re-read every entry point the kickoff cites against the pinned jj-lib 0.44.0
source. **All Group 1 line numbers are exact**, and every module is public in
`lib.rs`: `graph.rs:33/81/95/133`, `annotate.rs:60/154/290`,
`trailer.rs:60/79`, `bisect.rs:48/75/86/99`, `fsmonitor.rs`.

Confirmed by grep over `jj-lib-0.44.0/src`: no `revert`, no `backout`, no
`describe` helper. **But `sign` is not in that class** — `CommitBuilder::
set_sign_behavior` (`commit_builder.rs:148`, `:382`) and `set_sign_key`
(`:153`, `:387`) are the whole primitive. `jj sign` is a rewrite with
`SignBehavior::Force`, `jj unsign` one with `Drop`. The kickoff's "jj-lib has
no `sign` helper" is true of the *name* and misleading about the *cost*.

Captured from the pinned binary: `jj --help`, per-verb help for all 12 Group 3
verbs plus `revert`/`sign`/`unsign`/`redo`/`run`/`gerrit`/`resolve`,
`jj git push --help`, `jj git fetch --help`, and
`jj config list --include-defaults` for `revsets`, `templates`, and `ui`.

Two facts that decide Group 4 items:

- **`jj git push` has no `--force` flag at all** in 0.44. It has `-r`, `-c`,
  `--named`, `--tag`, `--deleted`, `--dry-run`, `-o`, and three `--allow-*`
  flags. jj push is force-with-lease by construction (`GitRefUpdate.targets`
  carries the expected remote position).
- **`jj git fetch --tag <PATTERN>` exists**, and Pyjutsu already builds the
  struct that carries it: `src/workspace.rs:1546` sets
  `GitFetchRefExpression { bookmark, tag: StringExpression::none() }`.

Evidence: `artifacts/20260826T235825Z-jj-cli-oracle/`.

### 2026-08-26 — the performance spike, and the measurement error it found

Built two synthetic repositories with `git fast-import`, then adopted each with
the pinned `jj git init --colocate`: 100,000 commits with 2,000 tags, and
10,000 commits in two variants (2,000 tags and none) to separate repository
size from reference count.

The first run reproduced the kickoff's picture and looked alarming:
`view.log("::@")` over 100k commits took **34.1 s**, against **1.2 s** for the
whole `jj log` process. Then the cause turned out to be the instrument, not the
library.

**`devenv tasks run pyjutsu:build` runs `maturin develop --uv` with no
`--release`.** Every local measurement — including the kickoff's 66 ms data
point — times an unoptimized Rust extension against a release `jj` binary. A
release build of the same code is 4.6× to 7.7× faster. Re-measured everything
against `maturin develop --release --uv`.

The conclusion inverts. On this repository (147 commits) release Pyjutsu reads
`::@` in **6.7 ms** against `jj log`'s **25 ms** — Pyjutsu is 3.7× *faster*,
not 2.3× slower. At 100k commits the full modelled read costs 74 µs per commit
against the CLI's 20.5 µs for a richer template.

A throwaway Rust spike (a `spike_log_phases` method on `PyRepoView`, added,
measured, and removed — `jj diff --stat` reports 0 files changed) split the
per-commit cost. **Pydantic validation is the largest single component at 45%**,
the candidate the kickoff listed last. Both named suspects are cleared:
`disambiguate_prefix_with_refs` costs 2.4 µs per commit for both ids together,
and the reference count changes nothing measurable.

One real defect surfaced: **`RepoView.log(revset, limit)` evaluates the whole
revset and loads every commit before it truncates** (`src/repo_view.rs:99`).
`log("::@", 1)` costs 775 ms on the 100k repository, release build;
`iter_log("::@", 1)` costs 7.6 ms for the same answer — 102×. The docstring at `src/repo_view.rs:82` says `limit`
"bounds the work too", which is true only of the `CommitData` build.

Full method, tables, and the disproved hypotheses are in [[PERFORMANCE.md]].
Evidence: `artifacts/20260827T001238Z-perf-100k/`,
`artifacts/20260827T001722Z-perf-attribution/`.

### 2026-08-26 — the rule for reproducing jj-cli policy

Open question 1, answered in [[GAP_REPORT.md]] §0. The discriminator is not
"how much policy" but **whether the pinned binary can be queried for it**:

1. Bind the primitive and push the policy to the caller when the caller can
   supply it. No re-verification entry.
2. Vendor a policy only when one `jj config list --include-defaults` command
   prints it and a test asserts the vendored copy equals that output.
3. Refuse to vendor a policy that exists only as prose or as a rendered
   template. Nothing can check it, so an upgrade diverges silently.

Applied: `revert`, `sign`/`unsign`, and `git describe` all land under rule 1
and add **no** entries. The list stays at five. `fix.tools` (C7) remains the
one rule-3 exception, taken because `fix` is meaningless without a tool schema.

### 2026-08-26 — documentation audit

Audited `docs/USER_GUIDE.md`, `docs/PYJUTSU_CONCEPT.md`, and
`docs/DEV_GUIDE.md` against the 0.19.0 surface. Thirteen drifts, none fixed here
— [[IMPLEMENTATION_PLAN.md]] schedules them as one lane.

The largest: **five public verbs are documented nowhere** — `view.try_merge`,
`ws.tracked_ignored_paths`, `ws.is_ancestor`, `ws.git_default_branch`, and the
`MergeResult` model.

### 2026-08-26 — verdicts and close

37 items across six groups: **12 bind, 16 reject, 9 defer**. The twelve bind
rows are eleven distinct pieces of work, and [[IMPLEMENTATION_PLAN.md]] carries
eleven lanes. Full reasoning in [[GAP_REPORT.md]].

Nothing in the report reopens a standing rejection. Two of the D-reject table's
entries are *acted on* rather than reopened: "blame via gix — bind jj's
`annotate`" becomes lane E3, and "`git describe` — implement it as a revset
over jj's tag view, schedule it in project 003" becomes lane E8. Project 003
closed without the second one; this plan schedules it.

Closing gate, on a tree with no spike code left and the normal debug extension
restored (the measurement work left a release build installed):

```text
maturin develop --uv                      restored the debug extension
cargo fmt --check                         PASS  exit 0
cargo clippy --all-targets -- -D warnings PASS  exit 0
cargo test                                PASS  7 passed, 0 failed
ruff check python tests scripts           PASS  exit 0
pytest -q                                 PASS  exit 0
devenv tasks run pyjutsu:verify           PASS  exit 0
cargo tree -i gix                         one version (0.85.0)
jj diff --stat                            4 files, all of them this project's
```

Evidence: `artifacts/20260827T002834Z-final-gate/gate.txt` (spike removed,
release extension still installed) and
`artifacts/20260827T004208Z-final-gate-restored/gate.txt` (the state this
project ends in).

The benchmark scripts and the `git fast-import` generator are preserved under
`artifacts/20260827T001722Z-perf-attribution/scripts/`, so [[PERFORMANCE.md]]'s
numbers are reproducible without rebuilding the method.
