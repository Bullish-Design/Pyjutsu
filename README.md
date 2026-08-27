# Pyjutsu

A general-purpose, Pythonic + Pydantic binding to **jujutsu's Rust engine (`jj-lib`)** via
PyO3/maturin — native graph, op-log, working-copy, and conflict access **in-process**, with
no subprocess and no text parsing.

- **Import:** `import pyjutsu`
- **Binds:** jujutsu / `jj-lib` **0.44.0** (pinned in `Cargo.toml` + `devenv.nix`). Pyjutsu is
  versioned on its own cadence, independent of the jj version it binds; `pyjutsu.JJ_VERSION`
  reports the linked jj-lib at runtime.
- **Docs:** [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) (using the library) ·
  [`docs/DEV_GUIDE.md`](docs/DEV_GUIDE.md) (working on it) ·
  [`docs/PYJUTSU_CONCEPT.md`](docs/PYJUTSU_CONCEPT.md) (design spec).

**Status: 0.20.0 — tracks jj-lib 0.44.0.** The reads, transactions/mutations, op-log time travel,
workspaces, and git interop are implemented and differential-tested against the pinned `jj` CLI.

### 0.19.0 — the colocated git surface

0.19.0 adds public surface only. Nothing existing changes behaviour. It completes `ws.git`, the
namespace 0.18.0 opened: the git half of a colocated repository that jj deliberately does not
model.

**Git configuration.** `ws.git.config_get(key)` returns the **effective** value — the merged
system, global, and repository-local configuration git itself would use — so it answers "what is
`core.hooksPath` here". `ws.git.config_set` and `ws.git.config_unset` write the
**repository-local** file only, never your global one.

**`HEAD`.** `ws.git.head()` returns `{name, oid, detached}`, and `ws.git.set_head(name)` points
`HEAD` at a branch symbolically. The raw `.git/HEAD` file write behind
`init(colocate=True, trunk=...)` is gone; gix validates the ref name now.

**Worktrees.** `ws.git.worktrees()` lists them as `GitWorktree` rows (`path`, `head_oid`,
`branch`, `locked`, `prunable`, `main`), matching `git worktree list --porcelain`. Read-only.

**Objects.** `ws.git.object_type(oid)`, `ws.git.exists(oid)`, and `ws.git.read_blob(oid)` read the
git object database directly. A malformed oid raises rather than reporting "absent".

**Submodules.** `ws.git.submodules()` lists what `.gitmodules` declares. jj has no submodule
support at all, so this is the only way to see them. Read-only: no update, init, or clone.

**Reflog.** `ws.git.reflog(ref="HEAD", limit=None)` returns `ReflogEntry` rows newest first. jj's
operation log covers what jj did; it does not cover a `git reset` or `git checkout` run outside
jj, which is exactly when a colocated recovery tool is needed.

**Index.** `ws.git.index_entries()` returns `GitIndexEntry` rows in `git ls-files --stage` order,
conflict stages included. Read-only: jj-lib's `reset_head` owns index writes.

Two gix features are now declared on Pyjutsu's own dependency edge — `attributes` (submodules) and
`index` (the index read). Both were already enabled by jj-lib, so nothing about the build changes;
Pyjutsu simply no longer relies on a transitive crate's feature choice.

### 0.18.0 — the jj read surface

0.18.0 adds public surface only. Nothing existing changes behaviour.

**Conflicts are readable and resolvable.** `view.conflict_content(path, rev, style)` returns the
marked text `jj file show` prints (`"diff"`, `"snapshot"`, or `"git"` markers);
`view.conflict_sides(path, rev)` parses it back into the conflict's terms; and
`tx.resolve_conflict(path, content)` writes a resolution into `@`.

**File content and listing.** `view.file_content(path, rev)` returns raw bytes (`jj file show`);
`view.file_list(rev, paths)` lists a revision's files, fileset-filtered (`jj file list`).

**Short ids.** `view.shortest_prefix(id)` gives the shortest unique prefix, and every `Commit`
carries `short_commit_id` / `short_change_id`. Prefixes disambiguate across the **whole**
repository, so they are never ambiguous; a commit-id prefix can therefore be longer than the
CLI's, which scopes itself to `visible()`.

**Evolution.** `view.evolution(change_id)` follows a change across its rewrites (`jj evolog`).
`Commit.predecessor_ids` is filled on those entries; ordinary reads leave it empty rather than
pay an op-log walk per commit.

**Three rewrite verbs.** `tx.duplicate(commits, onto)` (`jj duplicate`),
`tx.absorb(source, into)` (`jj absorb`), and `tx.fix(revset, tools)` (`jj fix`). `fix` uses jj's
own `fix.tools` configuration — Pyjutsu defines no second format.

