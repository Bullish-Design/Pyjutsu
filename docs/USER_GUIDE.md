# Pyjutsu — User Guide

A practical, task-oriented walkthrough of Pyjutsu for people **using** the library in their own
Python code. For the design rationale see [`PYJUTSU_CONCEPT.md`](PYJUTSU_CONCEPT.md); to work on
Pyjutsu itself see [`DEV_GUIDE.md`](DEV_GUIDE.md).

- **Import:** `import pyjutsu`
- **Binds:** jujutsu / `jj-lib` **0.42.0** (pinned), in-process via PyO3 — no subprocess, no text
  parsing.
- **Status:** shipping at `pyjutsu 0.16.0`.

---

## 1. Mental model (read this first)

Pyjutsu is a faithful, un-opinionated binding of **jujutsu's engine**. If you know `jj`, you
already know the concepts; Pyjutsu just gives you typed, in-process objects instead of parsing CLI
text. Four ideas carry the whole API:

1. **A `Workspace` is one working copy.** You load it from a path. The repo behind it (commit
   store + operation log) is shared across all workspaces (jj's answer to git worktrees).
2. **Reads return frozen Pydantic models and never change the repo.** They go through a
   `RepoView` — a snapshot of the repo at one operation. No working-copy snapshot, no new
   operation.
3. **Every mutation happens inside a `transaction(...)` block, and one clean block == exactly one
   jj operation.** Exit the block cleanly → it's published; raise inside → it rolls back entirely.
4. **The operation log is your undo history.** `ws.undo()`, `ws.restore_operation(op)`, and
   `ws.at_operation(op)` (time-travel reads) all work off it.

Revisions are named with **revset strings** — jj's own query language (`"@"`, `"trunk()..@"`,
`"main::"`). Anywhere a revset is accepted you can pass a string or a built `Revset` (see §7).

### 0.16.0 revset and safety changes

Pyjutsu now uses the resolved jj revset configuration and vendors jj 0.42's default aliases, so
`trunk()`, `immutable_heads()`, `immutable()`, `mutable()`, `visible()`, and `hidden()` work
without a `jj-cli` dependency. User, repository, and workspace `[revset-aliases]` configuration
overrides those defaults with jj precedence. An invalid configured alias emits a Python warning at
`Workspace.load()` and does not prevent unrelated revsets from working.

The unset default for `ui.revsets-use-glob-by-default` is now `true`, matching jj 0.42. This is a
behaviour change: bare string patterns can select more revisions than under earlier Pyjutsu
releases. Set that option explicitly to retain the old literal-default behaviour.

History-rewriting verbs now reject every revision in `immutable_heads().ancestors()`, not only the
root. The per-transaction `ignore_immutable=True` escape hatch deliberately bypasses configured
immutability for an administrative operation, but it never permits rewriting the root commit.

```python
import pyjutsu
from pyjutsu import Workspace

ws = Workspace.load("my-repo")            # or Workspace.init(path, colocate=True)
print(pyjutsu.__version__, pyjutsu.JJ_VERSION)   # pyjutsu ver, linked jj-lib ver
```

---

## 2. Getting a `Workspace`

| You want to… | Call |
|---|---|
| Open an existing jj repo | `Workspace.load(path)` |
| Create a new repo (internal git store) | `Workspace.init(path)` |
| Create a colocated repo (shares a `.git`) | `Workspace.init(path, colocate=True)` |
| Adopt an existing `.git` as a jj repo | `Workspace.init(path, colocate=True)` on a dir that already has `.git` |
| Clone a remote git repo | `Workspace.git_clone(url, path)` |

```python
ws = Workspace.load("my-repo")
ws.name            # "default"
ws.root            # Path to the working-copy root
```

`init(colocate=True)` on a directory that already holds a `.git` **adopts** it: existing git
branches become jj bookmarks, `@` becomes an empty child of the imported `HEAD`, and any
uncommitted edits are preserved.

