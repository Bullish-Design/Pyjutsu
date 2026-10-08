# Guarded publication: `publish_if` and `pyjutsu publish-if`

Status: pyjutsu 0.23.0, jj-lib 0.44.0.

A guarded call publishes a prepared change as the new working-copy commit. It publishes only if
two things still hold:

- the working-copy commit is the one the caller expected, and
- no other writer moved the operation heads while the call prepared its operation.

If either check fails, the call stops before it moves `@` and before it writes any file. Plain jj
cannot give this. A normal transaction commit updates the operation heads with no comparison.

This page is the contract. Each limit in §7 has a test that shows it.

## 1. The Python API

```python
result = ws.publish_if(
    expected_wc_commit,   # full commit id of the expected @ (40 hex digits; 64 for SHA-256)
    onto,                 # full commit id to build the new @ on
    description,          # the operation description
)
```

Both ids must be full lowercase hex ids. A revset, a prefix, or an uppercase id raises
`ValueError`.

On success the call returns a `PublishResult`:

| Field | Meaning |
| --- | --- |
| `operation` | The publication operation id. |
| `head_operation` | The newest operation id. It differs from `operation` when Git sync added one. |
| `wc_commit` | The new `@`: an empty commit whose only parent is `onto`. |
| `onto`, `expected_wc_commit` | The ids the caller passed. |
| `git_sync` | `synced`, `unchanged`, or `not-colocated`. |
| `git_sync_operation` | The Git sync operation id, or `None`. |

On failure the call raises one of these. All derive from `PyjutsuError`.

| Exception | Meaning | Changed anything? |
| --- | --- | --- |
| `StalePublishError` | A check failed. `reason` is one of the four codes in §3. | No. |
| `PublishError` | The call cannot run: `onto-not-found`, `invalid-commit-id`, or `unsupported-store`. | No. |
| `PublishIncompleteError` | The operation landed, or may have landed, and a later step failed. | **Yes.** Read `recovery`. |
| `ValueError` | An id is not a full lowercase hex id. | No. |

A native failure that is none of these (for example a lock or backend error before publication)
raises the usual `BackendError` or `WorkingCopyError`. It also means nothing landed.

`ws.recover()` finishes a publication that stopped after its operation landed. See §6.

## 2. The command

```text
pyjutsu publish-if --repo PATH --expect-wc FULL_COMMIT_ID --onto FULL_COMMIT_ID -m DESCRIPTION
pyjutsu recover    --repo PATH
```

`python -m pyjutsu` runs the same commands. Use the `pyjutsu` executable when you can: wheel
installs add it to `bin/`.

The output goes to stdout as `key=value` lines. The first line is `result=<kind>`. A value has no
newline. Diagnostics for people go to stderr and are not a stable interface.

`result=published`, exit 0:

```text
result=published
operation=<id>
head_operation=<id>
wc_commit=<id>
onto=<id>
expected_wc_commit=<id>
git_sync=synced|unchanged|not-colocated
git_sync_operation=<id>          (only when git_sync=synced)
```

`result=stale`, exit 1. The call changed nothing visible:

```text
result=stale
reason=commit-moved|dirty-working-copy|stale-working-copy|op-heads-moved
expected_wc_commit=<id>
observed_wc_commit=<id>
onto=<id>
head_operations=<id>[,<id>...]
dirty=0|1
```

`result=incomplete`, exit 2. **The operation landed or may have landed.** Do not treat it as a
refusal:

```text
result=incomplete
reason=published-checkout-failed|published-git-sync-failed|publish-uncertain
stage=checkout|git-sync|publish-uncertain
operation=<id>
expected_wc_commit=<id>
onto=<id>
recovery=pyjutsu recover --repo PATH
```

`result=error`, exit 2. The call refused and changed nothing:

```text
result=error
reason=repo-not-found|workspace-load-failed|onto-not-found|invalid-commit-id|unsupported-store|<exception name>
message=<text>
```

`pyjutsu recover` prints `result=recovered`, `operations=<id>[,<id>...]` (empty when it did
nothing), and `stale=0|1`.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Published, or recovered. |
| 1 | Stale: a check failed and nothing was published. |
| 2 | Infrastructure or configuration failure, or an operation that landed and then failed. Read `result=`. |
| 3 | Usage failure: a missing argument, or an id that is not a full lowercase hex id. |

## 3. Reason codes

| Reason | Meaning | Where the writer's data is |
| --- | --- | --- |
| `commit-moved` | `@` is not the expected commit. A writer committed or moved `@`. | In its commit. Still reachable. |
| `dirty-working-copy` | The disk differs from `@`. A writer edited files without committing. | On disk, unchanged. The next jj command snapshots it. |
| `stale-working-copy` | The working-copy state file names an older operation and a different tree. | On disk, unchanged. Run `ws.recover()` or `jj workspace update-stale`. |
| `op-heads-moved` | Another writer published between the call's load and its head update. | In the other writer's operation. |

