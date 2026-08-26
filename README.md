# Pyjutsu

A general-purpose, Pythonic + Pydantic binding to **jujutsu's Rust engine (`jj-lib`)** via
PyO3/maturin — native graph, op-log, working-copy, and conflict access **in-process**, with
no subprocess and no text parsing.

- **Import:** `import pyjutsu`
- **Binds:** jujutsu / `jj-lib` **0.42.0** (pinned in `Cargo.toml` + `devenv.nix`). Pyjutsu is
  versioned on its own cadence, independent of the jj version it binds; `pyjutsu.JJ_VERSION`
  reports the linked jj-lib at runtime.
- **Docs:** [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) (using the library) ·
  [`docs/DEV_GUIDE.md`](docs/DEV_GUIDE.md) (working on it) ·
  [`docs/PYJUTSU_CONCEPT.md`](docs/PYJUTSU_CONCEPT.md) (design spec).

**Status: 0.16.0 — tracks jj-lib 0.42.0.** The reads, transactions/mutations, op-log time travel,
workspaces, and git interop are implemented and differential-tested against the pinned `jj` CLI.

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
```

Pass `message="..."` to retain the annotated Git tag path. That form emits a
`DeprecationWarning` and will move to `ws.git.create_tag` in a later release.
Existing positional message arguments continue to work.

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
0.42 CLI, which accepts one expression that matches several commits. To give the new `@` several
parents, pass several revisions instead of one expression that matches several commits.

Primary and secondary workspaces load the same secure repository configuration. Intentional
workspace configuration remains workspace-specific. Configuration precedence and conditional
path, hostname, and environment scopes match the pinned Jujutsu 0.42 behavior.

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
pinned jj 0.42 default aliases (`trunk()`, `immutable_heads()`, `mutable()`, and the rest), then
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
`maturin`, and the matching `jj` 0.42.0 CLI used for differential tests:

```sh
devenv shell -- devenv tasks run pyjutsu:build   # maturin develop
devenv shell -- devenv tasks run pyjutsu:test    # pytest + cargo test
devenv shell -- devenv tasks run pyjutsu:lint    # ruff + clippy
```