**Commit signing.** `Commit.is_signed` is a cheap field read; `view.verify(rev)` runs jj's
configured signing backend and returns the verdict. `Workspace.load(path, sign_behavior=...)`
overrides jj's `signing.behavior` for one handle (`"drop"`, `"keep"`, `"own"`, `"force"`); the
backend and key still come from jj's own configuration.

**The `ws.git` namespace opens.** A colocated repository's git half now has one place to live.
`ws.git_refs`, `ws.write_git_ref`, `ws.delete_git_ref`, and `ws.remotes` move to `ws.git.refs`,
`ws.git.write_ref`, `ws.git.delete_ref`, and `ws.git.remotes`; each old name still works and
emits a `DeprecationWarning`. `ws.git.create_tag(name, target, message)` writes an annotated git
tag — the path 0.17.0's `create_tag(message=...)` warning already named — and `ws.git.tag(name)`
/ `ws.git.tags()` read tags back. `git_import`, `git_export`, `sync_colocated`, `git_fetch`, and
`git_push` stay on `Workspace`: they publish jj operations, they are not git-side reads.

### 0.17.0 behaviour changes

Pyjutsu now binds jj-lib 0.44.0 (was 0.42.0). Two user-visible changes ship with it.

**`create_tag` writes a lightweight jj tag by default.** `ws.create_tag(name, target)` now goes
through jj-lib (`set_local_tag_target` + `export_refs`) and publishes one operation, so the tag is
a jj tag that the CLI lists. Pass `message=` to keep the old annotated Git tag; that form still
works, still takes the message positionally, and now emits a `DeprecationWarning` naming
`ws.git.create_tag`, where the verb moves in a later release. Creating a tag that already exists
raises `GitError` unless you pass `force=True`. A fetched annotated tag is never degraded.

**Adopt no longer prunes keep-refs; `ws.gc()` does.** Re-adopting a colocated repo used to delete
orphaned `refs/jj/keep/` entries with a raw Git ref transaction. That workaround is gone. Use the
new `ws.gc(keep_newer=None)`, which delegates to jj-lib's `Store::gc` and mirrors `jj util gc`
(default: keep everything newer than two weeks; no operation is published). Between a re-adopt and
the next `gc()`, stale keep-refs stay in `.git`; nothing imports or displays them.

Also new: a repo created by `init` follows the `git.object-hash` setting, so `"sha256"` gives a
SHA-256 repository, exactly as `jj git init` does. `patch_id` is unaffected — it is a Pyjutsu
content digest, stays SHA-1, and returns the same value in both formats.

Note for callers who read jj configuration: jj 0.44 removed `ui.revsets-use-glob-by-default`.

### 0.16.0 behaviour changes