### Add a secondary workspace

`add_workspace()` returns `WorkspaceInfo`. Load its path to obtain a workspace handle.

```python
info = ws.add_workspace(
    "../candidate",
    name="candidate",
    revisions="trunk()",
)
candidate = Workspace.load(info.path)
```

`revisions` accepts a string, a `Revset`, a sequence of either type, or `None`.
With `None`, the new `@` uses the source `@`'s parents and becomes its sibling.
Each explicit revset must resolve to one commit. Several revisions create a merge working-copy
commit with Jujutsu's merged tree. Conflicts remain first-class Jujutsu conflicts.
This rule is stricter than the pinned `jj` 0.42 CLI, which lets `jj workspace add -r 'A|B'` take
one expression that matches two commits. To give the new `@` several parents, pass several
revisions rather than one expression that matches several commits.

Use `revisions="root()"` to request the former root-based behavior.
The `sparse_patterns` setting accepts `"copy"` (default), `"full"`, or `"empty"`.
Registration and initial commit creation publish two operations, as in Jujutsu 0.42.

Pyjutsu validates revision expressions, the name, and the destination before registration.
If later initialization fails, `PartialWorkspaceError` explains how to forget the registration.
Pyjutsu leaves existing files in place.

Workspace loading uses Jujutsu's secure configuration identities.
Primary and secondary workspaces share repository configuration.
Workspace configuration can intentionally differ for each workspace.
Conditional configuration receives canonical repository and workspace paths, hostname, and environment context.

---

## 3. Reading the repo

All reads are available directly on the `Workspace` (each loads a fresh view of the head
operation), or on a `RepoView` you hold and reuse. Reads are **side-effect-free**.

```python
ws.working_copy()                 # Commit for @
ws.resolve("trunk()")             # a single-revision revset -> Commit (errors if 0 or many)
ws.log("trunk()..@", limit=50)    # list[Commit] in revset order
ws.iter_log("::@")                # lazy Iterator[Commit] for huge histories
ws.bookmarks()                    # list[Bookmark] (local + remote-tracking)
ws.operations(limit=20)           # list[Operation] (the op log, newest first)
ws.diff_stat("@")                 # DiffStat (per-file + total line counts)
ws.diff("@")                      # Diff (name-status + content hunks)
ws.conflicts("@")                 # list[Conflict] (first-class, N-sided)
```

### Reuse a view for several reads of the same state

Each shortcut on `Workspace` re-loads the repo at its latest operation (like the CLI does). If
you make several reads of the **same** state, take one view and reuse it:

```python
view = ws.head()                  # RepoView at the current head operation
wc = view.working_copy()
hist = view.log("::@", limit=100)
stat = view.diff_stat(wc.commit_id)
```

### The models you get back

Every model is a frozen Pydantic v2 object (`extra="forbid"`, so a jj-lib shape change would fail
loudly rather than pass silently).

- **`Commit`** — `change_id` (stable across rewrites), `commit_id` (changes on rewrite),
  `description`, `author`/`committer` (`Signature`), `parent_ids`, `is_empty`, `has_conflict`,
  `bookmarks`.
- **`Signature`** — `name`, `email`, tz-aware `timestamp`.
- **`Bookmark`** — `name`, `remote` (`None` for a local bookmark), `target_ids`, `tracked`, and
  a `.conflicted` property (`True` when it points at more than one commit).
- **`Operation`** — `id`, `parent_ids`, `description`, `hostname`/`username`, `is_snapshot`,
  `tags`, `start_time`/`end_time`.
- **`Conflict`** — `path`, `num_sides`, `num_bases` (a plain 3-way conflict is 2 sides / 1 base).
- **`DiffStat`** / **`FileStat`** — per-file and total insertions/deletions.
- **`Diff`** / **`FileChange`** / **`Hunk`** / **`HunkLine`** — the structured diff (§6).
- **`WorkspaceInfo`**, **`Remote`**, **`JjResult`** — workspace, git-remote, and `run_jj` rows.

