---
title: Implementation plan — the bind items from the gap audit
type: plan
status: draft
project: 005-pyjutsu-gap-investigation
---

# Implementation plan

Eleven lanes, one per **bind** item in [[GAP_REPORT.md]]. Nothing here is new
analysis; every lane's reasoning lives in that report and every measurement in
[[PERFORMANCE.md]].

Format follows [[.loci/projects/002-pyjutsu-refactor-jj044/IMPLEMENTATION_PLAN.md]]:
goal, size, risk, blocks, entry points, surface, steps, tests, acceptance.

## 0. Conventions

Unchanged from project 002. Restated because they bind every lane below.

**The gate.** Every lane runs the same commands before it lands. No lane lands
on a partial gate.

```text
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
ruff check python tests scripts
pytest -q
devenv tasks run pyjutsu:verify
```

**Test oracle policy.** A jj-side feature is verified against the `jj` CLI
(`tests/diff/jj_cli.py`). A git-side feature is verified against the `git`
binary. Never against Pyjutsu's own output.

**FFI rule.** No `jj_lib` or `gix` type crosses the boundary. The native layer
returns dicts, lists, strings, and bytes.

**Native layout.** `#[pymethods]` blocks stay flat on `PyWorkspace`,
`PyRepoView`, and `PyTransaction`. Namespacing happens in pure Python.

**Depth rule.** Prefer `gix::Repository` methods. No lane here touches gix at
all, so `apply_head_ref_packed` remains the only site under
`gix::refs::file::transaction`.

**The jj-cli policy rule** ([[GAP_REPORT.md]] §0). Push policy to the caller;
vendor only what `jj config list --include-defaults` prints and a test asserts;
never vendor prose or a template. **No lane below adds a re-verification
entry.** The list stays at five:

| Entry | Where |
|---|---|
| `src/config/revsets.toml` | vendored default aliases |
| the `git.object-hash` key, values, and `"sha1"` default | `src/workspace.rs` |
| the `fix.tools` schema | `src/fix.rs` |
| the `revsets.fix` default | `src/fix.rs` |
| the four `signing.behavior` names | `src/config_loader.rs` |

**Re-read every line number from the pinned source at implementation time.**
Line numbers move between releases. The ones below were read on 2026-08-26
against jj-lib 0.44.0.

---

# Phase E — the gap-audit lanes

## E1 — `005/log-limit`

**Status.** Complete on 2026-08-26. See [[RESEARCH_REPORT.md]].

**Goal.** Make `RepoView.log(revset, limit)` cost what `limit` says it costs.
Retire the measured 102× penalty and the docstring that denies it.

**Size** S. **Risk** low. **Blocks** nothing.

**The defect.** `eval_to_data` (`src/repo_view.rs:90-106`) calls
`revset::evaluate`, which collects the **whole** revset into a `Vec<Commit>` —
one store read per commit — and only then calls `commits.truncate(limit)`.

Measured on a 100,002-commit repository, release build
([[PERFORMANCE.md]] §4):

```text
view.log("::@", 1)        775.0 ms
view.iter_log("::@", 1)     7.6 ms      102x
jj log -r "::@" -n 1       40   ms      (process included)
```

The cost is linear in the whole revset, not in `limit`, so it grows without
bound as a repository grows. `ws.log(revset, limit=50)` is the shape
`docs/USER_GUIDE.md` §3 teaches first.

**The fix already exists in the file.** `log_stream`
(`src/repo_view.rs:460-484`) collects ids through `revset::evaluate_ids` —
7.2 ms for 100k, no store reads — truncates, and only then reads commits.

**Entry points.** None new. `revset::evaluate_ids` (`src/revset.rs:177`) and
`Store::get_commit` are both already used.

**Surface.** Unchanged. This is a pure performance fix with identical results.

**Steps.**
1. Rewrite `eval_to_data` to evaluate ids, truncate, then build one
   `CommitData` per surviving id — the shape `log_stream` plus
   `PyCommitStream::__next__` already use together.
2. Keep `resolve` and `resolve_single` on the full-evaluation path. They must
   count **every** match to raise "resolved to N revisions", so truncation
   would break their contract. Verify this while editing: `resolve` calls
   `eval_to_data(py, revset_str, None)` today and must keep passing `None`.
