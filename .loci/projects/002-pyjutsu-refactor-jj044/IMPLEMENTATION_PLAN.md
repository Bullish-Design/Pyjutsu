---
title: Implementation plan — refactor, upgrade, read surface, git namespace
type: plan
status: draft
project: 002-pyjutsu-refactor-jj044
---

# Implementation plan

This plan turns three reports into ordered, testable lanes:

- [[NATIVE_SURFACE_REPORT.md]] — what to remove.
- [[LIBRARY_DESIGN_REVIEW.md]] — what is missing on the jj side.
- [[COLOCATED_GIT_SURFACE.md]] — what to add on the git side.

Where the reports disagree, the last one wins. It supersedes the
"minimise gix" premise of the first two.

## 0. Conventions

**Lanes.** `gitman.toml` sets `trunk = "main"`. Work happens on stacked lanes
named `<project>/<lane>`. Each lane lands on trunk only after its gate passes.

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
returns dicts, lists, strings, and bytes. `src/lib.rs:3-7` states this; keep it.

**Native layout.** `#[pymethods]` blocks stay flat on `PyWorkspace` and
`PyRepoView`. Namespacing happens in pure Python. A `ws.git.*` call reaches
`self._handle.git_*` on the same flat handle.

**Depth rule.** Prefer `gix::Repository` methods. Any code reaching under
`gix::refs::file::transaction` carries a `// gix depth:` comment, its own test,
and its own line in the upgrade budget.

**Re-verification list.** Each jj-lib upgrade re-diffs every vendored copy.
The list starts at three entries and ends at one:

| Entry | Retired by |
|---|---|
| `python/pyjutsu/revset.py::_quote` | Lane A2 |
| `NO_GC_REF_NAMESPACE` in `src/workspace.rs` | Lane A4 |
| `src/config/revsets.toml` | never — keep |

---

# Phase A — pre-bump lanes (project 002)

Every lane here deletes code that the gix 0.85 port would otherwise have to
carry. All four land **before** the pin moves.

## A1 — `jj044-refactor/patch-id-hash`

**Goal.** Remove Pyjutsu's only direct gix hashing call. Retire finding F1.

**Size** S. **Risk** low. **Blocks** nothing.

**Change.** `repo_view.rs:429` calls `gix::hash::hasher(gix::hash::Kind::Sha1)`.
That is a plain SHA-1 state with no Git object framing, so the `sha1` crate
produces identical bytes.

```rust
// before
let mut hasher = gix::hash::hasher(gix::hash::Kind::Sha1);
...
hasher.try_finalize().expect(...).to_hex().to_string()

// after
use sha1::{Digest, Sha1};
let mut hasher = Sha1::new();
...
format!("{:x}", hasher.finalize())
```

**Files.** `src/repo_view.rs`, `Cargo.toml` (add `sha1`), `Cargo.lock`.

**Steps.**
1. Add `sha1 = "0.10"` to `[dependencies]`. Track the version jj-lib resolves
   if it already pulls one, to avoid a second build.
2. Replace the hasher. Keep `update()` call order byte-for-byte identical.
3. Keep the docstring at `repo_view.rs:419-425`. Rewrite its last paragraph:
   the digest is a Pyjutsu content digest, always SHA-1, and no longer uses gix.

**Tests.** Add a regression test that pins one known digest for a fixed diff.
Capture the value **before** the edit and assert it after. This is the only
proof that patch ids did not move.

**Acceptance.** Gate green. The pinned digest is unchanged.

**Upgrade-order effect.** Delete the planned `features = ["sha1"]` step from
the issue-002 pin move. It is no longer needed.

## A2 — `jj044-refactor/escape-string`

**Goal.** Delete the hand port of jj-lib's string escaper. Retire finding F5.

**Size** S. **Risk** low.

**Change.** `jj_lib::dsl_util::escape_string` is public in 0.42 and 0.44
(`dsl_util.rs:474`), and `dsl_util` is a public module (`lib.rs:50`).