---

## 4. Making changes: transactions

Every mutation runs inside a `with ws.transaction("<description>") as tx:` block. The block:

- **begins** a transaction on entry (auto-snapshotting a dirty `@` first, matching the CLI),
- **publishes exactly one operation** with your description on a clean exit,
- **rolls back everything** if any exception escapes the block.

You may make several mutations in one block; they land as one atomic operation.

```python
with ws.transaction("start feature") as tx:
    base = ws.resolve("trunk()")
    child = tx.new(parents=[base.change_id])    # new empty commit, @ moves onto it
    tx.describe(child.change_id, "Add feature")
    tx.set_bookmark("feature", child.change_id)

op_id = ws.head_operation()      # the single operation this block produced
```

### The transaction verbs

| Method | Does |
|---|---|
| `tx.new(parents=None)` | Create a commit on `parents` (default: child of `@`); `@` moves onto it. Multiple parents ⇒ a merge. |
| `tx.describe(commit, message)` | Set a commit's description (change id preserved). |
| `tx.edit(commit)` | Move `@` onto an existing commit (no new commit written). |
| `tx.abandon(commit)` | Remove a commit; its children rebase onto its parents. |
| `tx.rebase(commit, onto=..., mode=...)` | Rebase. `mode`: `"source"` (default, `-s`: commit + descendants), `"revision"` (`-r`: only this commit), `"branch"` (`-b`). |
| `tx.squash(source, into, message=None)` | Move `source`'s changes into `into`; `source` is abandoned. |
| `tx.restore(commit, from_=..., paths=None)` | Replace a commit's content (or just `paths`) with another commit's. |
| `tx.split(commit, selection, mode="siblings")` | Split one commit into two by a **hunk-level** selection (§6). Returns `(first, second)`. |
| `tx.select_tree(commit, selection)` | Lower-level: build the tree id for a hunk selection. |
| `tx.create_bookmark(name, commit)` | Create a local bookmark (errors if it exists). |
| `tx.set_bookmark(name, commit)` | Create-or-move a local bookmark. |
| `tx.delete_bookmark(name)` | Delete a local bookmark. |
| `tx.track_bookmark(name, remote)` / `tx.untrack_bookmark(name, remote)` | Track / untrack a remote bookmark. |

Most verbs return the rewritten `Commit` (or `Bookmark`) read back from inside the open
transaction, so you can chain on it.

> **Rules of thumb.** Every `commit`/`source`/`into` argument is a **single-revision** revset
> (errors if it matches zero or many). `describe`, `edit`, `abandon`, `rebase`, `squash`,
> `restore`, and `split` reject configured immutable revisions. For a deliberate administrative
> exception, use `with ws.transaction("…", ignore_immutable=True) as tx:`; the root commit always
> raises `ImmutableCommitError`. Call verbs only *inside* the `with` block — using `tx` after the
> block raises `RuntimeError`.

### One-shot working-copy operations (outside a transaction)

These live on the `Workspace` directly (each publishes its own operation):

```python
ws.snapshot()          # snapshot a dirty @ as a "snapshot working copy" op (or None if clean)
ws.untrack_paths([p])  # stop tracking paths (file stays on disk); gitignore them to make it stick
```

---

## 5. Undo & time travel (the operation log)

```python
ws.undo()                      # revert the head operation (publishes a new "undo" op)
ws.undo("@-")                  # undo a specific operation by id/prefix/expression
ws.restore_operation(op_id)    # reset repo state to what op_id recorded
view = ws.at_operation(op_id)  # READ a historical state (on-disk working copy untouched)
view.log("::@")
```

If an undo/restore moves `@`, the on-disk working copy is checked out to the new `@` for you.
Undoing the repo-initialization operation or a merge operation raises `PyjutsuError`.

### Stale working copies

If another operation moved `@` out from under this working copy, it is **stale**. Mutating or
snapshotting a stale `@` raises `StaleWorkingCopyError`. Reconcile it:

```python
if ws.is_stale():
    ws.update_stale()          # check out the current @ to the on-disk tree
```

---

## 6. Diffs, down to the hunk

`ws.diff("@")` returns a `Diff` — one `FileChange` per changed path:

```python
d = ws.diff("@")
for fc in d.files:
    print(fc.kind, fc.path)                 # "modified src/main.rs", "added README.md", …
    for hunk in fc.hunks:
        for line in hunk.lines:
            print(line.kind, line.content, end="")   # "added"/"removed"
```

- `FileChange.kind` ∈ `added` / `modified` / `removed` / `type_changed` / `renamed` / `copied`.
  (`type_changed` is jj's `M` for a file↔symlink switch; `copied` is rare — jj detects renames,
  not similarity copies.)
- `FileChange.binary` is `True` for non-line-diffable files (binary/symlink/submodule/conflict);
  those carry no `hunks`.
- Hunks have **no surrounding context** — every `HunkLine` is `added` or `removed`. This is a
  faithful structured diff, not a byte-exact `@@` unified-diff header.

### Sub-file `split`

`tx.split` carves one commit's diff into two by selecting **hunks** — the very hunks `diff()`
emits. A `selection` maps each path to `None` (the whole file) or a list of **0-based hunk
indices** into that file's `diff()` output for the same commit:

```python
d = ws.diff("@")                              # inspect to choose hunks
with ws.transaction("split out the docs") as tx:
    first, second = tx.split(
        "@",
        {"README.md": None,        # whole file
         "src/main.rs": [0, 2]},   # only hunks 0 and 2 of this file
        mode="siblings",           # or "stacked" (jj's own `jj split` topology)
    )
```