Pyjutsu now resolves revset configuration the same way as the pinned CLI and ships jj 0.42's
default aliases. Consequently, unset `ui.revsets-use-glob-by-default` now means `true` (rather
than Pyjutsu's former `false`), which can change matches for bare string patterns. Also, rewrites
now enforce `immutable_heads().ancestors()` by default. Use
`transaction(ignore_immutable=True)` only for a deliberate, scoped administrative rewrite; the
root commit remains permanently protected.

## Reads

Reads return frozen Pydantic models and never mutate the repo (no working-copy snapshot):

```python
from pyjutsu import Workspace

ws = Workspace.load("my-repo")

ws.working_copy()                # Commit for @
ws.resolve("trunk()")            # single-revision revset -> Commit
ws.log("trunk()..@", limit=50)   # list[Commit] in revset order
ws.iter_log("::@")               # lazy Iterator[Commit] for huge histories
ws.bookmarks()                   # list[Bookmark] (local + remote-tracking)
ws.operations(limit=20)          # list[Operation] (the op log)
ws.diff_stat("@")                # DiffStat (per-file + total line counts)
ws.diff("@")                     # Diff (name-status + content hunks)
ws.conflicts("@")                # list[Conflict] (first-class, N-sided)

# Time travel: read a historical repo state (writes nothing)
view = ws.at_operation(ws.head_operation())
view.log("::@")                  # every read also lives on a RepoView
```

All reads are also available on a `RepoView` (`ws.head()` for the current state,
`ws.at_operation(op)` for history); the `Workspace` conveniences delegate to a fresh head view.

## Transactions & git

Mutations run in a transaction context manager that publishes exactly one operation on clean exit
and rolls back on any exception; git interop and workspace management live on the `Workspace`:

```python
with ws.transaction("describe @") as tx:
    tx.describe("@", "a better message")

ws.git_fetch("origin")           # fetch + import remote-tracking refs
ws.git_push("origin", "main", allow_new=True)
ws.create_tag("v1.0", "@")       # lightweight jj tag (the default)
ws.push_tag("v1.0", "origin")
ws.undo()                        # revert the head operation
ws.gc()                          # backend GC; publishes no jj operation
```

Pass `message="..."` to retain the annotated Git tag path. That form emits a
`DeprecationWarning` and will move to `ws.git.create_tag` in a later release.
Existing positional message arguments continue to work.

`ws.gc()` mirrors `jj util gc`: it refreshes internal Git keep-refs and preserves objects newer
than two weeks by default. Pass a timezone-aware `datetime` to select another cutoff. Re-adopting
a colocated repository after its `.jj` was deleted leaves obsolete keep-refs in `.git` until this
explicit maintenance call; they are neither imported nor displayed by Pyjutsu.

## Secondary workspaces

Create a workspace on the source workspace's parents, one selected revision, or several parents:

```python
info = ws.add_workspace("../candidate", name="candidate")
info = ws.add_workspace("../candidate", revisions="trunk()")
info = ws.add_workspace("../integration", revisions=["candidate-a", "candidate-b"])
candidate = Workspace.load(info.path)
```

`revisions=None` matches `jj workspace add`: the new `@` is a sibling of the source `@`.
Use `revisions="root()"` for the former Pyjutsu default. Multiple parents use Jujutsu's merged
tree and preserve conflicts. `sparse_patterns` accepts `"copy"`, `"full"`, or `"empty"`.

Each explicit revset must resolve to exactly one commit. This is stricter than the pinned `jj`
0.44 CLI, which accepts one expression that matches several commits. To give the new `@` several
parents, pass several revisions instead of one expression that matches several commits.

Primary and secondary workspaces load the same secure repository configuration. Intentional
workspace configuration remains workspace-specific. Configuration precedence and conditional
path, hostname, and environment scopes match the pinned Jujutsu 0.44 behavior.

## Revset builder

A typed, composable builder renders to jj revset strings (it evaluates nothing) — escaping mirrors
jj's own `escape_string`, so a built query is identical to the hand-written one, without f-string
quoting hazards. It's accepted anywhere a revset string is, and `R.raw(...)` covers anything
unbound:

```python
from pyjutsu import revset as R, Pattern

ws.log(R.author("alice") & R.description("fix"))   # (author(substring:"alice") & description(substring:"fix"))
ws.log(R.range(R.root(), R.working_copy()))        # root()..@
ws.log(R.bookmark("main").descendants())           # main::
ws.log(R.description(Pattern.glob("release-*")))   # explicit pattern kind
```

Pyjutsu evaluates jj-lib revsets without depending on the `jj` command-line crate. It vendors the
pinned jj 0.44 default aliases (`trunk()`, `immutable_heads()`, `mutable()`, and the rest), then
applies your resolved user, repository, and workspace `revset-aliases` configuration above them.
Malformed configured aliases produce a warning and leave unrelated revsets usable. See
[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) §1.

## Escape hatch: `run_jj`

For operations not yet bound, `run_jj` runs the external `jj` binary against the workspace and
returns raw stdout/stderr/exit — it parses nothing into models (that is the point: a labeled exit
from the typed in-process surface). It depends on a `jj` binary on `PATH`, which should match
`pyjutsu.JJ_LIB_TARGET` for fidelity; this is not part of the in-process guarantee.

```python
result = ws.run_jj(["describe", "-m", "msg"])   # JjResult(args, returncode, stdout, stderr)
ws.run_jj(["bad-command"], check=False)         # don't raise on non-zero exit
```

## Hooks

Pre-commit-style hooks around every mutation verb — in-process Python callbacks, zero cost when
none are registered (jj never runs `.git/hooks/*`; this is pyjutsu's own event surface):

```python
@ws.hooks.on("pre-commit")
def check_license(tx, *, paths=None):
    if any("LICENSE" in p for p in paths):
        raise HookAbort("add a license file first")
```

Pre-hooks veto (a transaction rolls back, a push never starts); post-hooks observe and a failure
raises `PostHookError` carrying the published operation id. Every hook runs by default and all
failures are reported (`fail_fast = false`); `on_post_failure = "warn"` downgrades post-hook
failures to a warning. A declarative `.pyjutsu-hooks.toml`
(pre-commit-config style) is auto-loaded by `Workspace.load(...)` when present, and
`run_prek`/`run_pre_commit` adapters delegate to the pre-commit ecosystem. See
[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) §11.

## Async usage

Every `Workspace`/`RepoView`/`Transaction` method releases the GIL while it touches the backend,
so in an asyncio app wrap calls in `asyncio.to_thread(...)` to run them off the event loop:

```python
await asyncio.to_thread(ws.git_fetch, "origin")
```

A native async facade is intentionally not provided — jj's `!Send` transaction model makes one
costly for little gain over `to_thread`.

## Development

Everything runs inside the [devenv](https://devenv.sh) shell, which pins the Rust toolchain,
`maturin`, and the matching `jj` 0.44.0 CLI used for differential tests:

```sh
devenv shell -- devenv tasks run pyjutsu:build   # maturin develop
devenv shell -- devenv tasks run pyjutsu:test    # pytest + cargo test
devenv shell -- devenv tasks run pyjutsu:lint    # ruff + clippy
```