3. Fix the docstring at `src/repo_view.rs:82-83`. It claims `limit` "bounds the
   work too"; after this lane that is true, so state what is true and drop the
   parenthetical about the `CommitData` build.

**Tests.**
- Behavioural: `log(revset, limit=N)` returns byte-identical rows to today for
  a fixture with more than `N` matches, and identical rows to
  `list(iter_log(revset, limit=N))`. Order must not move.
- Contract: `resolve` on a multi-match revset still raises `RevsetError` naming
  the **full** count, not the truncated one. This is the regression the fix can
  cause, so it gets an explicit test.
- Performance: on a fixture repository built to a few hundred commits, assert
  `log(revset, 1)` reads no more commits than `iter_log(revset, 1)`. Assert
  behaviour, not wall-clock — a timing assertion in a test suite is flaky.

**Acceptance.** Gate green. `log(limit=N)` and `iter_log(limit=N)` agree, and
the docstring matches the code.

## E2 — `005/trailers`

**Goal.** Bind jj's description-trailer parser. Retire the hand-rolled parsing
in the consumer Pyjutsu exists to serve.

**Size** XS. **Risk** low. **Blocks** nothing.

**Entry points.**

```text
trailer.rs:60   parse_description_trailers(body: &str) -> Vec<Trailer>
trailer.rs:79   parse_trailers(body: &str) -> Result<Vec<Trailer>, TrailerParseError>
trailer.rs:23   Trailer { key: String, value: String }
```

Pure functions over a `&str`. No repo, no transaction, no revset, no GIL
release.

**Surface.**

```python
pyjutsu.parse_trailers(description: str) -> list[Trailer]
Commit.trailers -> list[Trailer]        # a Python property over self.description
```

**Steps.**
1. Add a native free function in `src/dsl.rs` beside `escape_string` (lane A2's
   shape), returning a list of `{key, value}` dicts. Register it in the
   `#[pymodule]`.
2. Bind `parse_description_trailers`, **not** `parse_trailers`. The first is
   infallible and takes the trailing block of a description, which is what a
   commit description is. The second returns `TrailerParseError` and is for
   parsing a standalone trailer block; expose it only if a caller asks.
3. New frozen `Trailer` model in `models.py` (`key`, `value`), exported.
4. `Commit.trailers` as a Python property calling the free function on
   `self.description`. It costs nothing until read, so it does not touch the
   per-commit budget [[PERFORMANCE.md]] §3 measures.
5. Stub entry in `_pyjutsu.pyi`; the golden model-shape fixture is regenerated
   only if `Trailer` counts as a model field somewhere.

**Tests.** Oracle is the pinned CLI's `trailers` template keyword
(`jj log -T 'trailers'`). Cover: no trailers; one; several; a trailer-shaped
line inside the body rather than the trailing block; a multi-line value; a
`Signed-off-by` and a `Change-Id` together; an empty description.

**Acceptance.** Gate green. `Commit.trailers` matches the CLI's template
output for every fixture.

## E3 — `005/annotate`

**Goal.** Bind `jj file annotate`. Close the D-reject table's "blame via gix —
bind jj's" entry by binding jj's.

**Size** S. **Risk** low. **Blocks** nothing.

**Entry points.**

```text
annotate.rs:154  FileAnnotator
annotate.rs:165  FileAnnotator::from_commit(commit, path) -> BackendResult<Self>   (async)
annotate.rs:219  FileAnnotator::compute(repo, domain: &Arc<ResolvedRevsetExpression>)  (async)
annotate.rs:233  FileAnnotator::to_annotation() -> FileAnnotation
annotate.rs:60   FileAnnotation
annotate.rs:87   FileAnnotation::lines()             -> (Result<&CommitId, &CommitId>, &BStr)
annotate.rs:102  FileAnnotation::line_ranges()
annotate.rs:119  FileAnnotation::compact_line_ranges()
annotate.rs:290  LineOrigin { commit_id, line_number }
```

**Already linked.** `src/transaction.rs:719` calls
`jj_lib::absorb::split_hunks_to_trees`, which constructs
`FileAnnotator::with_file_content` at `absorb.rs:158`. The machinery compiles
and Pyjutsu's absorb tests exercise it.

**The one helper is already there.** `compute` takes an
`Arc<ResolvedRevsetExpression>`, and `revset::resolve_expression`
(`src/revset.rs:203`, added by lane C6) produces exactly that.

**Surface.**