**Files.** new `src/dsl.rs` (or a function in `src/revset.rs`), `src/lib.rs`,
`python/pyjutsu/revset.py`, `python/pyjutsu/_pyjutsu.pyi`.

**Steps.**
1. Add a native free function:
   ```rust
   #[pyfunction]
   fn escape_string(s: &str) -> String { jj_lib::dsl_util::escape_string(s) }
   ```
2. Register it in the `#[pymodule]` next to `version`.
3. Replace `revset.py::_quote`'s body with a call to it. Keep the name `_quote`
   so no call site changes.
4. Delete the "re-verify against `escape_string`" comment.

**Tests.** Keep every existing `_quote` test. Add a property-style case list:
quotes, backslashes, newlines, tabs, non-ASCII, empty string. Each must match
what the `jj` CLI accepts for the same literal.

**Acceptance.** Gate green. The re-verification list drops to two entries.

## A3 — `jj044-refactor/lightweight-tags`

**Goal.** Make the jj tag API pure jj-lib. Preserve the annotated capability
without a gap.

**Size** M. **Risk** medium — public API change. **Blocks** the pin move.

**Design.** One verb, two paths, chosen by whether a message is given.

```python
ws.create_tag(name, target, message=None, force=False)
# message is None -> lightweight, via jj-lib          (new default)
# message is str  -> annotated, via gix + DeprecationWarning
```

The deprecation points at `ws.git.create_tag`, which lane D2 introduces. Until
then the warning names the future path. This ordering avoids a capability gap:
no release can both write annotated tags and not write them.

**Lightweight path.**
1. Resolve `target` to exactly one commit. Reuse the existing check.
2. `tx.repo_mut().set_local_tag_target(name, RefTarget::normal(id))`
   (0.42 `repo.rs:1817`, 0.44 `repo.rs:1850`).
3. `git::export_refs(tx.repo_mut())` writes `refs/tags/<name>`
   (`git.rs:1287`).
4. `rebase_descendants`, then `tx.commit(...)`, then `finish_op`.
5. `force=false` and an existing local tag ⇒ `GitError`, matching today.

**Annotated path.** Unchanged code. Move `src/workspace/tags.rs` to
`src/git/tags.rs` in lane D1; in A3 it stays where it is.

**Files.** `src/workspace/tags.rs`, `src/workspace.rs` (the `create_tag`
delegate at `:1808`), `python/pyjutsu/workspace.py:311`,
`python/pyjutsu/_pyjutsu.pyi:114`, `tests/test_tags.py`.

**Tests.** Split `tests/test_tags.py`.
- `test_tags.py` — the jj path. Oracle is `jj tag list` plus
  `git cat-file -t refs/tags/<n> == "commit"`. Cover: creation publishes one
  operation, the tag appears in jj's view, `force` semantics, multi-revision
  target raises `RevsetError`, and `push_tag` still works on a lightweight tag.
- `test_tags_annotated.py` — the git path. Keep every existing assertion
  (`cat-file -t == "tag"`, message body, tagger line), and assert the
  `DeprecationWarning` fires.

**Acceptance.** Gate green. `push_tag` is untouched — it was already pure
jj-lib. A fetched annotated tag survives a local export, proving
`find_git_tag_oid_to_copy` (`git.rs:1408`) still applies.

**Risk note.** This is the one lane that changes an existing signature.
`message` moves from positional-required to keyword-optional. Any caller
passing it positionally keeps working.

## A4 — `jj044-refactor/native-gc`

**Goal.** Replace the hand-rolled keep-ref purge with jj's own collector.
Retire finding F3 and the vendored private constant.

**Size** M. **Risk** medium — changes recovery behaviour.

**Change.**
1. Delete `prune_orphaned_keep_refs` (`workspace.rs:249`) and its call in
   `adopt_existing_git` (`workspace.rs:136`).
