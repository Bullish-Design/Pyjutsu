# Kickoff: pyjutsu Phase 3 conditional publication

Use this prompt from `/home/andrew/Documents/Projects/pyjutsu`.

## Goal and accepted decision

Implement a public, guarded publication API and command in pyjutsu for
CopyRoom. The user approved a pyjutsu dependency on 2026-10-08. The user also
decided that CopyRoom's current D-A publication design is not sufficient as
the final default. A successful guarded call must publish the reviewed
working-copy change only if the expected working-copy commit and operation
heads still match. A stale call must reject before it moves `@` or overwrites
writer bytes.

This session is **Phase 3 in pyjutsu**. Do not edit CopyRoom, Vendomat, or
nix-meta. Do not start distribution or CopyRoom integration (Steps 4 and 5).
Deliver an implementation, tests, API documentation, and a validated release
candidate. State any guarantee that the implementation cannot provide.

## Read and verify first

1. Read this repository's `AGENTS.md`, `.agents/skills/gitman/SKILL.md`,
   `.agents/skills/writing/SKILL.md`, `docs/DEV_GUIDE.md`, and relevant
   package and release instructions. Use the devenv shell for all commands.
   Route all repository version-control actions through gitman. Do not run
   raw `git` or `jj` in this repository. Disposable test repositories may
   invoke the pinned jj binary.
2. Run `devenv shell -- gitman status` before editing. Confirm the current
   lane, remote relation, changed paths, and lockfiles. Start an isolated
   gitman lane on current trunk for Phase 3. This repository had old orphaned
   `003+*` and `004+*` lanes on 2026-10-08. Do not repair or land those as a
   side effect of this task. Preserve any relevant active work.
3. Read the cross-repository design and evidence in CopyRoom:
   - `/home/andrew/Documents/Projects/copyroom/.scratch/projects/28-temporary-workspace-handoff/HANDOFF_DESIGN_2026-10-08.md`,
     especially §§3, 6, 9.2, 13, 14, and 16
   - `/home/andrew/Documents/Projects/copyroom/.scratch/projects/28-temporary-workspace-handoff/IMPLEMENTATION_GUIDE.md`,
     especially Step 3 and the Step 5 consumer contract
   - `/home/andrew/Documents/Projects/copyroom/.scratch/projects/28-temporary-workspace-handoff/evidence/2026-10-08/prototype/full-diff.patch`
   - The adjacent `publish_if_method.rs.txt`, `__main__.py.txt`, prototype
     hooks, crash logs, and sweep results. These are research artifacts.
     The temporary prototype checkout no longer exists.
   - CopyRoom's new `KICKOFF_REMAINING_STEP_2_5.md` and final Step 2.5 evidence,
     if that work has completed when this session starts.
4. Check the current pyjutsu code and primary sources before porting the
   prototype. On 2026-10-08, `Cargo.toml` pinned `jj-lib = "=0.44.0"`,
   `src/transaction.rs` committed through unconditional `tx.commit`, and
   `src/workspace.rs` had `sync_colocated`. `python/pyjutsu/workspace.py`,
   `python/pyjutsu/_pyjutsu.pyi`, `pyproject.toml`, and
   `tests/test_colocated_sync.py` identify the Python API and packaging
   surfaces. There was no public `publish_if`, `StalePublishError`, or CLI.
   Verify that these facts still hold. Do not treat the patch as ready code.

Primary references, checked on 2026-10-08:

- [Jujutsu concurrency model](https://docs.jj-vcs.dev/latest/technical/concurrency/)
  describes operation forks and automatic merge. Inference: normal jj
  transaction commit cannot supply CopyRoom's requested conditional check.
- [jj-lib v0.44.0 operation heads interface](https://github.com/jj-vcs/jj/blob/v0.44.0/lib/src/op_heads_store.rs)
  and [simple store](https://github.com/jj-vcs/jj/blob/v0.44.0/lib/src/simple_op_heads_store.rs)
  define the lock and head update path. Check the exact pinned source before
  choosing APIs. A lock may be optional for other store implementations.
- [pyjutsu upstream](https://github.com/Bullish-Design/Pyjutsu) and its
  [releases](https://github.com/Bullish-Design/Pyjutsu/releases) identify the
  current package and wheel surfaces. Recheck release facts at implementation
  time. Do not claim an API or artifact is available until the new build proves
  it.

## Required API and behavior

Design a small public Python API and a command that CopyRoom can execute by
absolute path. The proposed command contract is:

```text
pyjutsu publish-if --repo PATH --expect-wc FULL_COMMIT_ID --onto FULL_COMMIT_ID -m DESCRIPTION
```

Use full commit IDs; resolve or reject ambiguous revisions. Return a
structured plain-text result with the observed IDs, published operation ID,
and reason code. Keep exit codes `0` success, `1` stale/finding, `2`
infrastructure or configuration failure, and `3` usage failure. Define the
Python result and exception types to expose the same facts. Keep the command
output stable enough for CopyRoom to parse. Add `[project.scripts]` so a real
executable exists. A `python -m pyjutsu` entry point can supplement it, but
cannot replace it.

The guard must take the working-copy lock **before** loading the current
repository state. Hold it through snapshot, validation, operation publication,
checkout, and finish. Check working-copy freshness while holding the lock.
Inspect an in-memory snapshot without publishing the writer's bytes. Reject a
dirty or stale working copy with its bytes intact. Compare the current
working-copy commit to `--expect-wc`. Prepare the child of `--onto`, then write
an unpublished operation. Take the operation-heads lock, compare the actual
heads with the loaded head, and update them only on a match. Keep jj's lock
order: working copy, then operation heads. Check whether the selected store
supports the needed lock; refuse an unsupported store explicitly.

Do not call `UnpublishedOperation::publish()`: it updates operation heads
without this comparison. Consume the unpublished handle through the proper
jj-lib API, then perform the guarded update. Verify all API names and failure
semantics against the pinned jj-lib source. The prototype uses
`tx.write(...)`, `leave_unpublished()`, and `op_heads_store.lock/get/update`.
Do not ship prototype FIFO hooks or `PJ_NO_CAS` runtime switches.

Support colocated repositories. After a successful guarded checkout, ensure
Git `HEAD` and the index agree with the new jj working copy. Inspect the
existing `sync_colocated` path. If synchronization requires a separate
operation or can fail after publication, report the landed operation and a
clear recovery action; never report this state as an unmodified stale refusal.
Check the placement of the synchronization step against the lock and
transaction boundaries. Do not claim a single-operation outcome if a second
operation is required. Add the license file that matches package metadata,
after checking the repository's actual license terms.

## Failure and crash contract

Specify when the call is atomic and when it is only recoverable. Distinguish:

- stale working-copy commit;
- stale working-copy state or direct file edits;
- operation heads changed by a concurrent writer;
- failure before the operation-head update;
- operation published but checkout or Git synchronization failed;
- crash before publication, after operation write, after publication, and
  after checkout but before finish.

For each state, preserve writer bytes and reachable commits where the storage
model allows it. Never turn an uncertain landed state into a clean stale
response. Give a specific recovery procedure and test it. The prototype's
last crash case had an `update-stale` side effect; resolve or document that
behavior. State the known limits: a direct write to a path during checkout
can still be overwritten; a previously loaded lock-free operation can publish
later as a fork; power-loss durability is unproved; raw Git in a colocated
repository may bypass jj's Git import/export lock. Verify each limit before
publishing documentation.

## Tests that must prove the contract

Add focused Rust and Python tests. Use disposable colocated projects with real
jj commands for the boundary cases. At minimum, prove:

1. The happy path makes `@` a clean child of the exact `--onto` commit, with
   matching tree, Git `HEAD`, and index. Count publication operations and
   explain any Git synchronization operation.
2. A committed foreign writer rejects before `@` moves with both the pinned
   jj CLI and the bundled jj-lib version. Its bytes and commit stay reachable.
3. A direct dirty-file writer rejects with bytes unchanged and no published
   guard operation. Include a stale working-copy metadata case.
4. A writer between operation preparation and head update triggers the
   operation-head comparison. Assert no third outcome in a bounded race
   sweep. Keep a test-only negative control proving that the comparison
   matters; do not ship a switch that disables it.
5. A second writer blocks while the guard holds the working-copy lock.
6. Kill the process at each documented crash point. Start a fresh process and
   assert active state, reachable data, operation heads, and recovery text.
7. Test the Python API, installed console script, error reason fields, and
   exit codes. Include invalid IDs, missing repository, unsupported storage,
   and a postpublication failure.

Tests must assert the stated outcome, not only the presence of a commit
object. Never count an uncommitted file that checkout overwrote as preserved.
Use narrowly scoped, test-only barriers. Remove runtime hooks and test debris
from the release artifact.

## Build, review, and handoff

Run the repository's current build, test, and lint gates. The development
guide listed these on 2026-10-08:

```bash
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint
```

Verify the tasks still exist. Build an installable wheel and install it into a
disposable environment. Prove that the `pyjutsu publish-if` executable works
there. Record versions, commands, outcomes, wheel platform tags, and residual
limits in a dated evidence note in this project directory. Keep large
generated artifacts out of version control unless repository policy requires
them. Check gitman status and the change scope before describing and
publishing the lane. Include all relevant active changes, except clear secrets
or local artifacts. Use no author attribution line.

Prepare release notes and a versioned wheel for Step 4. If tagging or
publishing a release needs approval under the active session instructions,
make the complete release candidate reviewable first and ask only at that
final step. Report the lane, commit, gates, artifact location, API contract,
known limits, and the exact Step 4 inputs. Stop before Steps 4 and 5.