```python
view.annotate(path, rev="@", domain=None) -> list[AnnotationLine]
# AnnotationLine: line_number (1-based), content (bytes), commit_id, origin_line_number
```

**Design decisions to make and record.**
- *`Result<&CommitId, &CommitId>`.* `lines()` yields `Ok` for a resolved origin
  and `Err` for an unresolved one (the walk hit the domain boundary). Both
  carry a commit id. Decide how Python sees the difference — a `resolved: bool`
  field is the honest shape; collapsing them loses information the CLI shows.
- *`domain` default.* `jj file annotate` has no domain flag; jj-cli passes the
  whole visible history. Default to `None` meaning `::rev`, and let a caller
  narrow it. Do **not** vendor a `revsets.*` default — none exists for this
  verb, so rule 1 is free here.
- *`content` is `bytes`.* Matching `view.file_content` (lane C2). The caller
  decodes.
- *Do not add `Commit.annotations`.* Annotating is a per-file walk. The C4 and
  C8 precedent (`predecessor_ids`, `is_signed`) says the expensive read is a
  method, not a field.

**Steps.**
1. New `src/annotate.rs`. Resolve `rev` to one commit, build the annotator with
   `from_commit`, resolve the domain expression, drive `compute` off the GIL
   with `pollster::block_on`, then flatten `to_annotation().lines()` into plain
   rows.
2. Flat native method `PyRepoView::annotate`; Python `RepoView.annotate`; new
   `AnnotationLine` model; `_pyjutsu.pyi` entry; regenerate the model-shape
   golden.
3. Raise a clear error for a path absent at that revision, and point a
   conflicted path at `conflict_content` — the same rule `file_content` (C2)
   applies.

**Tests.** Oracle is `jj file annotate -T` with an explicit template so the
comparison is field-for-field, not against the default rendering. Cover: a file
edited across several commits; a file added in one commit; a file whose lines
all come from one commit; a binary file; a path absent at `rev`; a narrowed
`domain` producing unresolved origins.

**Acceptance.** Gate green. Every line's origin commit matches the CLI.

## E4 — `005/sparse`

**Goal.** Make the working copy's sparse patterns readable and writable. Retire
a write-only API on a capability Pyjutsu already drives.

**Size** S. **Risk** low. **Blocks** nothing.

**Entry points.**

```text
working_copy.rs:71          WorkingCopy::sparse_patterns() -> &[RepoPathBuf]
working_copy.rs:145         LockedWorkingCopy::set_sparse_patterns(Vec<RepoPathBuf>)  (async)
local_working_copy.rs:1043  LocalWorkingCopy::sparse_patterns
local_working_copy.rs:2184  LockedLocalWorkingCopy::set_sparse_patterns
```

**Already linked.** `src/workspace.rs:1136` reads `.sparse_patterns()` and
`src/workspace.rs:1229` calls `locked_wc().set_sparse_patterns(patterns)`,
both inside `add_workspace` to implement `sparse_patterns="copy"|"full"|"empty"`.

**Surface.**

```python
ws.sparse_patterns() -> list[str]              # jj sparse list
ws.set_sparse_patterns(paths) -> None          # jj sparse set
ws.reset_sparse() -> None                      # jj sparse reset  (== set to root)
```

`jj sparse edit` is interactive and stays out ([[GAP_REPORT.md]] §3.3).

**Design decisions to make and record.**
- *Paths are repo-relative strings*, matching `view.file_list` (C2) and
  `tracked_ignored_paths`. `RepoPathBuf::from_internal_string` on the way in.
- *Publishing.* `jj sparse set` updates the working copy on disk. Decide and
  record whether it publishes a jj operation, by checking the pinned CLI's op
  log after a `jj sparse set` — do not guess. Lanes D3 through D9 all recorded
  "publishes no operation" after checking; this one may differ, because it
  writes files.
- *Empty pattern list.* `reset` sets the patterns to `[RepoPathBuf::root()]`
  (see `local_working_copy.rs:1093`), not to an empty vector. An empty vector
  means "no files". Make `set_sparse_patterns([])` explicit — either reject it
  or document that it empties the working copy. Check what the CLI does first.