2. Add a native method backed by `Store::gc` (`store.rs:255`), which takes
   `&dyn Index` — available from `Repo::index()` (`repo.rs:361`) — and a
   `SystemTime` cutoff.

```python
ws.gc(keep_newer: datetime | None = None) -> None
```

**Open item, resolve during implementation.** jj-cli's `jj util gc` has a
default expiry. Read it from the pinned jj-cli release and mirror it as the
`None` default. Record the value and its source in the docstring. Do not guess.

**Files.** `src/workspace.rs`, `python/pyjutsu/workspace.py`,
`python/pyjutsu/_pyjutsu.pyi`, new `tests/test_gc.py`.

**Tests.**
- Create a keep-ref by writing a commit; run `gc()`; assert reachable keep-refs
  survive and unreachable ones are gone. Oracle: `git for-each-ref refs/jj/keep`.
- Assert `gc()` publishes no operation.
- Re-adopt path: delete `.jj`, re-init colocated, run `gc()`, assert the stale
  keep-refs are gone. This replaces the old prune's coverage.

**Behaviour change to document.** After a re-adopt, orphaned keep-refs now
survive until the next `gc()`. Nothing imports them
(`diff_refs_to_import`, `git.rs:940-1000`, never scans `refs/jj/keep`) and
nothing displays them. They hold dead objects on disk.

**Acceptance.** Gate green. `NO_GC_REF_NAMESPACE` no longer appears in
`src/`. The re-verification list drops to one entry.

---

# Phase B — the jj-lib 0.44 bump (project 002)

Follow the existing upgrade guide and
[[.loci/issues/002-jj-lib-0-44-upgrade-investigation/issue.md]] in their phase
order. Three amendments apply.

**B1 — pin move.** `jj-lib = "=0.44.0"`, `gix = "=0.85.0"`, lock regenerated.
`cargo tree -i gix` must show exactly one version. The `lib.rs` test
`version_is_pinned` asserts `"0.42.0"` today — update it to `"0.44.0"` in the
same commit, so the pin and its assertion never disagree.

**Amendment 1.** Do **not** add `features = ["sha1"]`. Lane A1 removed the
call that needed it.

**B2 — gix 0.84 → 0.85 port, budgeted by call site.** After Phase A the direct
gix surface is: `remotes` (2 shallow calls), `git_refs` (5 shallow calls),
`write_git_ref` / `delete_git_ref` / `apply_head_ref_packed` (the deep one),
and `src/git/tags.rs`.

**Amendment 2.** `apply_head_ref_packed` (`workspace.rs:309`) is its own task
with its own compile check and its own test run
(`pytest -q -n0 tests/test_git_ref_write.py`). Do not fold it into a
mechanical sweep. Everything else in the port is shallow and stable.

The `gix::remote::fetch::Tags` import at `workspace.rs:18` is deleted here:
0.44's `add_remote` takes four parameters, not five (`git.rs:2371`).

**B3 — SHA-256.** jj-lib 0.44 enables gix `sha256`, so the capability arrives
through the dependency.

**Amendment 3.** `patch_id_hex` stays SHA-1 in every repository, including
SHA-256 ones. Finding F2's decision is already recorded in the docstring at
`repo_view.rs:419`. Add a SHA-256 repository to the test matrix and assert that
a patch id computed there has the same 40-hex width.

**B4 — release.** Pyjutsu 0.17.0 wheels. The changelog names one behaviour
change (`create_tag` default) and one removal (the keep-ref prune).

---

# Phase C — the jj read surface (project 003)

Open a new project. Do not attach this to 002.

Pyjutsu's mutation surface is close to complete. Its read surface stops at
metadata and diffs. Every Tier 1 item below is a read the library cannot
perform, and each forces callers out to a subprocess today.

## C1 — conflict content and resolution

**Size** L. **Risk** medium. **Priority** highest.

**Why.** `Conflict` carries `path`, `num_sides`, `num_bases`. That reports a
conflict exists and nothing more.