`first` holds the selected change, `second` the remainder. An empty or full selection raises
`PyjutsuError` (there'd be nothing to carve, or nothing left).

---

## 7. Revsets: strings or the typed builder

Anywhere a revision is accepted you can pass a **revset string** (jj's language, transferred
directly) or build one with the `revset` module + `Pattern`. The builder **renders to a string**
and evaluates nothing — it just removes f-string quoting hazards (values are escaped exactly the
way jj's own `escape_string` does) and adds discoverability.

```python
from pyjutsu import revset as R, Pattern

ws.log(R.author("alice") & R.description("fix"))    # (author(substring:"alice") & description(substring:"fix"))
ws.log(R.range(R.root(), R.working_copy()))         # root()..@
ws.log(R.bookmark("main").descendants())            # main::
ws.log(R.description(Pattern.glob("release-*")))     # explicit pattern kind
ws.log(R.raw("main | tags()"))                       # escape hatch: anything unbound, verbatim
```

- A bare `str` passed to a filter (`author`/`description`/…) is coerced to a **substring** pattern
  (jj's default). Pass an explicit `Pattern.exact(...)`, `Pattern.glob(...)`, `Pattern.regex(...)`
  (and `*_i` case-insensitive variants) for other kinds.
- Operators: `a & b` (∩), `a | b` (∪), `a - b` (difference), `~a` (complement). Methods:
  `.range(b)` → `a..b`, `.dag_range(b)` → `a::b`, `.ancestors()` → `::a`, `.descendants()` → `a::`.
- Constructors include `all_()`, `root()`, `working_copy()`, `commit(id)`, `bookmark(name)`,
  `bookmarks(pat)`, `tags()`, `heads(x)`, `roots(x)`, `parents(x)`, `children(x)`, `latest(x, n)`.

---

## 8. Git interop

```python
ws.git_fetch("origin")                        # fetch + import remote-tracking refs
ws.git_fetch("origin", bookmarks=["feat/*", "~feat/wip"])   # jj string-pattern selection

ws.git_push("origin", "main", allow_new=True) # push a bookmark (create it on the remote)
ws.git_push("origin", ["a", "b"])             # several in one operation
ws.git_push("origin", "old", delete=True)     # delete a bookmark on the remote
ws.git_push("origin", all=True)               # push every local bookmark
ws.git_push("origin", tracked=True)           # push only bookmarks tracking this remote

ws.git_import(); ws.git_export()              # colocated <-> jj sync
ws.sync_colocated()                           # repair colocated git HEAD + index after mutations
```

- **`git_push` is force-with-lease by contract.** A non-fast-forward bookmark move succeeds only
  while the remote-tracking lease holds, and is rejected otherwise (never blindly force-pushed).
- `git_fetch`'s `bookmarks` list uses jj's string patterns: glob-by-default, `kind:` prefixes
  (`exact:`/`glob:`/`substring:`/`regex:` + `-i`), and a leading `~` to negate.
- **Remotes CRUD:** `ws.remotes()`, `ws.add_remote(name, url)`, `ws.remove_remote(name)`,
  `ws.rename_remote(old, new)`, `ws.set_remote_url(name, url)`.

Each `git_*`/mutation method returns the published `Operation`, or `None` when nothing changed.

---

## 9. Async usage

Every `Workspace` / `RepoView` / `Transaction` method releases the GIL while it touches the
backend. There is **no native async facade** (jj's `!Send` transaction model makes one costly for
little gain); in an asyncio app, offload with `asyncio.to_thread`:

```python
await asyncio.to_thread(ws.git_fetch, "origin")
```

Keep writes on one workspace **serialized**. Only `transaction(...)` is guarded against re-entry;
running another mutator concurrently with an open transaction lets both publish operations, which
jj records as divergent (its normal concurrency model — not corruption, but likely a surprise).

---

## 10. Escape hatch: `run_jj`

For anything not yet bound, run the external `jj` binary against the workspace. It returns raw
text and **parses nothing** into models — a deliberate, labeled exit from the typed surface:

```python
r = ws.run_jj(["describe", "-m", "msg"])   # JjResult(args, returncode, stdout, stderr)
ws.run_jj(["bad-command"], check=False)    # don't raise on a non-zero exit
```

`check=True` (default) raises `JjCliError` on a non-zero exit. The binary is resolved from the
`jj_binary=` argument, then `PYJUTSU_JJ`, then `jj` on `PATH`. It should match
`pyjutsu.JJ_LIB_TARGET` for fidelity (check with `ws.jj_version()`); this depends on an external
binary and is **not** part of the in-process guarantee.

---

## 11. Hooks

Pre-commit-style hooks fire around the mutation verbs: **pre-hooks run before an operation and can
veto it; post-hooks observe it after it published.** Hooks are in-process Python callbacks — no
subprocess, no serialization — and cost nothing when none are registered (a registered pre-commit
hook even sees the pending change's file list for free).

jj deliberately never runs git hooks (it passes `--no-verify` to git), so a repo's
`.git/hooks/*` never fire on jj operations — including operations made through pyjutsu. This is
the gap hooks fill: they are pyjutsu's own event points, not git's.

### Registering hooks (imperative)

Every `Workspace` owns a registry (`ws.hooks`); registrations never cross workspaces:

```python
ws.hooks.add("pre-commit", check_license)        # returns fn — composes with decorators

@ws.hooks.on("pre-commit")                        # decorator form
@ws.hooks.on("post-commit")
def check_license(tx, *, paths=None): ...

with ws.hooks("pre-commit", check_license):      # registered for the block only
    ...
```

### Semantics

- A **pre-hook that raises** (`HookAbort` explicitly, or any exception — wrapped) **vetoes** the
  operation. A transaction rolls back and publishes nothing; `git push`/`fetch` never start.
- A **post-hook failure** raises `PostHookError` carrying the published `operation_id`: the
  operation landed, only the hook failed. Distinguish the two — a transaction that raised
  `PostHookError` did commit. Set `ws.hooks.on_post_failure = "warn"` (or `on_post_failure =
  "warn"` in the config) to downgrade post-hook failures to a `UserWarning` instead.
- By default **every hook runs** even after one fails, and all failures are reported before
  aborting (`fail_fast = false`, pre-commit's run-all behavior); set `fail_fast = true` to stop at
  the first failure.
- A `pre-commit` hook receives the **live, open transaction** — it can call `tx.*` methods to
  amend the pending commit before it publishes (no JSON round-trip), and it receives
  `paths=`, the repo-relative paths changed by the pending commit
  (`tx.changed_paths("@")` — available to you too, and it sees in-flight rewrites the
  read surface cannot).

### Events

| Event | Hook called as |
|---|---|
| `pre-commit` | `fn(tx: Transaction, *, paths: list[str] | None = None)` |
| `post-commit` | `fn(operation_id: str, description: str)` |
| `pre-push` / `post-push` | `fn(remote, bookmarks)` / `fn(operation: Operation \| None, remote)` |
| `pre-fetch` / `post-fetch` | `fn(remote, bookmarks: list[str] \| None)` / `fn(operation \| None, remote)` |
| `pre-import` / `post-import` | `fn()` / `fn(operation: Operation \| None)` |
| `pre-export` / `post-export` | `fn()` / `fn(operation: Operation \| None)` |
| `pre-sync` / `post-sync` (`sync_colocated`) | `fn()` / `fn(operation: Operation \| None)` |
| `pre-snapshot` / `post-snapshot` | `fn()` / `fn(operation: Operation \| None)` |
| `pre-untrack` / `post-untrack` | `fn(paths)` / `fn(operation \| None, paths)` |
| `pre-undo` / `post-undo` | `fn(operation: str \| None)` / `fn(operation: Operation)` |
| `pre-restore` / `post-restore` | `fn(operation: str)` / `fn(operation: Operation)` |

The auto-snapshot inside `transaction(auto_snapshot=True)` is tx-internal and does not fire
`pre-`/`post-snapshot`; the tag verbs, remotes CRUD, `add_workspace`/`forget_workspace`, and
`update_stale` have no events.

### Declarative config: `.pyjutsu-hooks.toml`

A pre-commit-config-style file, loaded automatically by `Workspace.load(...)` (the default
`hooks_config="auto"`) when `<repo>/.pyjutsu-hooks.toml` exists — or explicitly with
`ws.load_config_hooks(path)` / `Workspace.load(path, hooks_config="off")`:

```toml
fail_fast = false           # optional: run every hook even after one fails (default)
on_post_failure = "raise"   # optional: "raise" (default) or "warn"

[hooks.pre-commit]
commands = [
  { command = "ruff check --fix", files = "\\.py$", timeout = 120 },   # external program
  { command = ["pytest", "-q"], pass_filenames = false },
  { command = "deploy", env = { DEPLOY_KEY = "..." } },
]
python = ["myapp.hooks:check_license"]      # in-process callable, zero subprocess
adapters = ["run_prek"]                     # delegate to prek / pre-commit

[hooks.post-commit]
commands = [{ command = "notify-ci" }]
```

- `command` is a string (shlex-split, no shell) or an argv list; a non-zero exit vetoes a `pre-*`
  event (or fails a `post-*` one). `files`/`exclude` regexes filter the event's path list
  (pre-commit style — `pre-commit` is the one event that passes paths today) and matching paths
  are appended to the command; `pass_filenames = false` skips the appending; `env` adds
  environment variables to the child process; `timeout` and `cwd` override the process defaults.
- `python` entries are dotted paths (`module:attr`); called in-process with the event's args.
- `adapters` build argv for a pre-commit-compatible binary: `run_prek` / `run_pre_commit`
  (`prek run --all-files` for `pre-commit`, with `--hook-stage post-commit` for post events;
  jj has no git index, so the default is `--all-files`, not "staged").

Validation is strict — an unknown event, key, or bad regex is an error, so a typo fails loudly
rather than silently not running.

### Performance

The registry is a dict lookup over an empty tuple when nothing is registered: no subprocess, no
allocation, no serialization. With no `pre-commit` hooks the pending diff is never computed. Only
declared `commands`/`adapters` cost what their external program costs.

### Recipes

A whole policy in one `.pyjutsu-hooks.toml`, exercising the pieces together:

```toml
fail_fast = true            # stop at the first failure
on_post_failure = "warn"    # a broken notifier must never fail the commit

[hooks.pre-commit]
# Lint the .py files you're about to commit — skipped entirely when none changed.
commands = [
  { command = "ruff check", files = "\\.py$" },
  { command = "cargo fmt --check", files = "\\.rs$", pass_filenames = false },
]
# Veto commits that hand-edit generated/ (see the python hook below).
python = ["myapp.hooks:no_changes_to_generated"]

[hooks.pre-push]
# Run the whole test suite before anything reaches a remote.
commands = [{ command = "pytest -q", pass_filenames = false }]

[hooks.post-commit]
# Notify CI after every commit; failure is a warning, never an error.
python = ["myapp.hooks:notify_ci"]
```

```python
# myapp/hooks.py — the in-process halves, zero subprocess
from pyjutsu import HookAbort


def no_changes_to_generated(tx, *, paths=None):
    if any(p.startswith("generated/") for p in (paths or [])):
        raise HookAbort("edit the source, not generated/ — regenerate instead")


def notify_ci(operation_id, description):
    ...  # post hooks receive the published operation id
```

The same policies work imperatively (e.g. a `pre-push` guard that reads the call's arguments):

```python
@ws.hooks.on("pre-push")
def protect_main(remote, bookmarks):
    if "main" in bookmarks:
        raise HookAbort("pushing main requires a second person")
```

### Ordering & re-loading

- Hooks run in **registration order**. Config hooks are registered when the config loads (at
  `Workspace.load`/`init`/`git_clone`, or `load_config_hooks`), so they run before hooks added
  later imperatively. Within one event, add the policy hooks first if ordering matters.
- `load_config`/`load_config_hooks` **appends** — re-loading the same file registers the hooks
  again (duplicates). Reload after a config edit by clearing first: `ws.hooks.clear()` (all) or
  `ws.hooks.clear("pre-commit")` (one event).

---

## 12. Errors

All in-process errors derive from `PyjutsuError` (import from `pyjutsu` or `pyjutsu.errors`):

| Exception | Raised when |
|---|---|
| `RevsetError` | a revset fails to parse/resolve, or a single-revision revset matches 0 or many |
| `ConflictError` | a conflict blocked an operation |
| `BackendError` | the store/backend reported an error |
| `WorkspaceError` | a workspace couldn't be loaded or is unusable |
| `PartialWorkspaceError` | registration succeeded, but later workspace initialization failed |
| `WorkingCopyError` | the working copy couldn't be locked/snapshotted/checked out |
| `StaleWorkingCopyError` (⊂ `WorkingCopyError`) | `@` is stale — call `update_stale()` |
| `ImmutableCommitError` | you tried to rewrite/abandon an immutable commit (e.g. the root) |
| `GitError` (⊂ `BackendError`) | a git import/export/remote/fetch/push failed |
| `JjCliError` | **only** from `run_jj` — binary not found, or non-zero exit under `check=True` |

---

## 13. What's intentionally out of scope

Pyjutsu binds jj primitives faithfully and stays un-opinionated (no lanes, no workflow policy).
Deliberately **not** provided (see [`PYJUTSU_CONCEPT.md`](PYJUTSU_CONCEPT.md) §12):

- a native async facade (use `asyncio.to_thread`);
- two-revset `diff(from, to)` and word/inline diff;
- interactive/partial selection beyond `split`'s hunk-level carve;
- assorted git/rewrite refinements (force-push flags, `--change`/`-r` push selection, tag fetch).

Reach for `run_jj` when you need something unbound today.
