---
title: 006-pyjutsu-diagnostic-logging
type: project
status: active
loci:
  schema: 1
  id: 01a043d4-0af1-7000-bd8a-e1d7aa2d2757
  projects: []
---

Add generic JSONL diagnostic logging to all Pyjutsu operations.

## Objective

Provide opt-in, library-wide diagnostic logging for Pyjutsu callers. Record
calls at the public Python facade, where Pyjutsu can see the method, safe
arguments, result, exception, and duration. The default remains silent. The
environment variable `PYJUTSU_LOG_FILE` enables append-only JSON Lines output.

## Scope

In scope:

- one small logger module with no new dependency;
- append-only JSONL output selected by `PYJUTSU_LOG_FILE`;
- one structured completion or failure event per public operation;
- public operation names, timing, safe context, and jj operation IDs when known;
- logging for reads, mutations, transactions, workspace operations, and Git
  interop;
- tests for disabled logging, append behavior, event shape, and failures;
- user and developer documentation with privacy and deployment guidance.

Out of scope:

- replacing or duplicating jj's operation log;
- changing the public return models to expose diagnostics;
- logging arbitrary file contents, secrets, environment values, tokens, or
  complete command arguments by default;
- acceptance-script-specific logging policy;
- a logging dependency unless the pinned environment proves one necessary.

## Proposed contract

```text
PYJUTSU_LOG_FILE=/absolute/or/relative/path/to/pyjutsu.jsonl
```

When unset or empty, Pyjutsu emits no diagnostic file. When set, Pyjutsu opens
the file in append mode and flushes each event. Each event includes a UTC
timestamp, operation name, status, duration, safe context, and an exception
summary when the call fails. Mutations include the resulting jj operation ID.

One event per completed call answers the practical questions without creating
a tracing system. Do not add start/end pairs, spans, correlation IDs, thread
metadata, or Rust logging in the first implementation. Add one only when a
demonstrated use case requires it.

The logger must create missing parent directories only when that behavior is
explicitly documented. Prefer requiring an existing parent directory so a
misspelled path fails clearly. File-open and write failures must not silently
change repository behavior; define and test whether they raise a Pyjutsu
logging error or disable logging with one warning. Choose one policy before
implementation and document it.

## Implementation plan

### L1 — inventory and event boundary design

1. Map public methods in `Workspace`, `RepoView`, `Transaction`, and `GitView`.
2. Identify the smallest shared Python wrappers that cover those methods.
   Instrument the facade, not every Rust helper.
3. Define one event schema with `timestamp`, `operation`, `status`,
   `duration_ms`, `context`, and optional `operation_id` or `error` fields.
4. Decide which arguments are safe to record. Paths must be normalized only as
   needed for diagnosis. Revision strings may be recorded. File contents,
   credentials, configuration values, and arbitrary hook data must not be.
5. Record the decisions in this project document before coding.

### L2 — Python logging core

1. Add a private logging module under `python/pyjutsu/`.
2. Read `PYJUTSU_LOG_FILE` on first write. Provide a small test reset hook,
   not a general runtime configuration API.
3. Implement append mode, UTF-8 JSONL encoding, immediate flush, and no ANSI.
4. Provide one decorator or context manager for completion or failure events.
   Preserve the original exception and return value.
5. Keep disabled logging close to zero-cost. Define and test one policy for
   logger failures without affecting repository transactions.

### L3 — public facade instrumentation

1. Instrument `Workspace.load` and initialization events.
2. Instrument `RepoView` reads, including bounded and streaming log calls,
   conflict reads, diffs, file reads, and Git-side reads.
3. Instrument transaction entry, mutation calls, commit/finalization,
   rollback, hook aborts, stale-workspace failures, and validation failures.
4. Instrument workspace lifecycle and Git interop calls at the Python boundary.
5. Add jj operation IDs only after native calls return them. Do not infer an
   operation ID for a read.
6. Do not add Rust instrumentation in this phase. Revisit it only if a public
   Python boundary cannot capture a required event.

### L4 — tests and evidence

1. Add unit tests for disabled logging and environment-variable activation.
2. Test append behavior across two independent invocations or logger resets.
3. Test one event sequence for a read and one for a successful mutation.
4. Test failures, hook aborts, stale workspaces, and failed file writes.
5. Test timestamps, operation IDs, repeated processes, and malformed or
   unwritable log paths.
6. Test that sensitive values and file contents do not appear in output.
7. Add a focused JSONL parser test that rejects malformed event lines.
8. Run the full build, lint, Python, Rust, and live workspace gates. Preserve
   raw gate output under this project's ignored `artifacts/` directory.

### L5 — documentation and release readiness

1. Document `PYJUTSU_LOG_FILE` in `docs/USER_GUIDE.md` as an opt-in diagnostic
   facility.
2. Document schema stability, append semantics, rotation responsibility,
   permissions, privacy, and failure behavior.
3. Document the difference between Pyjutsu diagnostic events and jj's operation
   log in `docs/DEV_GUIDE.md`.
4. Add a release note and a small example showing how to inspect events with
   standard JSONL tooling.
5. Verify that logs are ignored and that no generated log enters a commit.

## Definition of done

- `PYJUTSU_LOG_FILE` records every supported public Pyjutsu operation without
  overwriting prior events.
- Logging is silent and low overhead when disabled.
- Every event is valid JSON on one line and can be grouped by process and time.
- Mutation events include jj operation IDs when available.
- Diagnostic logging does not expose secrets or file contents.
- Logging failures have a documented, tested policy.
- The full verification gate and live secondary-workspace acceptance contract
  pass.
- Documentation and type stubs match the shipped behavior.