**jj-lib entry points** (read exact signatures from the pinned source at
implementation time):

```text
conflicts.rs:207   materialize_tree_value
conflicts.rs:264   try_materialize_file_conflict_value
conflicts.rs:450   materialize_merge_result
conflicts.rs:838   parse_conflict
conflicts.rs:1050  update_from_content
conflicts.rs:305   ConflictMarkerStyle
conflicts.rs:326   ConflictMaterializeOptions
```

**Surface.**

```python
view.conflict_content(path, rev="@", style="diff") -> str
view.conflict_sides(path, rev="@") -> list[str]      # parsed, no markers
tx.resolve_conflict(path, content) -> Commit         # update_from_content
```

**Steps.**
1. New `src/conflicts.rs`. Read the tree value at `path`, materialize it, and
   return marked text.
2. Expose `ConflictMarkerStyle` as a string enum in Python
   (`"diff"`, `"snapshot"`, `"git"`). Validate in Python, map in Rust.
3. `resolve_conflict` runs inside the existing transaction, writes the tree,
   and returns the rewritten commit.
4. Add a `Conflict.content` accessor only if it can be lazy. Do not materialize
   every conflict during `conflicts()`.

**Tests.** Build a three-way conflict with the `jj` CLI. Assert the marked text
matches `jj file show` byte-for-byte in each marker style. Round-trip:
materialize, edit one side, resolve, assert `jj status` reports no conflict.

## C2 — file content and listing

**Size** S. **Risk** low.

**Why.** No `jj file show`. `Store::read_file` is already called in
`diff_stat.rs:117`; only the public verb is missing.

**Surface.**

```python
view.file_content(path, rev="@") -> bytes
view.file_list(rev="@", paths=None) -> list[str]     # fileset-filtered
```

**Notes.** Return `bytes`, not `str` — the caller decodes. Reuse the fileset
parsing already present at `workspace.rs:668-677`. Raise a clear error for a
path that is a conflict, and point at `conflict_content`.

**Tests.** Oracle is `jj file show` and `jj file list`. Cover binary content,
a path absent at that revision, and a fileset pattern.

## C3 — short id prefixes

**Size** M. **Risk** low.

**Why.** Every model returns full 40-character ids. jj's UX rests on shortest
unique prefixes, and a caller that reimplements uniqueness without the index
will get it wrong.

**jj-lib.** `id_prefix.rs:116` `IdPrefixContext`, `:150` `IdPrefixIndex`.

**Surface.**

```python
view.shortest_prefix(id) -> str
Commit.short_change_id      # populated when the view can supply a context
Commit.short_commit_id
```

**Open decision.** `IdPrefixContext` is scoped by a revset. jj-cli reads
`revsets.short-prefixes`, which is **not** in Pyjutsu's vendored
`src/config/revsets.toml`. Choose one and record it:

1. Disambiguate across the whole repo. Simple, always correct, slower.
2. Disambiguate within a configured revset, defaulting to `visible()`. Matches
   the CLI more closely, and needs a new vendored config key.

Recommend option 1 for the first release. It has no configuration surface and
no vendored data.

**Tests.** Oracle is `jj log -T 'change_id.shortest()'`. Assert prefixes are
unique within the tested repo, and that a prefix resolves back to its id.

## C4 — evolution and predecessors

**Size** M. **Risk** low.

**Why.** Pyjutsu sets `record_synthetic_predecessors: true` on import and then
cannot read the data back. gitman rewrites commits constantly and has no API to
follow a change across those rewrites.

**jj-lib.** `evolution.rs:86` `walk_predecessors`, `:46`
`CommitEvolutionEntry`, `:203` `accumulate_predecessors`.

**Surface.**

```python
view.evolution(change_id, limit=None) -> list[EvolutionEntry]
Commit.predecessor_ids -> list[CommitId]
```

