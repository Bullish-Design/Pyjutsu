---
title: Research report — bounded log reads
type: research
date: 2026-08-26
project: 005-pyjutsu-gap-investigation
---

# Bounded log reads

## Target

Make `RepoView.log(revset, limit)` load no commits after `limit`.
Keep its rows and order identical to `iter_log(revset, limit)`.
Keep `resolve` counting all revset matches.

The work started from `main` at `5f3c236334a5261de1f7f518f1bc8aa727686b49`.
The environment used jj-lib and `jj` 0.44.0, Rust 1.94.1, Python 3.13.13,
and Maturin 1.12.6.

## Baseline and failure boundary

The release extension reproduced the defect on the preserved 100,002-commit
repository. `log("::@", 1)` took 818.74 ms. `iter_log("::@", 1)` took
9.55 ms.

The focused regression hides an old commit object in a temporary repository.
Before the fix, `log("::@", limit=1)` raised `BackendError`. The failure proves
that the limited call loaded a commit after its requested result.

Evidence:

- [before.txt](artifacts/20260827T011206Z-log-limit-fix/before.txt)
- [test-before.log](artifacts/20260827T011206Z-log-limit-fix/test-before.log)
- [build-before.log](artifacts/20260827T011206Z-log-limit-fix/build-before.log)

## Cause

[`eval_to_data`](../../../src/repo_view.rs) called `revset::evaluate` first.
That function resolved every commit ID to a `Commit`. The method truncated the
result only after those store reads.

`log_stream` showed the responsible boundary. It called `evaluate_ids`,
truncated the IDs, and loaded a commit only when the caller requested it.

## Hypotheses

The investigation tested three explanations:

1. Python model validation caused the limited-read delay. Rejected. The native
   handle reproduced the eager work before model validation.
2. Revset evaluation caused the full delay. Rejected. `iter_log` evaluated the
   same revset to IDs in 9.55 ms.
3. `eval_to_data` loaded the complete revset before truncation. Confirmed by
   source inspection, scaling data, and the hidden-object regression.

This was a local ordering defect. No upstream jj-lib change was required.

## Fix

`eval_to_data` now evaluates IDs, truncates them, and loads only the surviving
commits. `resolve` still passes no limit. It therefore loads and counts every
match before it checks for exactly one revision.

The focused suites pass. The post-fix release measurement is 6.67 ms for
`log("::@", 1)` and 7.21 ms for `iter_log("::@", 1)`.

Evidence:

- [after.txt](artifacts/20260827T011206Z-log-limit-fix/after.txt)
- [test-after.log](artifacts/20260827T011206Z-log-limit-fix/test-after.log)
- [build-after.log](artifacts/20260827T011206Z-log-limit-fix/build-after.log)

## Removal and limits

The fix contains no temporary patch. Remove the regression only if `log` no
longer accepts `limit`. Any replacement must preserve bounded commit reads.

The measurement proves `limit=1` on one 100,002-commit fixture and this
repository. It does not prove memory use, other storage backends, or concurrent
read performance. The full verification gate covers functional compatibility.
