# Pyjutsu

A general-purpose, Pythonic + Pydantic binding to **jujutsu's Rust engine (`jj-lib`)** via
PyO3/maturin — native graph, op-log, working-copy, and conflict access **in-process**, with
no subprocess and no text parsing.

- **Import:** `import pyjutsu`
- **Binds:** jujutsu / `jj-lib` **0.38** (pinned in `Cargo.toml` + `devenv.nix`). Pyjutsu is
  versioned on its own cadence, independent of the jj version it binds; `pyjutsu.JJ_VERSION`
  reports the linked jj-lib at runtime.
- **Spec:** see [`docs/PYJUTSU_CONCEPT.md`](docs/PYJUTSU_CONCEPT.md).

**Status: M1 (read layer) complete** — a side-effect-free read surface, differential-tested
against the pinned `jj` CLI. Mutations, transactions, and git interop (M2+) are not yet
implemented.

## Reads (M1)

Reads return frozen Pydantic models and never mutate the repo (no working-copy snapshot):

```python
from pyjutsu import Workspace

ws = Workspace.load("my-repo")

ws.working_copy()                # Commit for @
ws.resolve("trunk()")            # single-revision revset -> Commit
ws.log("trunk()..@", limit=50)   # list[Commit] in revset order
ws.bookmarks()                   # list[Bookmark] (local + remote-tracking)
ws.operations(limit=20)          # list[Operation] (the op log)
ws.diff_stat("@")                # DiffStat (per-file + total line counts)
ws.conflicts("@")                # list[Conflict] (first-class, N-sided)

# Time travel: read a historical repo state (writes nothing)
view = ws.at_operation(ws.head_operation())
view.log("::@")                  # every read also lives on a RepoView
```

All reads are also available on a `RepoView` (`ws.head()` for the current state,
`ws.at_operation(op)` for history); the `Workspace` conveniences delegate to a fresh head view.

## Development

Everything runs inside the [devenv](https://devenv.sh) shell, which pins the Rust toolchain,
`maturin`, and the matching `jj` 0.38.0 CLI used for differential tests:

```sh
devenv shell -- devenv tasks run pyjutsu:build   # maturin develop
devenv shell -- devenv tasks run pyjutsu:test    # pytest + cargo test
devenv shell -- devenv tasks run pyjutsu:lint    # ruff + clippy
```
