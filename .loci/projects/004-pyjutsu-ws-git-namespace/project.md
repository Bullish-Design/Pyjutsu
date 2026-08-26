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

### 2026-08-26 — D2 annotated tags

Lane `004/d2` lands the verb A3's `DeprecationWarning` already names. The
warning now points at a path that exists, and `Workspace.create_tag(
message=...)` delegates to it instead of calling the native handle directly.

**Rust.** `src/git/tags.rs` gains the reader: `GitTagData` (plain rows; no
`gix` type crosses the FFI), `read_tag` (one ref by name, `None` if absent),
and `read_tags` (all `refs/tags/*`, sorted). A ref whose direct target is a
git tag object is annotated (message, tagger name/email, and timestamp
decoded); anything else is lightweight. `target` is always the fully-peeled
commit. The tag message's trailing newline is stripped. Two flat native
methods on `PyWorkspace`, `git_tag` and `git_tags`, delegate here — the
namespace stays pure Python.

**Python.** `GitView.create_tag` (annotated-only, message required), `tag`,
and `tags`; the `GitTag` Pydantic model (name, target, annotated, message,
tagger as a `Signature`, date as a tz-aware datetime). `Workspace.create_tag`
with a message warns and delegates to `self.git.create_tag`; its warning text
drops "when available". `_pyjutsu.pyi` tracks the two new native methods.

**Tests.** New `tests/test_git_tags.py`: read back a tag created by
`git tag -a` (message/tagger/date/peeled target), a lightweight tag
(`annotated is False`, `message is None`), a tag fetched from a bare remote,
`ws.git.create_tag` writing a real tag object, the duplicate/force rule, and
the workspace alias delegating with a `DeprecationWarning`. The existing
`test_tags_annotated.py` and `test_tags.py` suites pass unchanged.

The first full gate stopped on a Ruff import-order finding in the new test
file (an in-function import block). Imports were moved to the top and the
whole gate restarted; both the red and green runs are preserved.

Validation:

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
```

Evidence is in `artifacts/<UTC>-d2-focused/`, `artifacts/<UTC>-d2-gate/` (red
Ruff run), and `artifacts/<UTC>-d2-gate-green/`.

### 2026-08-26 — D3 git config

Lane `004/d3` gives a colocated caller a route to `core.hooksPath`, `user.signingkey`, and every
other git configuration key.

**Rust.** New `src/git/config.rs` with `get`, `set`, and `unset`, plus three flat native methods
(`git_config_get` / `git_config_set` / `git_config_unset`) delegating to them. A key is split the
way git splits it: the first component is the section, the last is the value name, and everything
between is the subsection — so `remote.my.remote.url` works. A key with no section is an error.

**Decision: reads are effective, writes are local.** `config_get` reads the merged snapshot —
system, then global, then repository-local — because that is the value git itself would use, and
"what is `core.hooksPath` in this repo" is the question the verb exists to answer. `config_set`
and `config_unset` write the repository-local file only; writing the user's global file is a
stated non-goal. The asymmetry is in both docstrings and in the user guide.

**Depth note.** `Repository::config_snapshot` is the shallow read. The write goes one level down,
to `gix::config::File::set_raw_value_filter_by`, because gix's typed `SnapshotMut::set_value`
accepts only the statically-known keys in gix's own config tree and this verb takes any key. The
filter is the same metadata test jj-lib's own `save_git_config` applies, so a new section is
created in the local file and a global section is never edited. Persisting is jj-lib's
`git::save_git_config` — no hand-rolled file write.

**Two API details that cost time.** The resolved gix-config is **0.58.0**, not the 0.59.0 also
present in the local registry; its `set_raw_value_filter_by` takes `subsection_name: Option<&BStr>`
directly, not `impl AsBStrOpt`. And the value name must be passed **by value** as a `String`:
`ValueName: TryFrom<String>` yields an owned `'static` name, which is what a `File<'static>`
requires. Passing `&str` fails to compile with a `'static` borrow error pointing at the wrong line.

**Tests.** `tests/test_git_config.py` against the `git` binary: `git config --local --get` for
the write path, `git config --get` for the effective read. Covered: set-then-get; reading a value
`git` wrote; `None` for an unset key; two- and three-part keys, including a dotted subsection;
overwrite produces one value, not a multivar; unset removes it; unset of an absent key is a no-op;
the writes publish no operation; a section-less key raises; and an existing local key survives a
write (the file is not truncated).