**Steps.**
1. New `src/workspace/sparse.rs` (or a section of `src/workspace.rs` beside
   `add_workspace`'s existing calls). Reuse the same locking path
   `add_workspace` and `snapshot` use.
2. Three flat native methods on `PyWorkspace`; three Python methods on
   `Workspace`; `_pyjutsu.pyi` entries.
3. `add_workspace`'s `sparse_patterns=` argument keeps working unchanged.

**Tests.** Oracle is `jj sparse list`. Cover: the default (`.`, the root);
setting one prefix and reading it back; setting several; `reset`; that files
outside the pattern leave the working copy and files inside return; the
stale-working-copy interaction; and whichever operation-publishing behaviour
the CLI check established.

**Acceptance.** Gate green. `ws.sparse_patterns()` matches `jj sparse list`
after every mutation in the test set.

## E5 — `005/tag-fetch`

**Goal.** Fetch and push git tags. Fill the one struct field Pyjutsu already
constructs and comments as a known gap.

**Size** S. **Risk** low. **Blocks** nothing.

**Entry points.**

```text
git.rs:2701  GitFetchRefExpression { bookmark, tag }
git.rs:2733  expand_fetch_refspecs(remote_name, ref_expr)
git.rs:550   GitImportStats.changed_remote_tags
git.rs:3199  GitPushRefTargets { bookmarks, tags }
```

**The gap, in full.** `src/workspace.rs:1546`:

```rust
let ref_expr = GitFetchRefExpression {
    bookmark,
    tag: StringExpression::none(),   // "Tag fetching stays out of scope (jj #7528)"
};
```

and `src/workspace.rs:1748`:

```rust
let targets = GitPushRefTargets {
    bookmarks: branch_updates,
    tags: vec![],
};
```

**Surface.**

```python
ws.git_fetch(remote, bookmarks=None, tags=None)   # tags: jj string patterns, as bookmarks
ws.git_push(remote, ..., tags=None)               # push named tags
```

**Design decisions to make and record.**
- *`tags=None` keeps today's behaviour* — no tags fetched. Changing the default
  would alter what an existing caller's `git_fetch` writes to the repository,
  which is a behaviour change, not new surface. Every lane in projects 003 and
  004 added surface without changing behaviour; keep that property.
- *Reuse `parse_fetch_bookmarks`* for the tag patterns. It already implements
  jj's glob-by-default, `kind:` prefixes, and `~` negation. Rename it if it now
  serves both, or keep the name and note it.
- *Push tags are the symmetric change*, and `push_tag(name, remote)` already
  exists as a single-tag path. Decide whether `git_push(tags=…)` supersedes it
  or sits beside it, and say so in the docstring. Do not deprecate `push_tag`
  in this lane — that is E9's business, and it needs its own removal version.

**Tests.** Oracle is `jj git fetch --tag` followed by `jj tag list`, against a
bare remote built by the existing `init_bare_remote` helper. Cover: fetching
all tags; a glob pattern; a negated pattern; a tag that already exists locally;
an annotated tag surviving the round trip (`ws.git.tag(name).annotated` stays
`True`, the property `find_git_tag_oid_to_copy` protects); and `tags=None`
fetching nothing, which is the no-behaviour-change assertion.

**Acceptance.** Gate green. `tags=None` leaves every existing test unchanged.

## E6 — `005/sign`

**Goal.** Sign and unsign an existing commit. Complete the capability lane C8
half-finished.

**Size** S. **Risk** low. **Blocks** nothing.

**Entry points.**

```text
commit_builder.rs:382  CommitBuilder::set_sign_behavior(SignBehavior)
commit_builder.rs:387  CommitBuilder::set_sign_key(String)
signing.rs:147         SignBehavior { Drop, Keep, Own, Force }
```

`jj sign -r X` is a rewrite of X with `Force`; `jj unsign -r X` the same with
`Drop`. The pinned help confirms: "Note that revisions are always re-signed."

**What C8 already built.** `SignSettings` and the `Signer` are wired through
`UserSettings`; `PyTransaction` holds `settings: Arc<UserSettings>` (added by
C7); `Commit.is_signed` and `RepoView.verify` read the result;
`SIGN_BEHAVIORS` validates the four names.

**Surface.**

```python
tx.sign(revsets, key=None) -> list[Commit]
tx.unsign(revsets) -> list[Commit]
```

**Policy, and why no re-verification entry.** `revsets.sign` defaults to
`reachable(@, mutable())`. Under rule 1 the revset is **required** — no default,
nothing vendored. Record that choice in the docstring so the next reader does
not add the default back.

**Steps.**
1. Resolve the revsets (multi-revision, dedup by id), run the existing
   immutable/root guard, and rewrite each with the builder call. Follow lane
   C5's ordering lesson: `duplicate` needed reverse-topological order **filtered
   to the target set**, and the unfiltered topological sort pulled in the whole
   ancestry. Check whether signing needs the same treatment — it rewrites in
   place rather than re-parenting, so it may not, but verify rather than assume.
2. `rebase_descendants` after the rewrites, like every other rewrite verb.
3. Two flat native methods on `PyTransaction`; two Python methods; stub
   entries.

**Tests.** Extend `tests/test_signing.py`, which already generates an ed25519
key and an `allowed-signers` file and skips when `ssh-keygen` is absent. Cover:
signing an unsigned commit makes `is_signed` true and `verify` return `"good"`;
`unsign` reverses it; signing several revisions in one transaction publishes
one operation; an immutable revision raises; `key=` selects a different key;
and signing with no backend configured raises rather than silently doing
nothing (the rule C7 set for an empty `fix.tools`).

**Acceptance.** Gate green. The CLI's `signature.status()` template agrees with
`verify` on every signed fixture.

## E7 — `005/revert`

**Goal.** Bind `jj revert`. Give the library the recovery path it lacks: undo a
landed change without rewriting it.

**Size** M. **Risk** medium. **Blocks** nothing.

**Entry points.**

```text
rewrite.rs:133  restore_tree(source, destination, source_label, destination_label, matcher)  (async)
rewrite.rs:451  rebase_to_dest_parent(repo, sources, destination)  (async)
rewrite.rs:59   merge_commit_trees(repo, commits)  (async)
```

No jj-lib `revert` or `back_out` — confirmed by grep over the pinned source.
This lane assembles the composition, as jj-cli does.

**Surface.**

```python
tx.revert(revsets, onto=None, insert_after=None, insert_before=None,
          description=None) -> list[Commit]
```

**Policy, and why no re-verification entry.** `templates.revert_description` is
a **template**:

```text
'Revert "' ++ description.first_line() ++ '"' ++ "\n",
"This reverts commit " ++ commit_id ++ ".\n",
```

Rendering it needs jj's template engine, which Pyjutsu does not have. Rule 3
refuses to vendor it. Rule 1 makes `description` a caller argument.

**Decide and record: what does `description=None` do?** Two defensible answers,
and the lane must pick one and say why in the docstring:
1. Raise. Explicit, and never diverges from jj.
2. Use a fixed Pyjutsu string that does not claim to be jj's. Convenient, and
   honest as long as the docstring says it is Pyjutsu's wording, not jj's.

Do **not** hand-render jj's template into a format string. That is rule 3 in
the form it is easiest to violate: it looks like a two-line constant and it is
a vendored jj-cli policy with nothing to check it.

**Steps.**
1. Resolve the revsets; order them **reverse-topologically within the target
   set**, exactly as lane C5 learned to for `duplicate` — filter the neighbours
   to the targets or `dag_walk::topo_order_reverse_ok` pulls in the whole
   ancestry and panics on the root's empty parents.
2. For each revision in that order, compute the reversed tree — `restore_tree`
   with the commit's tree as the destination and its parent tree as the source
   — and write a new commit onto the chosen location.
3. Implement the three location modes. `--onto` is the simple one. `-A`/`-B`
   insert into the graph and rebase descendants; both accept repetition to
   build a merge. Exactly one of the three is required — the CLI enforces this
   in its usage line (`<--onto|--insert-after|--insert-before>`), so match it.
4. `rebase_descendants` before commit.

**Tests.** Oracle is `jj revert`, compared repo-state-for-repo-state the way
lane C6 compared `jj absorb`: the shared test config pins
`debug.commit-timestamp`, so the same mutation over two copied repositories is
byte-identical. Pass the same `description` to both sides so the templated
default never enters the comparison.

Cover: one revision onto a target; several revisions (order matters — assert
the reverse-topological result, not just the set); `--insert-after` and
`--insert-before` including the repeated form that builds a merge; reverting a
revision that touches a path another revision also touches; an immutable target
raising; and the `description=None` behaviour this lane chose.

**Acceptance.** Gate green. The repository state after `tx.revert` is
commit-for-commit identical to the CLI's, given the same description.

## E8 — `005/describe`

**Goal.** Land the `git describe` replacement the D-reject table specified and
project 003 closed without.

**Size** S. **Risk** low. **Blocks** nothing.

**Entry points.** None new. The whole implementation is a revset over machinery
already bound: `heads(::rev & tags())` for the nearest tagged ancestor, and
`(tag::rev).count()` minus one for the distance.

**This does not enable gix's `revision` feature.** `COLOCATED_GIT_SURFACE.md`
§3's recommendation not to take it stands untouched.

**Surface.**

```python
view.describe(rev="@") -> Describe | None
# Describe: tag, distance, commit_id, short_commit_id, dirty=False
```

**Design decisions to make and record.**
- *jj's tag view is the source, not git's refs.* A git tag jj has not imported
  is invisible. That is correct for a jj binding, it is the reason this beats
  the gix version, and it must be in the docstring rather than discovered by a
  caller.
- *`None` when no tagged ancestor exists*, matching `git describe`'s failure
  rather than inventing a fallback. `git describe --always` falls back to the
  short id; expose that as an explicit argument if a caller asks, not by
  default.
- *Several tags on one commit.* `heads()` can return more than one. Decide the
  tie-break and record it — `git describe` prefers the most recent tag, and jj's
  tag view has no ordering, so name the rule (lexicographic is defensible and
  deterministic; "whichever comes first" is not).
- *`distance` counts commits, not merges.* State which, and test a merge.

**Tests.** Oracle is `git describe --tags --long` on a **colocated**
repository, after `ws.git_export()` so jj's tags and git's refs agree. Cover: a
tag on `rev` itself (distance 0); a tag several commits back; no tag at all
(`None`); several tags on the same commit; a tag on a side branch that is not
an ancestor (must be ignored); a merge in the path; and — the case that proves
the design — a **non-colocated** repository, where `git describe` cannot run
and this verb can.

**Acceptance.** Gate green. Every colocated fixture agrees with
`git describe --tags --long`, and the non-colocated fixture returns a correct
answer with no git binary involved.

## E9 — `005/deprecations`

**Goal.** Give every deprecation a removal version, and take the first removal.

**Size** S. **Risk** medium — one removal changes a signature.
**Blocks** nothing. **Ships in 0.20.0.**

**The policy, to record in `docs/DEV_GUIDE.md`.**

> A deprecated path warns for at least **two minor releases** after the one
> that introduced the warning, and is removed in the first minor release after
> that. The warning text names the removal version from the day it is added.
> Removal is a minor bump while Pyjutsu is below 1.0, and the release notes
> list every removal under one heading.

**Applied.**

| Path | Replacement | Warned since | Removed in |
|---|---|---|---|
| `ws.create_tag(message=…)` | `ws.git.create_tag` | 0.17.0 (A3) | **0.20.0** |
| `ws.git_refs` | `ws.git.refs` | 0.19.0 (D1) | 0.22.0 |
| `ws.write_git_ref` | `ws.git.write_ref` | 0.19.0 (D1) | 0.22.0 |
| `ws.delete_git_ref` | `ws.git.delete_ref` | 0.19.0 (D1) | 0.22.0 |
| `ws.remotes` | `ws.git.remotes` | 0.19.0 (D1) | 0.22.0 |

**Steps.**
1. Add the removal version to all five warning strings
   (`python/pyjutsu/workspace.py:373`, `:401`, `:434`, `:449`, `:511`).
2. Remove `ws.create_tag`'s `message` parameter. It has had its two releases.
   `ws.create_tag(name, target, force=False)` becomes pure jj-lib — the end
   state lane A3 designed for — and the annotated path lives only at
   `ws.git.create_tag`, where `COLOCATED_GIT_SURFACE.md` §2 put it.
3. Update `docs/USER_GUIDE.md` §8, `docs/PYJUTSU_CONCEPT.md` §5, and
   `README.md`, each of which documents the `message=` form.
4. Release notes under one "Removals" heading, naming 0.22.0 for the rest.

**Risk note.** This is the lane that breaks a caller. `message` is
positional-or-keyword today, and A3's own risk note said "any caller passing it
positionally keeps working". After this lane, a positional third argument
becomes `force`, which is a **silent** change of meaning, not an error.

Mitigate: make `force` keyword-only in the same commit, so
`ws.create_tag("v1", "@", "msg")` raises `TypeError` instead of quietly
setting `force` to a truthy string. Say so in the release notes.

**Tests.** `tests/test_tags_annotated.py` moves entirely to
`ws.git.create_tag`. Add a test that `ws.create_tag(name, target, "msg")`
raises `TypeError`. Delete the alias-warning test for this path; keep the four
D1 ones and extend each to assert the warning text names 0.22.0.

**Acceptance.** Gate green. `grep -n "message" python/pyjutsu/workspace.py`
shows no `create_tag` message path. The four remaining warnings name their
removal version.

## E10 — `005/docs`

**Goal.** Close all thirteen documentation drifts, fix the completeness claim,
and add the guard that stops the next thirteen.

**Size** M. **Risk** low. **Blocks** nothing.

**The drifts.** [[GAP_REPORT.md]] §5, D1 through D13. Summarised by file:

| File | Drifts |
|---|---|
| `docs/USER_GUIDE.md` | D2 (five undocumented verbs), D3 (says jj 0.42's aliases; they are 0.44's), D7 (`Commit` omits `tree_id`, `is_signed`), D9 (names a `--force` flag jj has never had), D11 (presents a choice as a limit) |
| `docs/PYJUTSU_CONCEPT.md` | D1 ("Later" names three shipped features), D4 (narrative stops at 0.10.0), D5 (§5 Reads/Mutations/Git omit 0.18.0 and 0.19.0), D6 (§5 Models omits eleven models and five `Commit` fields) |
| `docs/DEV_GUIDE.md` | D8 (file tables predate project 003), D10 (says two gix features; `Cargo.toml:54-59` declares four), D13 (points only at `.scratch/projects/`; projects 001–005 are in `.loci/projects/`) |
| `src/repo_view.rs:82` | D12 — fixed by lane E1, not here |
| `README.md` | the completeness claim |

**The completeness claim.** Replace `README.md` line 15 with the sentence
[[GAP_REPORT.md]] §6.4 settles on. It names the three **shapes** of exclusion —
interactive, presentational, and the rewrite tail — rather than a list that
rots, and the lanes above shorten only the third clause.

**The release-notes rule** ([[GAP_REPORT.md]] §6.3). There is no `CHANGELOG.md`
and there will not be one; the README sections are better, because they explain
why. But they must stop growing: keep the four most recent releases in
`README.md` and move older sections to a new `docs/RELEASES.md`, newest first,
linked from the README. At 0.19.0 that moves nothing; the rule takes effect at
0.20.0.

**The guard — the part that matters most.** D2 and D5 are mechanically
detectable, and nothing detects them. `tests/test_stub_sync.py` guards the
native surface; add its documentation counterpart:

> A test asserting that every name in `pyjutsu.__all__` appears in
> `docs/USER_GUIDE.md`.

Follow `test_stub_sync.py`'s design notes, which were earned:
- *Names, not prose.* The test proves a verb is mentioned, not that the prose
  is good. A weak guard that runs beats a strong one that cannot.
- *One allowlist, in the test, with a reason per entry.* A name that genuinely
  belongs in no user document (an exception class the errors table covers by
  another name) is listed with why, not silently skipped.
- *Verify it in both directions.* Confirm it reproduces the five real gaps in
  D2, and confirm a deliberately invented export fails it — the same
  two-directional check that validated the stub guard.

**Steps.** One editing pass per file, then the guard, then re-run the audit
grep from D2 and confirm it is empty.

**Tests.** The new guard. It must fail on today's tree — that is the proof it
works — and pass after the editing pass.

**Acceptance.** Gate green with the new guard in it. The D2 grep returns
nothing. `README.md` carries the new status sentence.

## E11 — `005/release-profile`

**Goal.** Stop the repository from producing misleading performance numbers.

**Size** XS. **Risk** none. **Blocks** nothing.

**The trap.** `nix/pyjutsu.nix:21` and `:99` run `maturin develop --uv` — no
`--release`. The installed `python/pyjutsu/_pyjutsu.abi3.so` is a debug
artifact, 87 MB against a release build's 20 MB, and **4× to 8× slower**
([[PERFORMANCE.md]] §5). The kickoff's own 66 ms data point, and the alarming
first pass of this investigation, both came from it.

Users never get that build: `pyjutsu:wheel` runs `maturin build --release`.

**Steps.**
1. `docs/DEV_GUIDE.md` §3: state that `pyjutsu:build` installs a **debug**
   extension, that it is 4–8× slower than a shipped wheel, and that any
   performance measurement must use `maturin develop --release --uv` first.
   Put it beside the existing stale-build tripwire note, which is the same kind
   of warning and the place a reader already looks.
2. Add a `pyjutsu:build-release` task running
   `maturin develop --release --uv`, so the correct build is one command and not
   a remembered incantation.
3. `docs/DEV_GUIDE.md` §6: any recorded performance number names its build.

**Tests.** None — this is documentation and one task definition. Verify the new
task by exit code, the way the `pyjutsu:wheel` smoke check was verified
(`devenv tasks run` suppresses task stdout).

**Acceptance.** Gate green. `devenv tasks run pyjutsu:build-release` exits 0
and leaves a release extension installed. Re-run `pyjutsu:build` afterwards so
the tree ends on the normal debug build.

---

# Sequencing

```text
E1  log-limit         ─┐
E11 release-profile   ─┤  land first: they change how everything else is measured
                       │
E2  trailers          ─┤
E3  annotate          ─┤  independent; any order, or parallel lanes
E4  sparse            ─┤
E5  tag-fetch         ─┤
E6  sign              ─┤
E8  describe          ─┘
                       │
E7  revert            ── the one M/medium lane; most review time
                       │
E9  deprecations      ─┐
E10 docs              ─┤  land last: both document what the lanes above added
                       │
                       v
                  release 0.20.0
```

**E1 and E11 first.** E11 changes how any later measurement is read, and E1 is
the only lane fixing something that is wrong rather than missing. Neither
blocks anything, so "first" is a recommendation, not a dependency.

**E2 through E8 are independent of each other.** Run them in any order or in
parallel lanes. Ranked by value per unit cost:

1. **E2 trailers** — XS, and it retires hand-rolled parsing in the consumer
   Pyjutsu exists to serve.
2. **E5 tag-fetch** — S, and the gap is a single struct field already
   constructed and already commented as known.
3. **E4 sparse** — S, and it retires a write-only API.
4. **E3 annotate** — S, highest absolute value, closes a D-reject entry.
5. **E6 sign** — S, completes what C8 half-finished.
6. **E8 describe** — S, a loose end from the 002 plan.

**E7 last of the feature lanes.** It is the only M/medium lane, it repeats lane
C5's ordering trap, and it has three graph-insertion modes to get right. Start
it early if lanes run in parallel, so it gets the most review time — the same
reasoning project 003 applied to C1.

**E9 and E10 land last.** Both document what the lanes above add, and E9's
removal is the only breaking change in the phase, so it belongs next to the
release note that announces it.

## Release

**0.20.0.** One breaking change (E9's `create_tag(message=…)` removal), so the
minor number moves and the release notes lead with it under a "Removals"
heading naming 0.22.0 for the remaining four aliases.

Everything else is added surface with no behaviour change, which is the
property projects 003 and 004 both held: `tags=None` keeps `git_fetch`
unchanged (E5), and E1 changes only how long an identical answer takes.

## Definition of done

- The gate is green on trunk after every lane.
- Each lane's oracle test passes against the pinned `jj` 0.44.0 CLI, or the
  `git` binary for E8's colocated fixtures.
- **The re-verification list still has five entries.** No lane here vendored
  jj-cli policy; if one did, the rule in [[GAP_REPORT.md]] §0 was not applied.
- `docs/USER_GUIDE.md` documents every new verb, and E10's guard proves it
  mechanically rather than by inspection.
- `cargo tree -i gix` shows one version, and `apply_head_ref_packed` is still
  the only site under `gix::refs::file::transaction`.
- The three figures in [[PERFORMANCE.md]] §2 are re-measured in a **release**
  build on the 100k repository, and recorded with their build named.

## What this plan does not schedule

Nine deferred items, recorded in [[GAP_REPORT.md]] with their entry points and
sizes so a future project picks them up without re-investigating: graph edges,
bisect, watchman, `metaedit`, `interdiff`, `simplify-parents`, `parallelize`,
`redo`, and the `ws.git.*` fixed cost.

Each is deferred for the same reason: real, bounded, and with no caller need
proven. None is blocked by anything in this plan.

**Do not open projects 006+.** Sequencing was this project's job; scheduling is
the user's.