The check order is: stale working copy, dirty working copy, expected commit. A dirty disk reports
`dirty-working-copy` even when `@` also moved.

## 4. What the call does

The call holds the working-copy lock from before it reads the repository until after it records
the working copy. jj's lock order is Git import/export lock (colocated repositories only), then
working-copy lock, then operation-heads lock. The call follows that order.

1. Take the Git lock (colocated only), then the working-copy lock. Block until both are free.
2. Load the repository at head. This happens **after** the locks, so every earlier operation is
   visible. If the heads are divergent, loading first publishes jj's usual merge operation.
3. Refuse a stale working copy.
4. Snapshot the disk **in memory only**. Nothing is published and no state file changes. A dirty
   disk is a rejection. Its bytes stay on disk.
5. Compare the working-copy commit with `expected_wc_commit`.
6. Prepare a new empty child of `onto` as `@`. Write the operation without publishing it.
7. Take the operation-heads lock. Compare the heads with the operation loaded in step 2. Update
   the heads only on a match. This is the one publication point.
8. Check out the new `@` into the working copy.
9. In a colocated repository, reset Git `HEAD` and the Git index to the new parent.
10. Record the working copy (`finish`) at the newest operation. Release the locks.

The call never uses `UnpublishedOperation::publish`. That function updates the heads with no
comparison.

### Operations

A publication adds one operation. In a colocated repository, step 9 adds a second operation named
`sync colocated git` when Git `HEAD` moves. Git `HEAD` moves in the normal case, so the usual
colocated result is two operations. `git_sync=unchanged` (one operation) happens when `onto` is the
current parent. A repository that is not colocated adds one operation.

jj needs the second operation because the jj view records Git `HEAD`. The call does not fold Git
sync into the publication operation. Folding it would write Git `HEAD` before the comparison, so a
rejected call would have moved Git. The call keeps the order "compare, then publish, then sync".

### Supported stores

The call needs the simple operation-heads store (`simple_op_heads_store`) and the `local`
working copy. jj-lib's loader already refuses an unknown store type, so a repository with another
type fails to load with `workspace-load-failed`, and the message names the type. The call also
checks both names before it takes a lock, and answers `unsupported-store`.

## 5. Guarantees and failure states

**Atomic point.** The head update is the only point where the call becomes visible. It writes one
file and removes one file under the operation-heads lock. Before it, the call is invisible. After
it, the operation is in the log.

**Rejected call.** A `stale` or `error` result means: no operation of this call is in the log,
`@` is unchanged, and no working-copy file was written or removed. The call leaves harmless
garbage (see §8). One exception is step 2: a call that finds divergent heads publishes jj's merge
operation first. Every jj command does the same.

**Landed call.** After the head update, the later steps are recoverable but not atomic.

| State | What you see | Result | Recovery |
| --- | --- | --- | --- |
| Stale working-copy commit | `@` moved | `stale commit-moved` | None. Rebuild the preview and retry. |
| Stale working-copy state, or direct edit | Disk differs | `stale stale-working-copy` or `dirty-working-copy` | None, or `recover` for stale state. |
| Heads changed by a concurrent writer | Heads moved | `stale op-heads-moved` | None. Retry. |
| Failure before the head update | Lock or backend error | exit 2 `error` or exception | None. Retry. |
| Head update failed part-way | Log may hold the operation | `incomplete publish-uncertain` | `jj op log`, then `recover`. |
| Published, checkout failed | Operation in log; working copy stale | `incomplete checkout` | `recover`. |
| Published, Git sync failed | Operation in log; jj files new; Git lags | `incomplete git-sync` | `recover`. |

Crash states. A kill models process death. It does not model power loss. `flock` locks release
when the process dies, and each test confirms it.

| Crash point | Landed? | State a fresh process sees | Recovery |
| --- | --- | --- | --- |
| While holding the locks, before the operation exists | No | Repository as it was. | None. Retry. |
| After the operation is written, before the head update | No | Repository as it was, plus one unreachable operation in the store. | None. Retry. |
| After the head update, before checkout | Yes | Operation in log. Old files. Working copy stale. Git at the old parent. | `recover` |
| After checkout, before `finish` | Yes | Operation in log. New files. State file old, so stale. | `recover` |
| After Git sync, before `finish` | Yes (two operations) | New files. Git synced. State file old, so stale. | `recover` |
| After `finish` | Yes (two operations) | Complete. | None |

`recover` is the recovery procedure for every landed state. It is idempotent.

Why not `jj workspace update-stale`? After "checkout done, `finish` not run", the stock command
exits 0 but prints `Attempted recovery, but the working copy is not stale`. It also snapshots
first, which leaves a stray visible head. No data is lost, and the final `@` is right. A test
pins this output. `pyjutsu recover` never snapshots, so it leaves no stray head.

## 6. Recovery procedure

