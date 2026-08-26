# Implementation prompt — Phase A and Phase B

Work in the Pyjutsu repository. Implement **Phase A** and **Phase B** of
`.loci/projects/002-pyjutsu-refactor-jj044/IMPLEMENTATION_PLAN.md`.

Read these first, completely, in this order:

1. `.loci/projects/002-pyjutsu-refactor-jj044/IMPLEMENTATION_PLAN.md` — the lanes.
2. `.loci/projects/002-pyjutsu-refactor-jj044/project.md` — the log so far.
3. `.loci/projects/002-pyjutsu-refactor-jj044/NATIVE_SURFACE_REPORT.md` — what to remove.
4. `.loci/projects/002-pyjutsu-refactor-jj044/COLOCATED_GIT_SURFACE.md` — why the removals stop where they do.
5. `.loci/issues/002-jj-lib-0-44-upgrade-investigation/issue.md` — the upgrade order Phase B follows.
6. `AGENTS.md` and the full `.agents/skills/my-ai/SKILL.md`.

Use the `build-run-investigation-loop` skill. Read its full instructions before
you change code. If it is not in your available-skills list, do not stall and do
not invent it — follow the evidence-first workflow this prompt specifies, and
say in your final report that the skill was unavailable.

`.loci/projects/002-pyjutsu-refactor-jj044/LIBRARY_DESIGN_REVIEW.md` describes
Phase C. Read it for context only. **Do not implement any of it.**

## Objective

Land four removals, then move the jj-lib pin to 0.44.0 and release 0.17.0.

Each Phase A lane deletes code that the gix 0.85 port would otherwise have to
carry. All four land before the pin moves.

```text
A1 patch-id-hash     sha1 crate replaces gix::hash          retires F1
A2 escape-string     bind jj_lib::dsl_util::escape_string   retires F5
A3 lightweight-tags  jj tags via set_local_tag_target       API change
A4 native-gc         Store::gc replaces the keep-ref purge  retires F3
B  the bump          jj-lib 0.44.0, gix 0.85.0, wheels 0.17.0
```

A1 through A4 are independent. Land each as its own verified lane. Do not begin
B1 until all four are on trunk.

## Before you start

The working tree had uncommitted Phase 2.5 changes when this prompt was written:
`.loci/projects/002-.../project.md`, `python/pyjutsu/revset.py`,
`src/repo_view.rs`, `src/workspace.rs`, `src/workspace/tags.rs`. Those are the
comment-only changes Phase 2.5 describes.

Inspect the working tree first. If that work is still pending, verify and land
it as its own commit before you start A1. Do not fold it into a Phase A lane.

Record the real baseline before your first edit: the gate output, the cargo test
count, and the pytest result. The last recorded baseline is 7 cargo tests and
pytest exit 0 at commit `dabe76a`. Report the numbers you actually observe.

## Decisions already made

Do not re-open these. Implement them.

- **Annotated tags are renamed, not deleted.** `src/workspace/tags.rs` keeps
  working. The jj-side `create_tag` becomes lightweight; the annotated path
  survives behind an optional `message`. Phase D later moves it to
  `ws.git.create_tag`. Deleting it would create a release that can neither write
  annotated tags nor offer a replacement.
- **The ref-repair code stays in Pyjutsu.** `write_git_ref`, `delete_git_ref`,
  and `apply_head_ref_packed` are not moved and not deprecated in this project.
  An earlier report proposed moving them to gitman; `COLOCATED_GIT_SURFACE.md`
  reverses that.
- **`gix` stays a direct dependency.** jj-lib already links it, so the wheel
  already carries it. Minimising call sites is **not** a goal. Minimising API
  **depth** is.
- **Do not add `features = ["sha1"]` to the gix edge.** The upgrade issue asks
  for it. Lane A1 removes the call that needed it. This corrects the issue.
- **`patch_id_hex` stays SHA-1 in every repository**, including SHA-256 ones.
  The reasoning is already in the docstring at `src/repo_view.rs:419`.
- **Release framing: 0.17.0.** Two user-visible changes ship together — the
  `create_tag` default and the keep-ref prune removal.

## Open items — resolve with evidence, do not guess

1. **The `gc` default expiry (A4).** `jj util gc` has a default cutoff. Read it
   from the pinned jj-cli release and mirror it as the `keep_newer=None`
   default. Record the value and its source in the docstring. If you cannot find
   it, make `keep_newer` required and say why.
2. **The `sha1` crate version (A1).** Check whether the dependency tree already
   resolves a `sha1`. Reuse that version if so, to avoid a second build.
3. **`create_tag` force semantics on the jj path (A3).** Today `force=False`
   with an existing tag raises `GitError` from gix's `PreviousValue::MustNotExist`.
   The jj path has no such constraint built in. Implement the same refusal
   explicitly and test it.

## Verified anchors

Confirmed against the working tree at commit `dabe76a`, jj-lib 0.42.0 and
0.44.0, and gix 0.85.0 in the Cargo registry. **Re-verify each one. Line numbers
move.**

Pyjutsu:

```text
src/repo_view.rs:429       gix::hash::hasher(gix::hash::Kind::Sha1)   -> A1
src/repo_view.rs:450       try_finalize().to_hex().to_string()        -> A1
python/pyjutsu/revset.py   _quote                                     -> A2
src/workspace/tags.rs:26   create_tag (annotated, via gix)            -> A3
src/workspace.rs:1808      create_tag #[pymethods] delegate           -> A3
python/pyjutsu/workspace.py:311   create_tag facade                   -> A3
python/pyjutsu/_pyjutsu.pyi:114   create_tag stub                     -> A3
src/workspace.rs:136       prune_orphaned_keep_refs call site         -> A4
src/workspace.rs:249       prune_orphaned_keep_refs                   -> A4
src/workspace.rs:18        use gix::remote::{Direction, fetch::Tags}  -> B2
src/workspace.rs:309       apply_head_ref_packed                      -> B2, its own task
src/lib.rs:76              assert_eq!(version(), "0.42.0")            -> B1
```

jj-lib, public in both 0.42.0 and 0.44.0:

```text
dsl_util.rs:474            escape_string                     (pub mod dsl_util, lib.rs:50)
repo.rs:1817 / :1850       MutableRepo::set_local_tag_target
git.rs:1287                export_refs
git.rs:1408                find_git_tag_oid_to_copy          preserves fetched annotated tags
store.rs:255               Store::gc(&dyn Index, SystemTime)
repo.rs:361                Repo::index() -> &dyn Index
git.rs:940-1000            diff_refs_to_import               never scans refs/jj/keep
```

jj-lib 0.44 only:

```text
git.rs:2371                add_remote — four parameters, no fetch_tags
Cargo.toml:97              gix features: attributes, blob-diff, index,
                           max-performance-safe, sha1, sha256, zlib-rs
```

---

## Lane A1 — `jj044-refactor/patch-id-hash`

Replace the gix hasher in `patch_id_hex` with the `sha1` crate.

`gix::hash::hasher(Kind::Sha1)` is a plain SHA-1 state with no Git object
framing, so the digest is byte-identical. Keep the `update()` call order exactly
as it is.

**The required test.** Capture a known patch id **before** the edit, for a fixed
diff. Assert it after. That test is the only proof patch ids did not move. Write
it first, watch it pass on the old code, then edit.

Rewrite the last paragraph of the docstring at `src/repo_view.rs:419-425`: the
digest is a Pyjutsu content digest, always SHA-1, and no longer uses gix.

## Lane A2 — `jj044-refactor/escape-string`

Delete the hand port. Add a native free function that calls
`jj_lib::dsl_util::escape_string`, register it in the `#[pymodule]` beside
`version`, and call it from `_quote`.

Keep the name `_quote` so no call site changes. Delete the
"re-verify against `escape_string`" comment.

Test quotes, backslashes, newlines, tabs, non-ASCII, and the empty string. Each
result must be a literal the pinned `jj` CLI accepts.

## Lane A3 — `jj044-refactor/lightweight-tags`

Make the jj tag verb pure jj-lib. Keep the annotated capability reachable.

```python
ws.create_tag(name, target, message=None, force=False)
# message is None -> lightweight, via jj-lib      (new default)
# message is str  -> annotated, via gix, with a DeprecationWarning
```

The warning names `ws.git.create_tag`, which Phase D introduces. Naming a path
that does not exist yet is deliberate: it tells callers where the verb is going
without removing it first.

Lightweight path: resolve `target` to exactly one commit, call
`set_local_tag_target`, call `git::export_refs`, rebase descendants, commit the
transaction, then `finish_op`. Reuse the existing single-commit check and the
existing `finish_op` helper.

`push_tag` needs **no change**. It is already pure jj-lib. Prove that with a
test that pushes a lightweight tag.

Split the tests:

- `tests/test_tags.py` — the jj path. Oracle is `jj tag list` plus
  `git cat-file -t refs/tags/<n> == "commit"`. Cover creation, the published
  operation, `force`, a multi-revision target raising `RevsetError`, and
  `push_tag`.
- `tests/test_tags_annotated.py` — the git path. Keep every current assertion
  (`cat-file -t == "tag"`, the message body, the tagger line) and assert the
  `DeprecationWarning`.

Add one test that a **fetched** annotated tag survives a local export unchanged.
That proves `find_git_tag_oid_to_copy` still applies and that the jj path did
not degrade an incoming annotated tag to a lightweight one.

`message` moves from positional-required to keyword-optional, so positional
callers keep working. Say so in the release note.

## Lane A4 — `jj044-refactor/native-gc`

Delete `prune_orphaned_keep_refs` and its call site. Add a native `gc` method
backed by `Store::gc`.

```python
ws.gc(keep_newer: datetime | None = None) -> None
```

Delete the vendored `NO_GC_REF_NAMESPACE` constant. After this lane the string
`refs/jj/keep/` must not appear in `src/`.

Tests, in a new `tests/test_gc.py`:

- reachable keep-refs survive `gc()`, unreachable ones are removed — oracle is
  `git for-each-ref refs/jj/keep`;
- `gc()` publishes no operation;
- the re-adopt path: delete `.jj`, re-init colocated, run `gc()`, assert the
  stale keep-refs are gone. This replaces the deleted prune's coverage.

Document the behaviour change: after a re-adopt, orphaned keep-refs survive
until the next `gc()`. Nothing imports them and nothing displays them.

---

## Phase B — the bump

Follow the upgrade issue's phase order. Three amendments apply, and they are
already listed under "Decisions already made" and in the plan.

**B1 — pin move.** `jj-lib = "=0.44.0"`, `gix = "=0.85.0"`, lock regenerated.
`cargo tree -i gix` must show exactly one version. Update the `version_is_pinned`
assertion at `src/lib.rs:76` in the **same commit** as the pin, so the pin and
its assertion never disagree.

**B2 — the gix port, budgeted by call site.** After Phase A the direct gix
surface is `remotes`, `git_refs`, the ref-repair trio, and `src/workspace/tags.rs`.
All of it is shallow and stable **except one function**.

Treat `apply_head_ref_packed` (`src/workspace.rs:309`) as its own task, with its
own compile check and its own focused run:

```bash
pytest -q -n0 tests/test_git_ref_write.py
```

Do not fold it into a mechanical sweep. It drives the low-level file-store
transaction API, disables the reflog, and clears the loose `refs/heads/` tree to
survive directory/file conflicts in fractal ref names. Read its doc comment
before you touch it.

Delete the `gix::remote::fetch::Tags` import here. 0.44's `add_remote` takes
four parameters.

**B3 — SHA-256.** The capability arrives through jj-lib, which enables gix
`sha256`. Add a SHA-256 repository to the test matrix. Assert that a patch id
computed there is still 40 hex characters, proving the A1 decision holds.

**B4 — release.** Bump to `0.17.0` in `Cargo.toml` and `pyproject.toml`. Build
the wheels. Write one release note covering both user-visible changes: the
`create_tag` default and the keep-ref prune removal.

---

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

`devenv tasks run` suppresses inner stdout. Run a task's exec line directly
inside `devenv shell` when you need pass or fail detail.

Run focused tests after each slice. Run the full gate at the end of each lane.

## Non-goals

- Do not implement Phase C or Phase D. Do not create `ws.git`.
- Do not move or deprecate `write_git_ref`, `delete_git_ref`, or `git_refs`.
- Do not delete `src/workspace/tags.rs`.
- Do not remove the direct `gix` dependency.
- Do not enable the gix `revision`, `blame`, `status`, `dirwalk`, or network
  features.
- Do not add `jj-cli` as a runtime dependency.
- Do not rework `src/config_loader.rs` or the vendored `src/config/revsets.toml`.
  The revsets table stays on the re-verification list; re-diff it at B1 against
  jj 0.44's `cli/src/config/revsets.toml`.
- Do not change the public `Revset` builder surface.

Rust stays a thin jj-lib binding. Python owns coercion and ergonomics. No
`jj_lib` or `gix` type crosses the FFI. `_pyjutsu.pyi` stays synchronized with
every native surface change.

## Delivery

Land each lane separately. Never commit on a red gate.

1. Run the full gate. When it is green, commit the lane and push to `origin`.
2. Inspect the diff and the status output before each commit.
3. After A1 through A4 are on trunk, start B1.
4. After B4, confirm the gate on the final state, merge to `main`, and push.

Append a dated entry to `.loci/projects/002-pyjutsu-refactor-jj044/project.md`
for each lane, in the style of the existing entries: what changed, the
validation block, and any decision you made.

Update `README.md`, `docs/USER_GUIDE.md`, and `docs/PYJUTSU_CONCEPT.md` where
they describe tags or the keep-ref prune. `docs/USER_GUIDE.md` currently
describes `create_tag` as writing an annotated tag; that becomes false in A3.

## Final report

Include:

- each lane, what it changed, by file;
- the pinned patch-id digest, and confirmation it did not move;
- the three open items and the evidence you resolved them with;
- the `apply_head_ref_packed` port, described separately from the rest of B2;
- confirmation that `refs/jj/keep/` and `gix::hash` appear nowhere in `src/`;
- the re-verification list, now one entry;
- the real gate numbers at the baseline and at the end;
- every commit, every push, and the merge commit on `main`.

Do not stop after analysis. Continue until Phase A and Phase B are implemented,
verified, committed, pushed, and merged to `main`, or until a concrete external
blocker requires user action.

## Handoff

When Phase B is released, create the two follow-on projects through the loci
CLI and write their prompts from the plan:

- **project 003** — the jj read surface. Phase C of the plan. Start with C1
  (conflict content and resolution); it is the largest and the highest value.
  C3 carries an unresolved design decision about `IdPrefixContext` scoping; the
  plan records the recommendation and the alternative.
- **project 004** — the `ws.git` colocated namespace. Phase D of the plan. D1
  is the scaffold and blocks every other D lane.

Do not start either one in this session.
