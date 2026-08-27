---
title: Gap report — everything Pyjutsu does not do
type: report
status: draft
project: 005-pyjutsu-gap-investigation
---

# Gap report

Thirty-seven items. Each one gets an entry point (or the finding that none
exists), the caller need it serves, a size, a risk, and one verdict:
**bind**, **reject**, or **defer**.

Tiering follows [[.loci/projects/002-pyjutsu-refactor-jj044/LIBRARY_DESIGN_REVIEW.md]],
so this report and that one read as one series:

- **Tier 1** — a hole that forces a caller out of the library.
- **Tier 2** — a real jj verb with no binding.
- **Tier 3** — worth having, lower urgency.

Verdicts mean:

- **bind** — schedule a lane now. [[IMPLEMENTATION_PLAN.md]] carries it.
- **defer** — real, but no caller need is proven and nothing blocks. Recorded
  with its size so a future project can pick it up without re-investigating.
- **reject** — decided against, with the reason. Not a backlog entry.

Every jj-lib line number below was re-read against the pinned 0.44.0 source.
Every jj-cli claim comes from the pinned 0.44.0 binary.

## Verdict summary

| Group | Items | Bind | Reject | Defer |
|---|---|---|---|---|
| 1 — jj-lib reads not bound (the C9 backlog) | 5 | 2 | 0 | 3 |
| 2 — jj-cli compositions with no jj-lib entry | 3 | 3 | 0 | 0 |
| 3 — CLI verbs with no Pyjutsu equivalent | 15 | 1 | 9 | 5 |
| 4 — documented non-goals re-examined | 6 | 1 | 5 | 0 |
| 5 — documentation drift (13 findings) | 1 | 1 | 0 | 0 |
| 6 — non-functional | 7 | 4 | 2 | 1 |
| **Total** | **37** | **12** | **16** | **9** |

Group 4 counts six rows because `USER_GUIDE.md` §13's "assorted git/rewrite
refinements" bullet names three unlike things (§4.4). Group 6 counts the
read-path question as three rows — two fixes, one rejected redesign — plus the
deferred `ws.git.*` cost, the deprecation policy, the CHANGELOG question, and
the completeness claim.

The twelve **bind** rows are eleven distinct pieces of work: `sign` and
`unsign` share a lane, and the completeness sentence rides with the
documentation lane. [[IMPLEMENTATION_PLAN.md]] carries eleven lanes.

---

# 0. The rule for reproducing jj-cli policy

Open question 1. C7 reproduced jj-cli policy and paid two re-verification
entries. The list is at five. What makes the next one worth it?

## The wrong discriminator

"How much policy is it" does not separate the five entries. Two of them are a
single string each; one is a whole schema. Size is not the risk.

## The right one: can the pinned binary be queried for it?

An entry on the re-verification list costs one thing — **somebody must re-check
it by hand at every jj upgrade, and if they forget, Pyjutsu diverges from jj
silently.** That cost disappears entirely when a test can do the checking. So
the rule keys on testability:

> **Rule 1 — push the policy to the caller when the caller can supply it.**
> Bind the jj-lib primitive with the policy as a required argument. Do not
> vendor jj's default. Costs no entry.
>
> **Rule 2 — vendor a policy only when one `jj config list --include-defaults`
> command prints it, and a test asserts the vendored copy equals that output.**
> The entry is machine-checked: an upgrade fails loudly instead of drifting.
>
> **Rule 3 — refuse to vendor a policy that exists only as prose or as a
> rendered template.** No command prints it, so no test can check it. This is
> the expensive kind, and it is the only kind that should ever be argued about.

## The list, re-read under the rule

| Entry | Kind | Checkable by | Rule |
|---|---|---|---|
| `src/config/revsets.toml` | data table | `jj config list --include-defaults revset-aliases`, asserted by `tests/test_revset_config.py` | 2 |
| `git.object-hash` key, values, default | key + two values | `jj config list --include-defaults git` | 2 |
| `revsets.fix` default | one string | `jj config list --include-defaults revsets` | 2 |
| `signing.behavior` names | enum serde names | jj-lib owns the enum; a drift is a deserialization error | 2 |
| **`fix.tools` schema** | **structure, from prose** | **nothing — `jj help -k config` is documentation** | **3** |

Four of five are machine-checked. **One is not**, and C7 took it deliberately:
`fix` cannot run without a tool schema, and inventing a second format was
forbidden by the plan. That is the exception the rule is built around, not a
precedent.

## Applied to this project's three candidates

| Candidate | The policy | Rule | Cost |
|---|---|---|---|
| `jj revert` | `templates.revert_description` — a **template**, needing jj's template engine | 3 → refuse; make `description` a caller argument (rule 1) | **no entry** |
| `jj sign` / `unsign` | `revsets.sign` default | 1 → make the revset required | **no entry** |
| `git describe` | none — it is a revset over jj's tag view | n/a | **no entry** |

**The re-verification list stays at five.** All three bind without touching it.
That is not a coincidence: rule 1 covers every case where jj-cli's policy is
"which revisions" or "what text", which is most of them.

---

# 1. Group 1 — jj-lib reads that exist and are not bound

The C9 backlog. All five entry points verified present in jj-lib 0.44.0, and
every module is `pub` in `lib.rs`.

## 1.1 Description trailers — **BIND** (Tier 2)

**Entry points.** `trailer.rs:60` `parse_description_trailers`, `:79`
`parse_trailers`, `:23` `Trailer { key, value }`.

**Caller need.** gitman parses `Change-Id` and `Signed-off-by` out of commit
descriptions by hand today. Every consumer that reads structured metadata out
of a description reimplements the same parser, and gets the continuation-line
and blank-line rules wrong.