When a call returns `result=incomplete`, or a process died during a call:

```text
pyjutsu recover --repo PATH
```

or, in Python, `ws.recover()`. It does two things:

1. If the working copy is stale, check out the current `@` into it (`update_stale`).
2. If the repository is colocated, reset Git `HEAD` and the index (`sync_colocated`).

The command prints the operations it published. It prints `stale=0` when it is done. If the
result was `publish-uncertain`, run `jj op log` first to see whether the publication operation is
in the log. Then run `recover`. Retrying the original call is safe only after `recover` reports
`stale=0` and the caller rebuilds its expectation from the current `@`.

## 7. Known limits

Each limit below has a test.

1. **A direct file write during the checkout phase can be overwritten.** jj's checkout rewrites a
   path without a content check. A writer that edits a path the checkout rewrites, after the head
   update and before the checkout, loses its bytes. They are in no commit. A new file, and an edit
   to a file the checkout does not touch, survive and join `@` at the next snapshot.
   Test: `test_limit_a_direct_write_to_a_path_the_checkout_rewrites_is_overwritten`.
2. **A writer that loaded the old head can publish after the guard and fork the log.** The guard
   compares the heads at one moment. It cannot stop a writer that loaded the head earlier and
   publishes later, because jj never rejects a head update. This includes a jj CLI command.
   In a colocated repository, a `jj new` takes the Git lock a second time before it publishes. If
   the guard holds that lock, the command waits, then publishes on the head it loaded before the
   guard ran. In the race sweep, a `jj new` released 30 ms before the guard ended this way in nine
   of ten runs. In a plain repository the same race is rare. The next jj command merges the fork.
   Both writers' commits and bytes survive. The guard's `@` stays `@`, and Git `HEAD` stays in
   step with it. The late writer's `@` move is lost, and its commit stays visible as an extra
   head. The race sweep records this as `published-late-fork` and checks the end state.
   The guard holds the Git lock on purpose. Without it, a CLI writer that starts after the guard
   can import a half-synchronized Git `HEAD` as a new operation. That was measured: six of ten
   runs at +30 ms.
   Tests: `test_limit_an_operation_loaded_before_the_guard_publishes_later_as_a_fork`, and the
   race sweep `test_race_with_a_jj_cli_writer_ends_in_a_legal_outcome`.
3. **Power-loss durability is unproved.** jj fsyncs only temporary files. It creates the new head
   file with a plain write and syncs no directory. The kill tests model process death only. See
   the evidence note for the trace.
4. **Raw Git bypasses jj's Git lock.** The call holds `.jj/repo/git_import_export.lock` in a
   colocated repository. A raw `git` command does not take it, and runs while the guard holds it.
   Test: `test_limit_raw_git_does_not_take_jjs_git_lock`.
5. **The guard sees the world through `@`.** A bookmark move, an operation in another workspace,
   an ignored file, and an empty directory do not change the working-copy commit. The guard does
   not report them. They lose nothing: bookmarks and other workspaces survive, and ignored files
   are not in the managed tree.
6. **Divergent heads are merged first.** See §4, step 2. Test:
   `test_limit_divergent_heads_are_merged_before_the_guard_compares`.

State these limits when you describe the guarantee. The accurate claim is: under writers that use
jj's locks, a stale call is rejected before `@` moves and before a writer's bytes are overwritten.
The guard does not remove limits 1 to 4.

## 8. Garbage and cleanup

A rejection leaves unreachable data:

- an operation, its view, and its index in the operation store (when the rejection came after step 6);
- a few unreferenced Git objects from the in-memory snapshot (when the working copy was dirty).

None of it is reachable from any operation head or ref. `jj util gc` removes it after its expiry
(two weeks by default; `jj util gc --expire=now` removes it at once, and a test run confirmed this
for the orphan operation). Do not report it as damage.

## 9. Tests and the test-only build

The release wheel contains no hooks. A cargo feature, `test-hooks`, adds named barriers
(`src/test_hooks.rs`) and one switch that removes the head comparison. `devenv tasks run
pyjutsu:build` enables the feature. `pyjutsu:wheel` does not, and its smoke check asserts
`_pyjutsu.has_test_hooks()` is `False`.

| Test file | Proves |
| --- | --- |
| `tests/test_publish_if.py` | The contract: happy path, committed and dirty writers, stale state, input and store errors. |
| `tests/test_publish_cli.py` | Output, exit codes, the console script, and post-publication failures. |
| `tests/test_publish_crash.py` | A kill at each crash point, and recovery in a fresh process. |
| `tests/test_publish_concurrency.py` | Lock blocking, the head comparison and its negative control, the race sweep, and the limits. |
| `src/workspace/publish.rs` (`cargo test`) | The jj-lib behavior the guard relies on. |

The negative control (`test_negative_control_without_the_comparison_a_third_outcome_appears`) is
the only protection against deleting the head comparison as redundant.