**Tests.** Oracle is `jj evolog`. Cover a described-then-amended commit, a
rebase, and an abandoned commit.

## C5 — duplicate and backout

**Size** S. **Risk** low.

**jj-lib.** `rewrite.rs:1010` `duplicate_commits`, `:1156`
`duplicate_commits_onto_parents`. The `rewrite` module is already imported for
`move_commits` and `squash_commits`, so this is close to free.

**Surface.** `tx.duplicate(commits, onto=None) -> list[Commit]`.

**Tests.** Oracle is `jj duplicate`.

## C6 — absorb

**Size** M. **Risk** medium.

**Why.** Distributes working-copy edits into the ancestor commits that
introduced those lines. Automation generates exactly the scattered fixups
absorb exists to file away.

**jj-lib.** `absorb.rs:108` `split_hunks_to_trees`, `:308` `absorb_hunks`,
`:294` `AbsorbStats`.

**Surface.** `tx.absorb(source="@", into=None) -> AbsorbResult`.

**Tests.** Oracle is `jj absorb`. Assert the stats match and that hunks with no
unique ancestor stay behind.

## C7 — fix

**Size** M. **Risk** medium.

**Why.** Runs formatters over a commit range in-process. This maps directly
onto the verify-then-land loop, which shells out today.

**jj-lib.** `fix.rs:181` `fix_files`, `:123` `ParallelFileFixer`, `:471`
`get_base_commit_map`.

**Surface.** `tx.fix(revset, tools=None) -> FixSummary`. Tool configuration
comes from jj's own `fix.tools` config; do not invent a second format.

**Tests.** Oracle is `jj fix` with a trivial tool (for example `sed`).

## C8 — commit signing

**Size** L. **Risk** medium. **Adoption-blocking for some users.**

**Why.** A land-and-push library cannot serve a repository that requires signed
commits.

**jj-lib.** `signing.rs:166` `Signer`, `:147` `SignBehavior`, `:37`
`SigStatus`, `:61` `Verification`; backends `gpg_signing`, `ssh_signing`.

**Surface.**

```python
Workspace.load(path, sign_behavior="keep")   # keep | drop | force | own
Commit.signature -> CommitSignature | None    # status, key, display
view.verify(rev) -> CommitSignature
```

**Notes.** Signing configuration lives in jj's settings. Wire the `Signer`
through the existing `UserSettings` path rather than adding Pyjutsu keys.

**Tests.** Use jj-lib's `test_signing_backend` for unit coverage. Add one
end-to-end SSH-signing test guarded on the binary being present.

## C9 — Tier 3 backlog

Ranked, not scheduled: graph edges for `log` (`graph.rs:33`), blame
(`annotate.rs:154`), description trailers (`trailer.rs:60`), watchman
(`fsmonitor`), bisect (`bisect.rs:75`).

---

# Phase D — the colocated git namespace (project 004)

Everything here is inside the free feature budget: jj-lib 0.44 already enables
`attributes`, `blob-diff`, `index`, `max-performance-safe`, `sha1`, `sha256`,
and `zlib-rs` on gix, and Cargo unifies features. Adding these calls costs code,
not compile time or wheel size.

Do **not** enable the `revision` feature. See D-reject below.

## D1 — namespace scaffold

**Size** M. **Risk** low. **Blocks** D2 through D9.

**Steps.**
1. New Rust module `src/git/` with `mod.rs`, and move `src/workspace/tags.rs`
   to `src/git/tags.rs`.
2. New pure-Python `python/pyjutsu/git.py` with a `GitView` class holding the
   same `PyWorkspace` handle.
3. `Workspace.git` — a cached property returning `GitView`.
4. Move, with deprecating aliases on `Workspace`:

| Today | Becomes |
|---|---|
| `ws.git_refs(prefix)` | `ws.git.refs(prefix)` |
| `ws.write_git_ref(name, target)` | `ws.git.write_ref(name, target)` |
| `ws.delete_git_ref(name)` | `ws.git.delete_ref(name)` |
| `ws.remotes()` | `ws.git.remotes()` |

