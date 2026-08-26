# Investigation prompt — the Pyjutsu gap audit

Work in the Pyjutsu repository. This is an **investigation**, not an implementation. It ends in
reports and a plan, not in bound verbs.

Pyjutsu 0.19.0 is on `main` at `fff168fa`, pushed to `origin`. It binds jj-lib 0.44.0 and gix
0.85.0, with a green gate: 7 Rust tests and 544 Python tests. Projects 002, 003, and 004 closed
the mutation surface, the jj read surface, and the colocated git namespace. What remains is
unmapped: a scattered set of known gaps that no one has costed, ranked, or checked against each
other.

## Read first, completely, in this order

1. `.loci/projects/002-pyjutsu-refactor-jj044/IMPLEMENTATION_PLAN.md` — the lane format this
   investigation must produce, and the C9 backlog and D-reject table it must resolve.
2. `.loci/projects/002-pyjutsu-refactor-jj044/LIBRARY_DESIGN_REVIEW.md` — the tiering method that
   produced Phase C. Reuse it; do not invent a second one.
3. `.loci/projects/002-pyjutsu-refactor-jj044/COLOCATED_GIT_SURFACE.md` — the "depth, not call
   count" argument, and the four rejected candidates.
4. `.loci/projects/003-pyjutsu-jj-read-surface/project.md` and
   `.loci/projects/004-pyjutsu-ws-git-namespace/project.md` — every decision Phases C and D
   recorded. Read both implementation logs in full. Several entries answer questions below.
5. `docs/PYJUTSU_CONCEPT.md`, `docs/USER_GUIDE.md`, `docs/DEV_GUIDE.md`.
6. `AGENTS.md` and the full `.agents/skills/my-ai/SKILL.md`.

## Objective

Produce one ranked, costed map of everything Pyjutsu does not do, and a lane plan for the part
worth doing next. The deliverable is a decision, not a survey: for every item, **bind, reject, or
defer**, each with its reason.

## The inventory to work from

Every entry below was verified against the pinned sources on 2026-08-26. Re-read every line number
before you use it; line numbers move.

### Group 1 — jj-lib reads that exist and are not bound

This is the C9 backlog. All five entry points are present in jj-lib 0.44.0.

| Item | Entry points |
|---|---|
| Graph edges for `log` | `graph.rs:33` `GraphEdge`, `:81` `GraphEdgeType`, `:95` `reverse_graph`, `:133` `TopoGroupedGraph` |
| Blame / annotate | `annotate.rs:60` `FileAnnotation`, `:154` `FileAnnotator`, `:290` `LineOrigin` |
| Description trailers | `trailer.rs:60` `parse_description_trailers`, `:79` `parse_trailers` |
| Bisect | `bisect.rs:75` `Bisector`, `:48` `Evaluation`, `:86` `BisectionResult`, `:99` `NextStep` |
| Watchman / fsmonitor | `fsmonitor.rs` |

Two of these are cheaper than the backlog implies, and the report must say so:

- **Blame.** Lane C7 already drives `FileAnnotator` inside `src/fix.rs` — the absorb path uses it
  to attribute lines. The machinery is linked and exercised; only the public verb is missing.
- **Trailers.** `parse_description_trailers` is a pure function over a string. It needs no repo,
  no transaction, and no revset.

Rank these against each other on caller value, not on implementation cost alone.

### Group 2 — jj-cli compositions with no jj-lib entry point

jj-lib has no `revert`, `backout`, or `sign` helper — verified by grep over `jj-lib-0.44.0/src`.
Each of these is assembled by jj-cli from lower-level primitives.

- **`jj revert`.** jj 0.44 renamed `backout` to `revert`; `jj revert --help` reads "Apply the
  reverse of the given revision(s)". Lane C5 deferred it under the old name for exactly this
  reason. Its description is templated (`templates.revert_description`), which is another piece
  of jj-cli policy.
- **`jj sign` / `jj unsign`.** Lane C8 bound signing *configuration* (`Workspace.load(
  sign_behavior=…)`) and *verification* (`RepoView.verify`). It did not bind the verb that
  re-signs an existing commit. A repository that requires signed commits can now be served by
  Pyjutsu, but a commit signed after the fact cannot.
- **`git describe`.** The D-reject table rejected the gix implementation and said: implement it as
  a revset over jj's tag view — nearest tagged ancestor — and "schedule it in project 003, not
  here". Project 003 closed without it. This is a loose end from the plan, not a new idea.