Validation:

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
```

Evidence is in `artifacts/<UTC>-d3-gate/`.

### 2026-08-26 — D4 HEAD state

Lane `004/d4` reads and writes the colocated `.git`'s `HEAD`, and retires the last raw
`.git/HEAD` file write.

**Rust.** New `src/git/head.rs`. `read` maps gix's `head::Kind` onto plain data: `Symbolic` →
`{name: "refs/heads/…", oid, detached: false}`, `Unborn` → the same with `oid: None`, `Detached`
→ `{name: None, oid, detached: true}`. `set` writes one `RefEdit` through
`Repository::edit_reference` with `deref: false`, so `HEAD` itself moves rather than its referent.
Two flat native methods, `git_head` and `git_set_head`.

**The raw write is gone.** `init(colocate=True, trunk=…)` used
`std::fs::write(".git/HEAD", format!("ref: refs/heads/{trunk}\n"))` guarded by a hand-rolled
`\n`/`\r` check. It now calls `head::set_on_store`, so gix validates the whole ref name. `init` has
no `PyWorkspace` yet, so that function takes the jj `Store` the initializer just returned. The
failure is no longer swallowed: an invalid trunk name now raises instead of silently leaving `HEAD`
alone.

**jj-lib gap, restated and narrowed.** jj-lib's `git.rs` only *reads* `HEAD`; `reset_head` owns a
different job (detach at `@`'s parent plus an index rewrite). gix 0.85 has **no** `set_head` either
— the plan cited one, and it does not exist at this pin. So the write is one `RefEdit` through the
shallow `edit_reference`, not the low-level file-store transaction `apply_head_ref_packed` drives.
That deep call site is untouched, as the plan requires.

**Design notes.**

- *`name` is the full ref name.* `git symbolic-ref HEAD` prints `refs/heads/main`, and that is the
  oracle, so the model matches it byte for byte. `set_head` accepts either form: a bare `main`
  becomes `refs/heads/main`, and anything starting with `refs/` is taken as written.
- *No `unborn` field.* `detached is False` with `oid is None` already says it, and the plan's shape
  is three fields. The model docstring names the state.
- *An absent branch is allowed.* `git symbolic-ref` allows it, and it is how git models an unborn
  branch.

**Tests.** `tests/test_git_head.py` against `git symbolic-ref HEAD` and `git rev-parse`: detached
after ordinary jj use, unborn in a fresh repo, `init(trunk=…)`, `set_head` by short and full name,
an absent branch, four invalid names, no operation published, and jj re-detaching `HEAD` on its
next colocated sync.

**One test-only discovery.** `Workspace.init` requires the target directory to exist; it does not
create it. That is pre-existing behaviour, not a regression from this lane — the first draft of the
`trunk` test omitted the `mkdir` and failed with `Cannot access <path>/.jj`, and a colocated `init`
with no `trunk` fails the same way.

Validation:

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
```

Evidence is in `artifacts/<UTC>-d4-gate/`.

### 2026-08-26 — D5 git worktrees

Lane `004/d5` lists the colocated repository's git worktrees. Read-only, as the plan requires:
nothing here adds, moves, or prunes.

**Rust.** New `src/git/worktrees.rs`, plus a flat `git_worktrees` native method. Each row is
`{path, head_oid, branch, locked, prunable, main}`.

**Three decisions.**

- *The main worktree is listed.* gix's `Repository::worktrees` returns only the **linked**
  worktrees — its own doc says the count is 0 when only the main worktree exists. But
  `git worktree list` starts with the main one, and that is this lane's oracle, so the binding
  prepends it from `Repository::workdir` + `head()`. The extra `main` field says which is which.
- *`prunable` is derived, not parsed.* gix has no `prunable`. git calls a worktree prunable when
  its checkout directory is gone ("gitdir file points to non-existent location"), so the binding
  applies the same test — `base()` is missing or is not a directory — rather than parsing git's
  prose.
- *`into_repo_with_possibly_inaccessible_worktree`, not `into_repo`.* A prunable worktree still
  has a readable `HEAD`, and reporting it is more useful than reporting nothing.

`branch` is the full ref name (`refs/heads/side`), matching `git worktree list --porcelain`'s
`branch` line; it is `None` for a detached worktree, and `head_oid` is `None` for an unborn one.

**Tests.** `tests/test_git_worktrees.py` against `git worktree list --porcelain`, parsed into
dicts: the main worktree alone; a linked worktree (path, branch, and `HEAD` all compared field for
field); a locked one; one whose checkout was deleted; a detached one; and no operation published.

Validation:

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
```

Evidence is in `artifacts/<UTC>-d5-gate/` and `-d5-gate-final/`.

### 2026-08-26 — D6 object access

Lane `004/d6` reads the git object database underneath jj's store: what kind of object is this
oid, does it exist, what bytes does this blob hold.

**Rust.** New `src/git/objects.rs` with `object_type`, `exists`, and `read_blob`, plus three flat
native methods. `try_find_object` and `has_object` are the shallow gix calls; the module is
read-only.

**Two decisions.**

- *A malformed oid raises; an absent one reports `None`/`False`.* `parse_oid` checks the id
  against the repository's **own** object format, so a SHA-256 repo takes 64 hex characters and a
  SHA-1 one does not. A typo must not look like a missing object, which is exactly what a bare
  "not found" would say.
- *`read_blob` refuses a non-blob.* Reading a commit's serialized form should be explicit, not
  something a caller falls into. To read a file at a revision, `RepoView.file_content` is the
  right verb — it goes through jj's model and handles conflicts.

**Tests.** `tests/test_git_objects.py` against `git cat-file -t` and `git cat-file -p`: type
agreement across all four object kinds, `None` and `False` for an absent object, blob bytes
matching `cat-file -p` (including a 256-byte binary payload round trip), the non-blob and absent
refusals, four malformed ids, and no operation published.

The fixture uses `git tag -a` for the tag object: jj 0.44's `jj tag` is a listing command, and
`jj tag v1 -m …` exits 2. The oracle for this lane is git anyway.

Validation:

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
```