Keep `git_import`, `git_export`, `sync_colocated`, `git_fetch`, `git_push` on
`Workspace`. They are jj operations that publish jj operations, not git-side
reads.

**Tests.** Every moved verb keeps its existing test file. Add one test per
alias asserting the `DeprecationWarning`.

## D2 — annotated tags

**Size** S after D1. **Risk** low.

**Surface.**

```python
ws.git.create_tag(name, target, message, force=False)   # annotated
ws.git.tag(name) -> GitTag | None
ws.git.tags() -> list[GitTag]
# GitTag: name, target, annotated, message, tagger, date
```

**gix.** `object.rs:338` `tag` (create), `object.rs:112` `find_tag`,
`reference.rs:15` `tag_reference`. All ungated.

**Steps.** The creation code already exists in `src/git/tags.rs`. Add the
reader. Make `ws.create_tag(message=...)` delegate here and keep its
`DeprecationWarning` from A3, now naming a path that exists.

**Tests.** Read back a tag created by `git tag -a`, a tag fetched from a
remote, and a lightweight tag (`annotated == False`, `message is None`).

## D3 — git config

**Size** S. **Risk** low.

**Why.** Colocated users need `core.hooksPath`, `user.signingkey`, and
per-remote settings, and have no route to any of them. jj-lib already assumes
callers hold a gix config: `save_git_config` takes `&gix::config::File`
(`git.rs:2218`).

**Surface.**

```python
ws.git.config_get(key) -> str | None
ws.git.config_set(key, value) -> None
ws.git.config_unset(key) -> None
```

**gix.** `repository/config/mod.rs:10` `config_snapshot`, `:20`
`config_snapshot_mut`. Ungated.

**Notes.** Keys are `section.key` or `section.subsection.key`. Write to the
repository-local config only; never to the global file. Reject a key with no
section.

**Tests.** Oracle is `git config --local --get`.

## D4 — HEAD state

**Size** S. **Risk** low.

**Surface.**

```python
ws.git.head() -> GitHead        # {name, oid, detached}
ws.git.set_head(name) -> None   # symbolic, refs/heads/<name>
```

**gix.** `reference.rs:187` `head`, `:211` `head_id`, `:219` `head_name`.
Ungated.

**Steps.** Replace the raw `std::fs::write(".git/HEAD", ...)` at
`workspace.rs:1091` with `set_head`. Drop the hand-rolled newline validation —
gix validates the ref name.

**Tests.** Oracle is `git symbolic-ref HEAD` and `git rev-parse HEAD`. Cover a
detached HEAD, an unborn branch, and `init(colocate=True, trunk=...)`.

## D5 — git worktrees

**Size** S. **Risk** low.

**Why.** jj workspaces and git worktrees coexist badly. jj-lib's own
`export_some_refs` walks `git_repo.worktrees()` to detach HEAD in each one.
This project's baseline log tracks a stray worktree by hand.

**Surface.** `ws.git.worktrees() -> list[GitWorktree]` with `path`, `head_oid`,
`branch`, `locked`, `prunable`.

**gix.** `repository/worktree.rs:16` `worktrees`. Ungated.

**Tests.** Oracle is `git worktree list --porcelain`.

## D6 — object access

**Size** S. **Risk** low.

**Surface.**

```python
ws.git.object_type(oid) -> str          # commit | tree | blob | tag
ws.git.exists(oid) -> bool
ws.git.read_blob(oid) -> bytes
```

**Tests.** Oracle is `git cat-file -t` and `git cat-file -p`.

## D7 — submodules

**Size** M. **Risk** low. **Read-only.**

**Why.** jj has no submodule support — `submodule_store` is a stub. A colocated
repo with submodules is invisible to Pyjutsu today.