**The governing question for this whole group.** Lane C7 *did* reproduce a jj-cli composition:
`src/fix.rs` vendors jj's `fix.tools` schema and the `revsets.fix` default, and that cost two new
entries on the per-upgrade re-verification list. That list now has five entries. Decide and record
a **rule** for when reproducing jj-cli policy is worth a re-verification entry, then apply it to
each item above. Do not decide them one at a time by feel.

### Group 3 — CLI verbs with no Pyjutsu equivalent

`jj --help` on the pinned 0.44.0 binary lists these verbs, which have no Pyjutsu surface:

```text
arrange   commit   diffedit   interdiff   metaedit   next   parallelize
prev      show     simplify-parents       sparse     status
```

Triage each into one of three buckets, with evidence:

1. **jj-lib-backed** — a real gap; find the entry point.
2. **jj-cli ergonomics** — a composition over verbs Pyjutsu already has. Say which ones, and show
   the composition. `status` and `show` are likely candidates (`working_copy()` + `diff()`).
3. **Out of model** — needs something Pyjutsu deliberately does not have.

This list is mechanical, not exhaustive of intent: check it against
`LIBRARY_DESIGN_REVIEW.md`'s tiering before ranking anything.

### Group 4 — documented non-goals to re-examine

`docs/USER_GUIDE.md` §13 and `docs/PYJUTSU_CONCEPT.md` §12 record these as deliberate exclusions:
a native async facade, word/inline diff, interactive selection beyond `split`'s hunk carve, and
assorted git refinements (force-push flags, `--change`/`-r` push selection, tag fetch).

Re-examine each **only** if something has changed since it was decided — a new jj-lib API, a real
caller need, or a wrong premise. Confirming an exclusion is a valid and useful outcome. Do not
reopen a decision to relitigate it.

### Group 5 — documentation drift

The docs have fallen behind the surface, and one instance was already found and fixed:
`docs/USER_GUIDE.md` §13 listed two-revset `diff(from, to)` as out of scope, while
`view.diff(rev, to=…)` had shipped long before.

One instance is known and **not** fixed: `docs/PYJUTSU_CONCEPT.md` §12's "Later" list still names
the revset builder, full diffs and hunks, and the streaming/iterator log as future work. All three
have shipped.

Audit all three documents against the 0.19.0 surface. Every claim of the form "Pyjutsu does not do
X" is a testable assertion — treat it as one. Report each drift; do not fix them piecemeal during
the audit.

### Group 6 — non-functional gaps

