# Pyjutsu

A general-purpose, Pythonic + Pydantic binding to **jujutsu's Rust engine (`jj-lib`)** via
PyO3/maturin — native graph, op-log, working-copy, and conflict access **in-process**, with
no subprocess and no text parsing.

- **Import:** `import pyjutsu`
- **Binds:** jujutsu / `jj-lib` **0.38** (pinned in `Cargo.toml` + `devenv.nix`). Pyjutsu is
  versioned on its own cadence, independent of the jj version it binds; `pyjutsu.JJ_VERSION`
  reports the linked jj-lib at runtime.
- **Spec:** see [`docs/PYJUTSU_CONCEPT.md`](docs/PYJUTSU_CONCEPT.md).

**Status: 0.8.0 — tracks jj-lib 0.42.0.** The reads, transactions/mutations, op-log time travel,
workspaces, and git interop are all implemented and differential-tested against the pinned `jj`
CLI. 0.8.0 ports the binding to jj-lib 0.42.0 (the 0.7.0 power-user surface — a revset builder, a
streaming log, and a `run_jj` escape hatch — is unchanged). Still flagged out of scope: a native
async facade, two-revset `diff(from, to)`, word/inline diff, and assorted git/rewrite refinements
(see `docs/PYJUTSU_CONCEPT.md` §12).

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
ws.undo()                        # revert the head operation
```

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

## Escape hatch: `run_jj`

For operations not yet bound, `run_jj` runs the external `jj` binary against the workspace and
returns raw stdout/stderr/exit — it parses nothing into models (that is the point: a labeled exit
from the typed in-process surface). It depends on a `jj` binary on `PATH`, which should match
`pyjutsu.JJ_LIB_TARGET` for fidelity; this is not part of the in-process guarantee.

```python
result = ws.run_jj(["describe", "-m", "msg"])   # JjResult(args, returncode, stdout, stderr)
ws.run_jj(["bad-command"], check=False)         # don't raise on non-zero exit
```

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