**Size** XS. **Risk** low.

`parse_description_trailers` is a pure function over a `&str`. It needs no
repo, no transaction, no revset, and no GIL release. It is the cheapest item in
this whole report — a free function beside `escape_string` (lane A2's shape)
plus a Python-side accessor on `Commit`.

**Verdict: bind.** Lowest cost in the report, and it retires hand-rolled
parsing in the one consumer Pyjutsu exists to serve.

## 1.2 Blame / annotate — **BIND** (Tier 2)

**Entry points.** `annotate.rs:154` `FileAnnotator`, `:165` `from_commit`,
`:219` `compute(repo, domain: &Arc<ResolvedRevsetExpression>)`, `:233`
`to_annotation`, `:60` `FileAnnotation` with `:87` `lines`, `:102`
`line_ranges`, `:119` `compact_line_ranges`, `:290` `LineOrigin`.

**Caller need.** `jj file annotate` has no Python route. Attributing a line to
the change that introduced it is the read behind every "who broke this" tool.

**Correcting the kickoff.** It says lane C7 drives `FileAnnotator` in
`src/fix.rs`. It does not — `src/fix.rs` uses `jj_lib::fix::
compute_changed_ranges` (`src/fix.rs:224`). The annotator is driven by lane
**C6**: `src/transaction.rs:719` calls `jj_lib::absorb::split_hunks_to_trees`,
which constructs `FileAnnotator::with_file_content` at `absorb.rs:158`.

**The substance holds, and is stronger than stated.** The annotate machinery is
linked, exercised by Pyjutsu's own absorb tests, and C6 already added the one
helper the public API needs: `revset::resolve_expression` produces exactly the
`Arc<ResolvedRevsetExpression>` that `FileAnnotator::compute` takes as its
`domain`. Only the public verb is missing.

**Size** S. **Risk** low. **Oracle** `jj file annotate`.

**This also closes a D-reject entry.** "Blame via gix — No. jj-lib has
`annotate`. Bind jj's." Binding it is the action that table called for.

**Verdict: bind.**

## 1.3 Graph edges for `log` — **DEFER** (Tier 3)

**Entry points.** `graph.rs:33` `GraphEdge`, `:81` `GraphEdgeType`, `:95`
`reverse_graph`, `:133` `TopoGroupedGraph`, `:30` `GraphNode`.

**Caller need.** `log()` returns a flat list. Any caller rendering a graph must
recompute topology from `parent_ids`, and will get the elided "indirect" edges
wrong, because those need the index.

**Size** M. **Risk** medium.

**Why defer, not bind.** The types are generic over the node type and the
`TopoGroupedGraph` is a stateful iterator; a faithful binding must decide how
to express `GraphEdgeType::{Direct, Indirect, Missing}` across the FFI and
whether to return edges beside `log()` or as a separate call. That is a design
decision with no caller pressing for it. Pyjutsu is a library, not a renderer,
and no consumer has asked for graph layout.

**Reopen when** a consumer needs to render a graph. The entry points are here.

## 1.4 Bisect — **DEFER** (Tier 3)

**Entry points.** `bisect.rs:75` `Bisector`, `:109` `new` (async), `:48`
`Evaluation`, `:86` `BisectionResult`, `:99` `NextStep`, `:212` `next_step`
(async), `:130`–`:167` the `mark_*` methods.

**Caller need.** Narrow. `jj bisect` in 0.44 ships **only** `jj bisect run`,
which runs a command across candidates — the pinned binary has no interactive
`good`/`bad` subcommand. So the CLI oracle covers one shape of use.

**Size** M. **Risk** low.

**Why defer.** `Bisector` is stateful and async, and it holds `&'repo`, so the
handle cannot outlive its view — a real FFI lifetime design problem for a verb
nobody has requested. `run_jj(["bisect", "run", ...])` covers the one shape the
CLI exposes today.

**Reopen when** a caller wants programmatic marking, which is the half the CLI
does not offer and where a library beats a subprocess.

## 1.5 Watchman / fsmonitor — **DEFER** (Tier 3)

**Entry points.** `fsmonitor.rs:39` `FsmonitorSettings`, `:59`
`from_settings`, `:85` `pub mod watchman`, `:169` `Fsmonitor`, `:180` `init`
(async), `:218` `query_changed_files` (async).

**Caller need.** Snapshot cost on very large working copies.

**Size** M. **Risk** medium — needs an external `watchman` daemon.

**Why defer.** `FsmonitorSettings::from_settings` reads jj's own configuration,
so a workspace with watchman configured **may already benefit** through
jj-lib's snapshot path with no Pyjutsu code at all. Nothing measured it. Adding
a Pyjutsu-side surface before confirming the pass-through works would bind an
API to an unknown.

**Reopen when** somebody measures snapshot cost on a large working copy with
and without a watchman daemon. That measurement is cheap and is the actual
open question; this project profiled the read path, not the snapshot path.

## Group 1, ranked on caller value

The kickoff asks for a ranking on caller value, not implementation cost. Both
orderings agree at the top, which is unusual and worth stating:

1. **Trailers** — highest value per unit cost. A consumer parses them by hand
   today, and the binding is a pure function.
2. **Blame** — highest absolute value. It is a read no caller can perform, the
   machinery is already linked, and it closes a D-reject entry.
3. **Graph edges** — real value, but only to a renderer, and Pyjutsu has no
   renderer among its consumers.
4. **Watchman** — value is unmeasured and may already be delivered for free.
5. **Bisect** — narrowest audience, and the CLI covers the shape it ships.

---

# 2. Group 2 — jj-cli compositions with no jj-lib entry point

Confirmed by grep over `jj-lib-0.44.0/src`: no `revert`, no `back_out`, no
`backout`, no `describe` helper.

## 2.1 `jj sign` / `jj unsign` — **BIND** (Tier 2)

**The kickoff understates this one.** It groups `sign` with `revert` as a
composition with no jj-lib entry point. There is no function *named* `sign`,
but the primitive is one setter:

```text
commit_builder.rs:148, :382   CommitBuilder::set_sign_behavior(SignBehavior)
commit_builder.rs:153, :387   CommitBuilder::set_sign_key(String)
signing.rs:147                SignBehavior { Drop, Keep, Own, Force }
```

`jj sign -r X` is a rewrite of X with `SignBehavior::Force`; `jj unsign -r X`
is the same rewrite with `Drop`. The pinned binary's help confirms the
semantics: "Note that revisions are always re-signed."

**Caller need.** Lane C8 bound signing *configuration*
(`Workspace.load(sign_behavior=…)`) and *verification* (`RepoView.verify`). A
repository that requires signed commits is now served. What is missing is
signing a commit **after the fact** — the case where a commit arrived
unsigned, from a fetch or from a tool that did not sign, and policy requires it
signed before the push.

**Policy.** `revsets.sign` default `reachable(@, mutable())`. Under rule 1, make
the revset required. No re-verification entry.

**Size** S. **Risk** low. C8 already wired `SignSettings` and the `Signer`
through `UserSettings`; `PyTransaction` already holds `settings: Arc<UserSettings>`
(added by C7). The rewrite path is `tx.describe`'s shape with a different
builder call.

**Verdict: bind.** Smallest of the three, completes a capability C8 left half
finished, and `unsign` is the same lane for two extra lines.

## 2.2 `jj revert` — **BIND** (Tier 2)

**Renamed, not new.** jj 0.44 renamed `backout` to `revert`. Lane C5 deferred
it under the old name; C5's log records the reason ("backout is a jj-cli
composition over lower-level primitives and has no listed jj-lib entry point").
That reason still holds, and the rule in §0 now resolves it.

**The composition.** `jj revert --help`: "The reverse of each of the given
revisions is applied sequentially in reverse topological order at the given
location." The primitives:

```text
rewrite.rs:133   restore_tree(source, destination, labels, matcher)
rewrite.rs:451   rebase_to_dest_parent(repo, sources, destination)
rewrite.rs:59    merge_commit_trees(repo, commits)
```

Reverting C onto D is `restore_tree` with source and destination swapped
relative to `tx.restore`, written as a new child of D. Pyjutsu already binds
`tx.restore`, so the tree half is a known quantity.

**The policy, and how rule 1 disposes of it.**
`templates.revert_description` is a **template**:

```text
'Revert "' ++ description.first_line() ++ '"' ++ "\n",
"This reverts commit " ++ commit_id ++ ".\n",
```

Rendering it needs jj's template engine, which Pyjutsu deliberately does not
have. Under rule 3 it must not be vendored. Under rule 1, `description` becomes
a caller argument. A caller that wants git's wording writes it; the two lines
above are in this report if they want jj's.

**Caller need.** Undoing a landed change without rewriting it is the one
history operation Pyjutsu cannot express. `tx.abandon` rewrites; `ws.undo`
works on operations, not commits. A land-and-push library that cannot revert a
bad landing has a hole where the recovery path should be.

**Size** M. **Risk** medium — the insertion modes (`--onto`, `--insert-after`,
`--insert-before`) each change the graph, and reverse topological order over a
multi-revision selection is the same trap lane C5 hit with `duplicate`.

**Verdict: bind.**

## 2.3 `git describe` — **BIND** (Tier 3)

**A loose end, not a new idea.** The D-reject table rejected the gix
implementation and wrote the replacement itself: "Implement it as a revset over
jj's tag view — nearest tagged ancestor. That is exact, pure jj-lib, and works
on non-colocated repos too. Schedule it in project 003, not here." Project 003
closed without it.

**Entry points.** No jj-lib helper — and none is needed. The whole
implementation is a revset over machinery Pyjutsu already binds:
`heads(::rev & tags())` gives the nearest tagged ancestor, and the distance is
`(tag::rev).count()` minus one. Both `view.log` and the tag view are bound
today.

**Caller need.** Version derivation. Every release tool wants "the nearest tag,
plus how far past it, plus the short id". Callers shell out to `git describe`
for it, which fails on a non-colocated repository.

**Size** S. **Risk** low. **Oracle** `git describe --tags --long` on a
colocated repository, with a documented divergence: jj's tag view, not git's
refs, is the source, so a git tag that jj has not imported is invisible. That
is the correct behaviour for a jj binding and must be stated, not hidden.

**Feature-flag note.** This does not enable gix's `revision` feature. The
recommendation in `COLOCATED_GIT_SURFACE.md` §3 — "`revision` is the only
feature-flag decision on the table, and the recommendation is not to take it"
— stands untouched.

**Verdict: bind.**

---

# 3. Group 3 — CLI verbs with no Pyjutsu equivalent

The kickoff lists twelve. The pinned `jj --help` shows three more with no
Pyjutsu surface — `gerrit`, `redo`, `run` — so this section triages fifteen.

Each goes into one bucket, with evidence.

## 3.1 jj-lib-backed — a real gap

### `sparse` — **BIND** (Tier 2)

**Entry points.**

```text
working_copy.rs:71        WorkingCopy::sparse_patterns() -> &[RepoPathBuf]
working_copy.rs:145       LockedWorkingCopy::set_sparse_patterns(Vec<RepoPathBuf>)
local_working_copy.rs:1043  LocalWorkingCopy::sparse_patterns
local_working_copy.rs:2184  LockedLocalWorkingCopy::set_sparse_patterns
```

**Pyjutsu already calls both.** `src/workspace.rs:1136` reads
`.sparse_patterns()` and `src/workspace.rs:1229` calls
`locked_wc().set_sparse_patterns(patterns)` — inside `add_workspace`, to
implement its `sparse_patterns="copy"|"full"|"empty"` argument.

So this is the same shape as blame: **the machinery is linked and exercised,
and only the public verb is missing.** It is the cheapest item in Group 3 and
the kickoff's list gives no hint of that.

**Caller need.** A caller can create a workspace with sparse patterns copied
from its source, and can then neither read them back nor change them. That is a
write-only API on an existing capability — the same argument that kept
`remotes` in `LIBRARY_DESIGN_REVIEW.md` Part 1.

**Surface.** `ws.sparse_patterns()`, `ws.set_sparse_patterns(paths)`, and
`ws.reset_sparse()` — the three non-interactive `jj sparse` subcommands.
`jj sparse edit` is interactive and stays out (see §3.3).

**Size** S. **Risk** low — it takes the working-copy lock, like `snapshot`.
**Oracle** `jj sparse list`.

**Verdict: bind.**

### `metaedit` — **DEFER** (Tier 2)

**Entry points.** `commit_builder.rs:359` `set_author`, `:368` `set_committer`,
`:341` `generate_new_change_id`, `:350` `set_description`.

**Caller need.** Rewriting authorship, refreshing timestamps, and forcing a new
change id, without touching content. Real, and jj-lib backed.

**Size** M. **Risk** medium — `--update-change-id` breaks the one invariant
every Pyjutsu consumer relies on (`change_id` is stable across rewrites), and
`tx.describe` already covers the `-m` case.

**Why defer.** Every option except `--update-change-id` and `--author` is a
timestamp refresh that jj performs on any rewrite anyway. The genuinely new
capability is "set the author", and no consumer has asked for it. Binding a
verb whose headline option deliberately violates the library's central
invariant needs a caller behind it.

### `interdiff` — **DEFER** (Tier 3)

**Entry point.** `rewrite.rs:451` `rebase_to_dest_parent(repo, sources,
destination)` — exactly what `jj interdiff --help` describes ("this works by
rebasing `--from` onto `--to`'s parents and comparing the result to `--to`").

**Caller need.** "How has this change changed since the last push." A review
tool wants it; an automation tool rarely does.

**Size** S. **Risk** low — one jj-lib call, then the existing `diff::compute`
against the rebased tree.

**Why defer, given it is small.** `view.diff(rev, to=…)` already answers the
adjacent question, and the difference between the two only matters when the two
revisions have different parents. Small, clean, and unrequested — the exact
profile of a deferred item rather than a rejected one.

### `simplify-parents` — **DEFER** (Tier 3)

**Entry points.** `rewrite.rs:289` `CommitRewriter::simplify_ancestor_merge`,
driven through `repo.rs:1398` `MutableRepo::transform_descendants`.

**Caller need.** Removing redundant merge parents after a rebase. jj creates
these; jj also mostly avoids them. Narrow.

**Policy.** `revsets.simplify-parents` default `reachable(@, mutable())` —
rule 1 applies, no entry.

**Size** S. **Risk** low. **Why defer.** No caller need identified.

### `parallelize` — **DEFER** (Tier 3)

**Entry point.** None named. Confirmed by grep: jj-lib 0.44.0 contains no
`parallelize`. The building block is `transform_descendants` (`repo.rs:1398`),
and the parent-rewiring algorithm lives in jj-cli.

**Note on the rule.** That algorithm is **not policy** — it is not
configurable, so nothing can drift at an upgrade and it earns no
re-verification entry. It is simply code that must be correct, and
`jj parallelize --help` documents the semantics precisely enough to reproduce
(including that `jj parallelize '1 | 3'` is a no-op).

**Size** M. **Risk** medium — graph surgery with subtle edge cases.
**Why defer.** No caller need, and the risk is real.

### `redo` — **DEFER** (Tier 3)

**Entry point.** None named; jj-lib has no `redo`. It is an op-log walk that
finds the operation the last `undo` reverted, over `op_walk` — which Pyjutsu
already binds for `operations()`.

**Size** S. **Risk** low. **Why defer.** `ws.restore_operation(op_id)` reaches
the same state, and callers hold op ids already. Convenience, not capability.

## 3.2 jj-cli ergonomics — a composition over verbs Pyjutsu has

Each of these is a shorthand. None is a capability gap. All five are
**rejected** as bindings, with the composition given so the rejection is
checkable and so the user guide can carry the recipes (§5.5).

### `status` — **REJECT**

`jj status --help` lists exactly four things it shows. Every one is bound:

```python
wc        = view.working_copy()          # @ and its metadata
parents   = [view.resolve(p) for p in wc.parent_ids]
changes   = view.diff("@")               # the working-copy diff
conflicts = view.conflicts("@")          # conflicts in @
stuck     = [b for b in view.bookmarks() if b.conflicted]   # conflicted bookmarks
```

There is no fifth thing. `status` is a printing function over four bound reads.

### `show` — **REJECT**

`jj show` is metadata plus diff for one revision:

```python
c = view.resolve(rev)        # every field jj show prints
d = view.diff(rev)           # the diff it prints under them
```

Purely presentational. Binding it would make Pyjutsu pick a rendering, which
`PYJUTSU_CONCEPT.md` §8 rule 10 forbids ("Pyjutsu = faithful jj primitives.
Policy lives in gitman and other consumers").

### `commit` — **REJECT**

`jj commit --help` states it outright: "When called without path arguments or
`--interactive`, `jj commit` is equivalent to `jj describe` followed by
`jj new`."

```python
with ws.transaction("commit") as tx:
    tx.describe("@", message)
    tx.new()
```

With filesets it becomes a split, which `tx.split` covers — and the help lists
the differences (`jj commit` does not move bookmarks forward, has no `-r`, and
has no `-o`/`-A`/`-B`). A caller who needs those exact semantics composes them;
they are three lines, not a binding.

### `next` / `prev` — **REJECT**

Working-copy navigation over revsets Pyjutsu already evaluates:

```python
# jj next            (no --edit: the default, since ui.movement.edit = false)
target = view.resolve("@+")           # or "@++" for offset 2
with ws.transaction("next") as tx:
    tx.new(parents=[target.change_id])

# jj next --edit
with ws.transaction("next") as tx:
    tx.edit("@+")

# jj prev  /  jj prev --edit           replace "@+" with "@-"
```

`--conflict` is `heads(@:: & conflicts())`. The one piece of jj-cli policy is
`ui.movement.edit` (default `false`, confirmed on the pinned binary), and under
rule 1 the caller chooses `new` or `edit` explicitly — which they must do
anyway, because they are writing the composition.

## 3.3 Out of model — needs something Pyjutsu deliberately does not have

### `arrange` — **REJECT**

"Interactively arrange the commit graph." Confirmed by grep: no `arrange` in
jj-lib 0.44.0. It is a terminal user interface. `USER_GUIDE.md` §13 excludes
interactive selection, and `PYJUTSU_CONCEPT.md` §8 rule 10 excludes workflow
policy. A library binding to a TUI is a category error.

### `diffedit` — **REJECT**

Requires a diff editor — an external interactive program that jj launches and
waits on. Excluded by the same §13 line.

**The non-interactive half is already bound.** `tx.select_tree(commit,
selection)` builds a tree from a hunk selection, which is precisely what a diff
editor's output feeds into. A caller that wants programmatic diff editing has
the primitive; what is excluded is launching an editor.

### `sparse edit` — **REJECT**

The interactive subcommand of an otherwise-bindable verb (§3.1). `set` and
`reset` cover it non-interactively.

### `run` — **REJECT**

"Run a command across a set of revisions." Experimental upstream. It runs
subprocesses across a revset, which is workflow policy — the thing
`PYJUTSU_CONCEPT.md` §8 rule 10 assigns to consumers. `tx.fix` already covers
the in-repository case where jj-lib owns the graph half.

### `gerrit` — **REJECT**

Integration with an external code-review service. Out of model by any reading:
it is neither a jj primitive nor a git primitive.

## 3.4 Group 3 against the tiering

`LIBRARY_DESIGN_REVIEW.md`'s observation was that **every Tier 1 gap is a read
the library cannot perform**, and that this predicts where the next gaps are.

The prediction held. Group 3 contains **no Tier 1 item**. Projects 003 and 004
closed the read surface, and what the CLI has left over is ergonomics (five
verbs), interaction (three), external integration (one), and a short tail of
rewrite verbs nobody has asked for (five). One item — `sparse` — is a genuine
Tier 2 gap, and it is a *write-only-API* gap of exactly the kind Part 1 of that
review named when it refused to drop `remotes`.

The mechanical list was worth walking. It produced one bind that no prior report
had noticed, and it confirms the tiering rather than adding to it.

---

# 4. Group 4 — documented non-goals, re-examined

Re-examined **only** for a changed fact: a new jj-lib API, a real caller need,
or a wrong premise. Confirming an exclusion is a valid outcome.

## 4.1 Native async facade — **REJECT (confirmed)**

**Nothing changed.** jj-lib 0.44's `Transaction` and `MutableRepo` are still
`!Send`; `PyTransaction` is still thread-affine. The documented reason
(`DEV_GUIDE.md` §4) is unchanged and correct. Every method already releases the
GIL, so `asyncio.to_thread` gets real concurrency.

**Exclusion stands, premise verified.**

## 4.2 Interactive selection beyond `split` — **REJECT (confirmed)**

**Nothing changed.** Interactive selection needs a terminal or an external
editor. `tx.split` and `tx.select_tree` cover programmatic hunk selection,
which is the half a library can own.

**Exclusion stands, premise verified.**

## 4.3 Word / inline diff — **REJECT (confirmed), with a corrected premise**

**The exclusion is right. One sentence supporting it is wrong.**

jj-lib 0.44 has a `diff_presentation` module Pyjutsu does not use:

```text
diff_presentation/mod.rs:42    DiffTokenType { Matching, Different }
diff_presentation/mod.rs:77    LineCompareMode { Exact, IgnoreAllSpace, IgnoreSpaceChange }
diff_presentation/mod.rs:87    diff_by_line(inputs, options)
diff_presentation/mod.rs:109   unzip_diff_hunks_to_lines  -> word-level tokens
diff_presentation/unified.rs:144  DiffLineType { Context, Removed, Added }
diff_presentation/unified.rs:182  unified_diff_hunks(contents, context, options)
```

`unzip_diff_hunks_to_lines` is the word-level tokenizer behind
`jj diff --color-words`. It is public and already compiled.

**This is not new.** The module is present in jj-lib 0.42.0 as well, so no fact
changed at the 0.44 bump. Under the re-examination rule, the exclusion is not
reopened.

**But `USER_GUIDE.md` §6 states a premise that is false.** It says hunks have
"no surrounding context — every `HunkLine` is `added` or `removed`. This is a
faithful structured diff, not a byte-exact `@@` unified-diff header." That
reads as a capability limit. It is a Pyjutsu choice: `src/diff.rs:182` calls
`ContentDiff::by_line`, while `unified_diff_hunks(contents, context, options)`
would supply both context lines and the ranges — for no new dependency.

**Verdict: the exclusion stands. The stated reason must change.** Recorded as
documentation drift D11 (§5.4), not as a reopened decision. If a caller ever
asks for context lines, the entry point is above and the cost is small.

## 4.4 Assorted git refinements — **SPLIT: two confirmed, one BIND**

This one bullet in `USER_GUIDE.md` §13 names three things. They are not alike.

### Force-push flags — **REJECT (confirmed), and the wording misleads**

**`jj git push` in 0.44 has no `--force` flag at all.** The pinned binary's
help lists `-b`, `-t`, `--all`, `--tracked`, `--deleted`, `--allow-empty-description`,
`--allow-private`, `--allow-conflicts`, `-r`, `-c`, `--named`, `--dry-run`,
and `-o`. No force, no lease override.

jj push is force-with-lease **by construction**: `git.rs:3206` `GitRefUpdate`
carries `targets: Diff<Option<ObjectId>>`, where the expected position comes
from the local remote-tracking branch. There is no flag to bind.

**Exclusion stands. The documentation names a flag that does not exist**, which
implies Pyjutsu is behind the CLI when it matches it exactly. Drift D9.

### `--change` / `-r` push selection — **REJECT (confirmed), one half noted**

Two different things under one name.

- **`-r/--revision`** — "Push bookmarks and tags pointing to these commits."
  A composition: `[b.name for b in ws.bookmarks() if <target> in b.target_ids]`
  fed to the existing `ws.git_push(remote, bookmarks)`. No new capability.
- **`-c/--change`** — creates a bookmark named by
  `templates.git_push_bookmark`, default `"push-" ++ change_id.short()`. That
  is a **template**, so rule 3 refuses to vendor it. Under rule 1 the caller
  passes the name — at which point the verb is `tx.set_bookmark` plus
  `git_push`, which Pyjutsu already has. `Commit.short_change_id` (lane C3)
  even supplies the piece the default template needs.

**Exclusion stands** for both. Neither is a capability gap.

### Tag fetch — **BIND. The premise changed.** (Tier 2)

**Three facts, all new since the exclusion was written.**

1. **The pinned CLI supports it.** `jj git fetch -t, --tag <TAG>` — "Fetch only
   some of the tags (can be repeated)", with jj's string-pattern algebra. The
   help also names `remotes.<name>.fetch-tags`.
2. **jj-lib models remote tags as first-class tracked refs.** `git.rs:2701`
   `GitFetchRefExpression { bookmark, tag }`, `git.rs:550` `GitImportStats`
   with a `changed_remote_tags` field, `git.rs:3199` `GitPushRefTargets` with a
   `tags` field.
3. **Pyjutsu already builds the struct.** `src/workspace.rs:1546`:

   ```rust
   let ref_expr = GitFetchRefExpression {
       bookmark,
       tag: StringExpression::none(),   // <- the whole gap
   };
   ```

   The comment beside it reads "Tag fetching stays out of scope (jj #7528)".

**Caller need.** A release tool fetches tags. Today `ws.git_fetch` silently
returns none, and the only route is `run_jj`. Pyjutsu binds `push_tag`,
`ws.create_tag`, `ws.git.tag`, and `ws.git.tags` — fetch is the one direction
missing, which is the write-only-API argument again, in the opposite direction.

**Size** S. **Risk** low. `parse_fetch_bookmarks` already implements the
pattern algebra; `tags=` reuses it and fills the field above. `git_push` gains
the symmetric `tags=` through `GitPushRefTargets.tags`.

**Oracle** `jj git fetch --tag`, then `jj tag list`.

**Verdict: bind.** The narrowest, best-evidenced item in the whole report: one
struct field, already constructed, already commented as a known gap.

---

# 5. Group 5 — documentation drift

Every claim of the form "Pyjutsu does not do X" was treated as a testable
assertion and tested. Thirteen drifts. **None fixed here** — the plan schedules
them as one lane.

## 5.1 The known instance, confirmed unfixed

**D1 — `PYJUTSU_CONCEPT.md` §12 "Later" names three shipped features.**

| "Later" item | Reality |
|---|---|
| revset builder | shipped — `pyjutsu.revset`, `Revset`, `Pattern` |
| full diffs / hunks | shipped — `ws.diff`, `Diff`/`FileChange`/`Hunk`/`HunkLine` |
| streaming / iterator log | shipped — `ws.iter_log`, `RepoView.iter_log` |
| native backend polish | vague; not a testable claim |
| async facade | correct — not shipped, deliberately (§4.1) |
| CLI fallback backend | correct — not shipped, deliberately (§9 of that document) |
| Windows | correct — not shipped |

## 5.2 Verbs documented nowhere

**D2 — five public members appear in no document.** Checked by grep across
`USER_GUIDE.md`, `PYJUTSU_CONCEPT.md`, and `README.md`:

| Member | USER_GUIDE | CONCEPT | README |
|---|---|---|---|
| `RepoView.try_merge` | 0 | 0 | 0 |
| `MergeResult` (an exported model) | 0 | 0 | 0 |
| `Workspace.tracked_ignored_paths` | 0 | 0 | 0 |
| `Workspace.is_ancestor` / `RepoView.is_ancestor` | 0 | 0 | 0 |
| `Workspace.git_default_branch` | 0 | 0 | 0 |

`try_merge` predicts a merge conflict before performing it and `MergeResult` is
in `pyjutsu.__all__`. These are shipped, tested, exported, and invisible.

This is the mirror image of the `_pyjutsu.pyi` drift the post-0.19.0 follow-up
found: that guard catches stub drift, and nothing catches **documentation**
drift.

## 5.3 Stale version claims

**D3 — `USER_GUIDE.md` §1** says Pyjutsu "vendors jj 0.42's default aliases".
`src/config/revsets.toml`'s own header says "Vendored from jj v0.44.0", and
`DEV_GUIDE.md` §2 agrees. The section heading "0.16.0 revset and safety
changes" is history, but the sentence reads as current.

**D4 — `PYJUTSU_CONCEPT.md` §3 header narrative** stops at 0.10.0 ("and — in
0.10.0 — `untrack_paths` plus an idempotent colocated-git `sync_colocated`")
while the status line says 0.19.0. Nine releases of surface are missing from
the paragraph that claims to summarise the surface.

## 5.4 Incomplete surface listings

**D5 — `PYJUTSU_CONCEPT.md` §5 "Surface (v1)"** claims to "describe the full
intended API". Its **Reads** line omits `conflict_content`, `conflict_sides`,
`file_content`, `file_list`, `shortest_prefix`, `evolution`, `verify`,
`try_merge`, `patch_id`, and `is_ancestor`. Its **Mutations** line omits
`resolve_conflict`, `duplicate`, `absorb`, and `fix`. Its **Git** line predates
the entire `ws.git` namespace.

**D6 — `PYJUTSU_CONCEPT.md` §5 "Models"** lists the model set and omits eleven:
`GitTag`, `GitHead`, `GitWorktree`, `GitSubmodule`, `GitIndexEntry`,
`ReflogEntry`, `EvolutionEntry`, `AbsorbResult`, `FixSummary`,
`CommitSignature`, `MergeResult`. Its `Commit` field list omits `tree_id`,
`short_commit_id`, `short_change_id`, `predecessor_ids`, and `is_signed`.

**D7 — `USER_GUIDE.md` §3 `Commit` bullet** omits `tree_id` and `is_signed`.
`is_signed` is documented later in the same section, `tree_id` nowhere.

**D8 — `DEV_GUIDE.md` §2 file tables** are pre-project-003. The Rust table
lists ten files; `src/` holds nineteen entries. Missing: `conflicts.rs`,
`evolution.rs`, `fix.rs`, `fileset.rs`, `id_prefix.rs`, `dsl.rs`,
`config_loader.rs`, `src/git/`, `src/workspace/`. The Python table omits
`git.py` and `hooks.py`.

## 5.5 Claims that are wrong, not merely incomplete

**D9 — `USER_GUIDE.md` §13** names "force-push flags" as an exclusion. jj 0.44
has none. See §4.4.

**D10 — `DEV_GUIDE.md` §7** says "Pyjutsu declares the gix `sha1` and `sha256`
features itself". `Cargo.toml:54-59` declares four: `attributes`, `index`,
`sha1`, `sha256`. Lanes D7 and D9 added the first two, and the 0.19.0 release
notes in `README.md` say so — the developer guide did not follow.

**D11 — `USER_GUIDE.md` §6** presents "no surrounding context … not a
byte-exact `@@` unified-diff header" as a property of a faithful structured
diff. It is an implementation choice; `diff_presentation::unified_diff_hunks`
supplies both. See §4.3.

**D12 — `src/repo_view.rs:82-83` docstring** says `limit` "bounds the work
too". It does not: the revset is fully evaluated first. See
[[PERFORMANCE.md]] §4 and lane P1. This is a code comment, not a document, but
it is the same class of untested claim and the fix belongs with the others.

**D13 — `DEV_GUIDE.md` §10** points at `.scratch/projects/` for "the running
history". That directory holds projects 01–15; projects 001–005 live in
`.loci/projects/`. Half the history is unreachable from the developer guide.

## 5.6 The verdict

**BIND — one lane (D1).** Thirteen findings, one editing pass, no code. The
lane must also add the guard that would have caught D2 and D5 mechanically:
a test that every name in `pyjutsu.__all__` appears in `USER_GUIDE.md`. That
is the documentation counterpart of `tests/test_stub_sync.py`, and without it
this section will be rewritten in a year.

---

# 6. Group 6 — non-functional gaps

## 6.1 Read-path performance — **BIND one lane, DEFER one item**

Full treatment in [[PERFORMANCE.md]]. Summarised for verdicts:

**BIND — P1: `log(limit=N)` does not bound its work.** `eval_to_data`
(`src/repo_view.rs:90-106`) loads every commit in the revset before truncating.
`log("::@", 1)` costs 775 ms on a 100k-commit repository against
`iter_log("::@", 1)`'s 7.6 ms — a 102× gap that grows with the repository, not
with `limit`. `log_stream` already does it correctly. **Size** S. **Risk** low.

**BIND — P2: the build-profile trap.** `pyjutsu:build` runs `maturin develop`
with no `--release`; the resulting extension is 4–8× slower than a shipped
wheel. Nothing in the repository says so, and the kickoff's own data point was
distorted by it. **Size** XS — documentation plus one devenv task. **Risk**
none.

**DEFER — the `ws.git.*` fixed cost.** `fresh_loader` re-opens the store on
every call: 4–6 ms per verb on a large repository, independent of repository
size. Real, measured, and nobody has hit it. Recorded with its number.

**REJECT — a redesign of the read path.** In release it is linear, costs 74 µs
per fully modelled commit at 100k, and beats the `jj log` process on this
repository (6.7 ms against 25 ms). The question is closed.

**Two hypotheses disproved** — recorded in [[PERFORMANCE.md]] §4 so they are
not raised again: `IdPrefixContext::populate` (carried from the kickoff) and
`disambiguate_prefix_with_refs` scanning reference names (new). The dominant
cost is Pydantic validation at 45%, the candidate that was listed last.

## 6.2 Deprecation aliases with no removal version — **BIND** (a policy)

Five paths warn today. Verified in `python/pyjutsu/workspace.py`:

| Path | Replacement | Warning since |
|---|---|---|
| `ws.create_tag(message=…)` | `ws.git.create_tag` | 0.17.0 (lane A3) |
| `ws.git_refs` | `ws.git.refs` | 0.19.0 (lane D1) |
| `ws.write_git_ref` | `ws.git.write_ref` | 0.19.0 (lane D1) |
| `ws.delete_git_ref` | `ws.git.delete_ref` | 0.19.0 (lane D1) |
| `ws.remotes` | `ws.git.remotes` | 0.19.0 (lane D1) |

None names a version. A `DeprecationWarning` with no removal date is a warning
a caller can ignore forever, which makes it noise rather than a signal.

**Proposed policy.**

> A deprecated path warns for at least **two minor releases** after the one
> that introduced the warning, and is removed in the first minor release after
> that. The warning text names the removal version from the day it is added.
> Removal is a minor bump while Pyjutsu is below 1.0, and the release notes
> list every removal under one heading.

**Applied.**

| Path | Warned since | Removed in |
|---|---|---|
| `ws.create_tag(message=…)` | 0.17.0 | **0.20.0** |
| the four D1 aliases | 0.19.0 | **0.22.0** |

`create_tag(message=…)` has already had its two releases (0.18.0, 0.19.0), so
0.20.0 removes it. That leaves `ws.create_tag` pure jj-lib — the end state lane
A3 designed for — and the annotated path lives only at `ws.git.create_tag`,
which is where `COLOCATED_GIT_SURFACE.md` §2 put it.

**Size** S. **Risk** low for the four aliases; **medium** for
`create_tag(message=…)`, because it is the one removal that changes a
signature a caller may pass positionally. `tests/test_tags_annotated.py`
already covers both paths.

**Verdict: bind.** Two releases is short enough to keep the surface honest and
long enough that a pinned consumer sees the warning before the removal.

## 6.3 No CHANGELOG — **REJECT, with one addition**

`README.md` accretes release notes as sections. Today it carries 0.19.0,
0.18.0, 0.17.0, and 0.16.0 — 105 of its 274 lines, newest first, each with the
behaviour changes called out.

**The argument for a `CHANGELOG.md`** is the standard one: convention, tooling,
and a README that stops growing.

**The argument against, which wins here.** The sections are good. They explain
*why* each change happened, not just what changed, and they are the first thing
a reader of the README meets — which is right for a library whose consumers
pin an exact version and read release notes before upgrading. Splitting them
into a file nobody opens would lose that.

There is also a project-shaped reason. Every release since 0.16.0 came out of a
`.loci` project whose `project.md` already holds the full implementation log.
The README section is the summary; the project log is the changelog. A third
document between them would duplicate one of the two.

**Verdict: reject.** Keep release notes in `README.md`.

**One addition, folded into the D1 documentation lane.** The README must not
grow without limit. Add a rule: **keep the four most recent releases in
`README.md`; move older sections to `docs/RELEASES.md`**, newest first, linked
from the README. That is a mechanical trim at each release, not a new format,
and it caps the README while keeping the current notes where readers meet
them.

## 6.4 The completeness claim — **BIND** (open question 3)

`README.md` line 15 says today:

> **Status: 0.19.0 — tracks jj-lib 0.44.0.** The reads, transactions/mutations,
> op-log time travel, workspaces, and git interop are implemented and
> differential-tested against the pinned `jj` CLI.

**What is wrong with it.** "The reads … are implemented" is unbounded. After
this audit the honest boundary is knowable and specific, and the sentence
should carry it. A reader deciding whether Pyjutsu covers their case gets no
help from a list of five nouns.

**The honest sentence, to replace it:**

> **Status: 0.19.0 — binds jj-lib 0.44.0.** Pyjutsu covers jj's read,
> mutation, operation-log, workspace, and colocated-git surfaces, each
> differential-tested against the pinned `jj` CLI; it does not cover jj's
> interactive commands (`arrange`, `diffedit`, `sparse edit`), its presentation
> commands (`status`, `show`, `log` graph rendering), or a short tail of rewrite
> verbs (`revert`, `sign`, `metaedit`, `parallelize`) — reach for `run_jj` for
> those.

It is one sentence, it names the three shapes of exclusion rather than a list
that will rot, and every noun in it is checkable against this report. The
lanes in [[IMPLEMENTATION_PLAN.md]] shorten the third clause; the first two
clauses are permanent, because interaction and presentation are not things this
library will ever do.

---

# 7. What this report does not reopen

Per the kickoff's non-goals, and confirmed unchanged by the evidence above:

| Standing decision | Status |
|---|---|
| Network via gix | stands — jj shells out to `git` (`git_subprocess.rs`); Pyjutsu matches |
| Blame via gix | stands — and §1.2 binds jj's `annotate`, which is what that entry asked for |
| `git status` / dirwalk | stands — jj's snapshot model owns it; §3.2 shows `status` is a composition |
| `git gc`, repack, fsck | stands — `Store::gc` covers jj's half |
| `git describe` via gix | stands — §2.3 binds the **revset** version the same table specified |
| Mailmap | stands — not enabled, low value |
| gix `revision` feature | stands — not taken; `git describe` needs no gix |
| The depth rule | stands — `apply_head_ref_packed` is still the only site under `gix::refs::file::transaction`, and no lane here goes near it |
| CI, PyPI publish, Python-version change | stands — out of scope |