- **Performance is unmeasured at scale.** One data point exists, taken on this repository
  (145 commits reachable from `@`): `view.log("::@")` takes **66 ms** in-process against **29 ms**
  for the entire `jj log` process rendering the same two short ids. Marginal cost was flat at
  ~0.39 ms per commit from 50 to 145 commits. Nobody has profiled a repository with 100k commits
  or thousands of refs.

  Measure before attributing. Candidate costs, none of them confirmed: the per-commit
  `short_commit_id` / `short_change_id` computation (`disambiguate_prefix_with_refs` scans the
  view's bookmark and tag names on every call), `is_empty` touching the backend per commit,
  `fresh_loader` re-opening the store on every `ws.git.*` verb, and Pydantic validation of one
  model per commit.

  One hypothesis is already **disproved** — do not re-raise it: `IdPrefixContext::populate` was
  suspected of walking the whole index per commit. It does not. Without `disambiguate_within` it
  returns an empty index that falls back to the repo's own, so it is cheap. C3's whole-repository
  decision is not a quadratic cost.

  If a real cost is found, the fix probably belongs in the plan as a lane. If the numbers are
  fine, say so plainly and close the question.

- **The deprecation aliases have no removal version.** Five paths warn today: `ws.git_refs`,
  `ws.write_git_ref`, `ws.delete_git_ref`, `ws.remotes` (moved by D1), and
  `ws.create_tag(message=…)` (from A3). Propose a removal release and a policy.

- **There is no CHANGELOG.** Release notes accrete as `README.md` sections. Propose a format, or
  argue for keeping it as it is.

## Open questions — resolve with evidence, do not guess

1. **What is the rule for reproducing jj-cli policy?** C7 did it and paid two re-verification
   entries. The list is at five. State the rule, then apply it to `revert`, `sign`, and
   `git describe`.
2. **Is the read path fast enough?** Measure a large repository. Name the dominant cost or clear
   the question.
3. **What is Pyjutsu's completeness claim?** The README says the surface is "implemented and
   differential-tested". After this audit, what is the honest one-sentence statement of what
   Pyjutsu does and does not cover? That sentence goes in the README.

## Method

- **Read the pinned source, not the documentation, for every jj-lib claim.** The registry has
  jj-lib 0.42.0 and 0.44.0 side by side; 0.44.0 is the pin.
- **The pinned binary is a stronger oracle than upstream source for anything jj-cli owns.**
  jj-cli is not published to crates.io. Use `jj <command> --help` and
  `jj config list --include-defaults <section>`.
- **A spike is allowed; a lane is not.** Write throwaway code to answer a cost question, then
  throw it away. Do not land a binding from this project.
- **Everything runs through `devenv shell -- …`.** The Rust toolchain, `maturin`, `mold`, and the
  pinned `jj` are not on the bare PATH.
- Preserve raw output for each measurement under
  `.loci/projects/005-pyjutsu-gap-investigation/artifacts/<UTC-timestamp>-<topic>/`. That
  directory is git-ignored.

## Deliverables

Four files under `.loci/projects/005-pyjutsu-gap-investigation/`:

1. **`project.md`** — opened in the house format, with an implementation log.
2. **`GAP_REPORT.md`** — every item from all six groups, each with: what it is, the jj-lib or gix
   entry point (or the finding that none exists), the caller need it serves, a size, a risk, and a
   verdict of **bind / reject / defer** with its reason. Match
   `LIBRARY_DESIGN_REVIEW.md`'s tiering so the two read as one series.
3. **`PERFORMANCE.md`** — the method, the repository profiled, the numbers, and the conclusion.
   Include the disproved `IdPrefixContext` hypothesis so it is not raised again.
4. **`IMPLEMENTATION_PLAN.md`** — lanes for the **bind** items only, in the exact format of
   project 002's plan: goal, size, risk, blocks, entry points, surface, steps, tests, acceptance.
   Sequence them. If the honest answer is that nothing is worth binding next, say that and write
   no lanes.

Do not open projects 006+. Sequencing the work is this project's job; scheduling it is the user's.

## The gate

This project lands documents, so the gate is lighter — but any spike that touches the tree must
leave it green before you commit, and the working tree must be clean of spike code at the end:

```bash
devenv shell -- bash -c '
  cargo fmt --check
  cargo clippy --all-targets -- -D warnings
  cargo test
  "$DEVENV_STATE/venv/bin/ruff" check python tests scripts
  "$DEVENV_STATE/venv/bin/pytest" -q
  devenv tasks run pyjutsu:verify
'
```

Baseline to reproduce before the first edit: 7 Rust tests, 544 Python tests, exit 0. Report what
you actually observe.

## Non-goals

- Do not bind anything. No new verb, no new native method, no new model.
- Do not reopen a rejection without new evidence. The D-reject table's four entries (network via
  gix, blame via gix, `git status` / dirwalk, mailmap) and Phase C's and D's recorded decisions
  stand unless a fact has changed.
- Do not add CI, a PyPI publish step, or a Python-version change. All three are decided against.
- Do not change the public surface, including to "clean it up".
- Do not fix the documentation drift you find. Report it; the plan schedules it.

## Environment facts that cost time to rediscover

- `cargo check` passing does not mean the extension rebuilt. Pytest imports the installed `.so`.
  After any Rust change run
  `VIRTUAL_ENV="$DEVENV_STATE/venv" UV_PROJECT_ENVIRONMENT="$DEVENV_STATE/venv" maturin develop --uv`
  before pytest, or you will debug a stale artifact.
- `gitman` is not installed despite the skill and the `.gitman/` directory. Use raw `jj`.
- jj snapshots the working copy into `@` on almost every command. After you describe a commit, run
  `jj new` immediately, before the next edit.
- Do not create a bookmark named `lane/sub` while `lane` exists. Git cannot hold both.
- `devenv tasks run` suppresses task stdout. Run a task's exec line directly inside `devenv shell`
  when you need pass or fail detail, or check the exit code.
- `Workspace.init` requires the target directory to exist; it does not create it.