**Surface.** `ws.git.submodules() -> list[GitSubmodule]` with `name`, `path`,
`url`, `head_oid`, `active`.

**gix.** `repository/submodule.rs:93` `submodules`, gated on `attributes` —
already enabled by jj-lib.

**Scope limit.** Listing and state only. Do not add update, init, or clone.
Those mutate a working copy jj knows nothing about.

**Feature declaration.** Add `attributes` to Pyjutsu's own gix edge. It is
already compiled; declaring it honours the rule that Pyjutsu never relies on a
transitive crate's feature choice.

**Tests.** Oracle is `git submodule status`.

## D8 — reflog read

**Size** S. **Risk** low.

**Why.** jj's op log covers what jj did. It does not cover a `git reset` or
`git checkout` run outside jj — exactly the case a colocated recovery tool is
called for.

**Surface.** `ws.git.reflog(ref="HEAD", limit=None) -> list[ReflogEntry]` with
`old_oid`, `new_oid`, `signature`, `message`.

**gix.** `head/log.rs:10` and `reference/log.rs:13` `log_iter`. Ungated.

**Tests.** Oracle is `git reflog show --format=...`.

## D9 — git index read

**Size** S. **Risk** low. **Read-only.**

**Surface.** `ws.git.index_entries() -> list[GitIndexEntry]` with `path`,
`oid`, `stage`, `mode`.

**Notes.** The `index` feature is already enabled. Read only. Writing the index
behind jj's back is a trap, and jj-lib's `reset_head` owns index updates.
Declare `index` on Pyjutsu's own gix edge.

**Tests.** Oracle is `git ls-files --stage`.

## D-reject — decided against, with reasons

| Candidate | Reason |
|---|---|
| Network via gix | jj shells out to `git` for the network (`git_subprocess.rs`). Match that. `credentials` plus a TLS transport is the heaviest thing in gix. |
| Blame via gix | jj-lib has `annotate` (`annotate.rs:154`). Bind jj's — see C9. |
| `git status` / dirwalk | jj's snapshot and working-copy model own this. Two answers to one question. |
| `git gc`, repack, fsck | `Store::gc` covers jj's half (lane A4). Leave git-side maintenance to the git binary. |
| `git describe` | Needs the `revision` feature jj-lib does not enable. Implement it as a revset over jj's tag view — nearest tagged ancestor. That is exact, pure jj-lib, and works on non-colocated repos too. Schedule it in project 003, not here. |
| Mailmap | Not enabled. Low value. |

---

# Sequencing

```text
A1 patch-id-hash ─┐
A2 escape-string ─┼─> B1 pin move ─> B2 gix port ─> B3 sha256 ─> B4 release 0.17.0
A3 lightweight-tags ─┤                                              │
A4 native-gc ────────┘                                              │
                                                                    v
                              project 003 (C1 -> C2 -> C3, then C4..C8)
                                                                    │
                                                                    v
                                          project 004 (D1, then D2..D9)
```

A1 through A4 are independent of each other. Run them in any order, or in
parallel lanes, but land all four before B1.

C1, C2, and C3 are independent. C1 is the highest value and the largest; start
it first so it has the most review time.

D1 blocks every other D lane. D2 through D9 are independent of each other.

## Definition of done, per phase

**Phase A.** The gate is green on trunk. `NO_GC_REF_NAMESPACE` and
`gix::hash` appear nowhere in `src/`. The re-verification list has one entry.

**Phase B.** `cargo tree -i gix` shows one version. `version_is_pinned`
asserts `0.44.0`. A SHA-256 repository passes the full Python suite. Wheels
build for the release matrix.

**Phase C.** Each lane's oracle test passes against the `jj` CLI at the pinned
version. `docs/USER_GUIDE.md` documents every new verb.

**Phase D.** Each lane's oracle test passes against the `git` binary. Every
gix feature Pyjutsu calls is declared on Pyjutsu's own edge. `ws.git` is the
only namespace holding git-side reads.