Evidence is in `artifacts/<UTC>-d6-gate/`.

### 2026-08-26 — D7 submodules

Lane `004/d7` makes a colocated repository's submodules visible. **Read-only**: listing and state
only, as the plan requires. jj has no submodule support — its submodule store is a stub — so
without this a superproject is invisible to Pyjutsu.

**Feature declared.** `attributes` joins `sha1` and `sha256` on Pyjutsu's own gix edge. It gates
`gix::Repository::submodules`, which this lane calls. jj-lib already enables it, so the build cost
is zero; declaring it honours the rule that Pyjutsu never relies on a transitive crate's feature
choice (finding F1). `cargo tree -i gix` still shows exactly one version.

**Rust.** New `src/git/submodules.rs`, plus a flat `git_submodules` native method. Rows are
`{name, path, url, head_oid, index_oid, active}`, sorted by name; no `.gitmodules` yields an empty
list, not an error.

**The two oids, and the gix trap between them.** `git submodule status` prints the commit checked
out *inside* the submodule, flagged against what the superproject records. gix has three candidate
calls, and only one of them means what the name suggests:

- `Submodule::head_id()` is **not** the submodule's HEAD. It reports the *superproject's*
  `HEAD^{tree}` record for that path. The first draft used it and reported the wrong oid.
- `Submodule::index_id()` is the *superproject's index* record — the right value for `index_oid`.
- `Submodule::open()?.head_id()` reaches the submodule's own repository. That is `head_oid`.

`open()` alone is still not git's "not initialized" test: `git submodule deinit` empties the
worktree but leaves the module repository under `.git/modules`, so `open()` keeps succeeding and
would report a HEAD git does not show. The gate is `state().worktree_checkout`. With that,
`head_oid is None` means exactly git's leading `-`, and the whole status line is reconstructible:

```python
flag = "-" if head_oid is None else ("+" if head_oid != index_oid else " ")
oid = index_oid if head_oid is None else head_oid
```

A test asserts that reconstruction against `git submodule status` on a submodule whose checkout
has moved ahead of the superproject's record.

**Tests.** `tests/test_git_submodules.py`: no submodules reads as an empty list; one submodule's
name/path/url/active and both oids; name sorting across three; a deinitialized submodule; the
status reconstruction; and no operation published. `git submodule add` from a local path needs
`GIT_ALLOW_PROTOCOL=file`, which the helper sets.

Validation:

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
cargo tree -i gix                         one version (0.85.0)
```

Evidence is in `artifacts/<UTC>-d7-gate/`.

### 2026-08-26 — D8 reflog read

Lane `004/d8` reads the colocated repository's git reflog. jj's operation log covers what jj did;
it does not cover a `git reset` or `git checkout` run outside jj — exactly the case a colocated
recovery tool is called for.

**Rust.** New `src/git/reflog.rs` with a flat `git_reflog(ref_name="HEAD", limit=None)` native
method. `Head::log_iter` for `HEAD`, `Reference::log_iter` for a branch; both are the shallow gix
calls. The platform's `rev()` iterator yields most-recent first, which is `git reflog show`'s
order, so no sorting is needed. Rows are `{old_oid, new_oid, signature, message}`, with the
signature split into the same four fields every other Pyjutsu signature uses.

**Two decisions.**

- *A bare `ref` is a branch.* `reflog("main")` reads `refs/heads/main`, matching
  `git reflog show main`; a name starting with `refs/` is taken as written.
- *No reflog is an empty list; an unknown ref raises.* git creates a reflog only once something
  moves the ref, so "nothing has happened yet" is not a failure. A ref that does not exist at all
  is a different thing and raises.

**Tests.** `tests/test_git_reflog.py` against `git reflog show --format=%H%x00%gD%x00%gs`: oid and
message agreement entry for entry, newest-first order (each entry's `old_oid` is the next-older
entry's `new_oid`), the all-zero `old_oid` git writes for a ref's creation, `limit`, the signature,
a branch read by short and full name, the empty-list case, an unknown ref, and no operation
published. The fixtures build a plain git repository first and adopt it with
`Workspace.init(colocate=True)`, so the reflog holds real `git commit` entries rather than jj's.

Validation:

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
```

Evidence is in `artifacts/<UTC>-d8-gate/`.
