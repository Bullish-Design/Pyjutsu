# Kickoff — Investigate performance-monitoring functionality in fornix

> **How to use:** run this in a session whose working directory is the **fornix**
> repo (`/home/andrew/Documents/Projects/fornix`), inside its `devenv shell`. It is a
> *research + design* task — produce a report and a concrete implementation proposal
> first; do **not** write production code until the design is reviewed.

---

## Context

Fornix (v0.4.1) runs a command in a disposable, contained, reproducible btrfs-CoW copy
of a repo under srt + a cgroup-v2 systemd scope, and emits one `result` JSON line per
box. A prior investigation (evaluating fornix as a live-testing harness for the
`pyjutsu` library) found that **fornix captures no timing or resource-usage data
whatsoever** — the `result` schema is isolation + exit code + diff only. This blocks
using fornix for any performance-sensitive evaluation. Your job is to investigate
closing that gap.

### What the prior read already established (verify, don't re-derive)
- `src/fornix/box.py` — `run_box()` (~line 189) wraps `runner(spec)` with **no clock**.
  The lifecycle is fork → run → diff → [check] → print. There is no start/end capture.
- `src/fornix/models.py` — `Result` (~line 44) has `id, exit_code, diff, touched_files,
  log, check, meta` and **no** duration/CPU/memory field. `schema_version` is `"2"`.
- `src/fornix/limits.py` — the workload runs in a systemd scope named
  `fornix-<id>.scope` (`unit_name`, ~line 91). The scope is started **without
  `--collect`** (see comment ~line 130). `scope_was_killed()` (~line 144) reads **only**
  the `Result=` property (to detect `timeout`/`oom-kill`), then immediately runs
  `systemctl --user reset-failed <unit>` — which **discards** the scope's
  `CPUUsageNSec` / `MemoryPeak` / `MemoryCurrent` accounting before anything can read it.
- `src/fornix/backends/srt.py` — `srt.run` / `_run_teed` stream output and return the
  exit code; they measure nothing.
- srt maps a cgroup kill (OOM/timeout) to rc 137, so kill *classification* already
  exists; resource *accounting* does not.

Treat the above as leads to confirm against the current source, not as ground truth.

---

## Goals of the investigation

Deliver a written report + design proposal answering:

1. **What performance signals can fornix capture, and where?** Enumerate the candidate
   data and the seam each would be captured at:
   - **Wall-clock** per box (and ideally per phase: fork/scaffold, env capture, workload
     run, diff, check) — a clock around the relevant calls in `run_box`.
   - **CPU time** (`CPUUsageNSec`) and **peak memory** (`MemoryPeak`, `MemoryCurrent`)
     from the systemd scope, read via `systemctl --user show fornix-<id>.scope -p ...`
     **before** `reset-failed`.
   - **btrfs fork cost** and **env-capture cost** (`devenv print-dev-env` is known-slow,
     600s timeout) — are these worth timing separately? They dominate small workloads.
   - Anything srt itself exposes (check `srt --help` / its output) about the run.
2. **Which of those are trustworthy under the cgroup + srt design?** Note the
   confounders: `CPUQuota` deliberately throttles (so wall-time measures the throttle,
   not the code unless quota is unset/100%); srt/bwrap adds fixed overhead; the scope
   accounts for *all* processes in the workload, not just the code under test.
3. **Design the schema change.** Propose exact new fields on `Result` (names, types,
   units — prefer explicit units like `wall_ms`, `cpu_ns`, `mem_peak_bytes`), whether
   they're a flat set or a nested `perf: {...}` object, and how to handle "not
   measured" (null vs absent). Bump `schema_version` to `"3"` and state the
   back-compat story (readers of v2, the atomic `write_json`, downstream `jq` in
   `run_suite.py` / `dedupe.py`).
4. **Design the capture code.** Identify the minimal, correct changes:
   - Where to take the wall-clock (respect the "box exits 0 on machinery success,
     workload exit is data" contract — timing must not change control flow or exit
     semantics).
   - How to read scope accounting **before** `reset-failed` without breaking
     `scope_was_killed`'s kill-detection (it currently relies on the unit lingering
     because there's no `--collect`; your read must slot in ahead of the reset). Handle
     the case where the unit already auto-released.
   - The check-path box (`_run_check`) uses an **un-named** scope
     (`systemd_run_prefix` without `sandbox_id`) — decide whether the oracle run is
     measured too, and if so, thread a deterministic unit name so it can be queried.
5. **Compare against the zero-fornix-change alternative.** A workload can already
   self-measure (`time.perf_counter`, `resource.getrusage`, `/usr/bin/time -v`, a
   `perf.json` sidecar in `$FORNIX_RESULTS`). Articulate clearly **what fornix-native
   capture buys over workload self-instrumentation** (aggregate resource envelope,
   language-agnostic, works for non-Python workloads, trustworthy scope-level numbers)
   and what it can't do (per-operation microbenchmarks — those must stay in the
   workload). Recommend where the line should sit.
6. **SINGLE_PATH / philosophy fit.** Fornix is aggressively single-path, no-fallback,
   no-degraded-mode (see `SINGLE_PATH.md`, `BACKEND_DECISION.md`). Decide whether perf
   capture is always-on or gated (env flag like `FORNIX_PERF`?), and justify it against
   that philosophy — e.g. is reading two `systemctl show` properties cheap enough to be
   unconditional? Does a missing value ever become a hard failure, or is perf inherently
   best-effort (unlike delegation, which is required)?

## Constraints & non-goals

- **Do not** turn fornix into a benchmarking framework or add statistical aggregation
  (repetitions, percentiles) — that belongs to a producer/reducer or the workload.
  Fornix's job is to *emit raw per-box measurements*, one number set per box.
- **Do not** break the safe-under-`set -e` contract or the atomic result write.
- **Do not** regress kill-detection (the `reset-failed` timing is load-bearing).
- Keep the change proportional: the prior estimate was ~20 lines across `limits.py`,
  `box.py`, `models.py`. If your design is much larger, say why.

## Method

1. Read the source (confirm every lead above against the current tree; note line drift).
2. Empirically probe systemd scope accounting on this host: start a throwaway
   `systemd-run --user --scope -p MemoryMax=... -- <something>` and confirm which of
   `CPUUsageNSec` / `MemoryPeak` / `MemoryCurrent` are actually populated *after*
   completion and *before* `reset-failed` on this systemd version. Check whether
   `MemoryPeak` requires `MemoryAccounting=yes` / a specific systemd version. Record the
   exact `systemctl show` invocations that work.
3. Check `src/fornix/config.py` (`Settings`, `FORNIX_*` env parsing) for where a perf
   toggle or field would live.
4. Review the existing tests (`tests/test_box.py`, `tests/test_substrate.py`,
   `tests/test_e2e.py`) and the `runner` seam to understand how to unit-test the new
   fields **without** the btrfs/srt substrate (fornix keeps `box` testable via an
   injected fake runner — the perf capture must stay behind a seam so tests don't need a
   real scope).

## Deliverables (write to the fornix repo, e.g. `.scratch/` or a design doc)

1. **Findings** — confirmed capture seams, which signals are trustworthy, host probe
   results (the working `systemctl show` commands + what they returned).
2. **Design proposal** — the exact `Result` schema delta (v3), the capture code plan
   per file with the `reset-failed` ordering spelled out, the always-on-vs-gated
   decision with rationale, and the check-path decision.
3. **Test plan** — how each new field is unit-tested behind the runner seam, plus one
   e2e assertion that a real box emits sane, non-null perf numbers.
4. **Risk register** — throttle confounder, srt/bwrap overhead, scope-accounts-all-PIDs,
   systemd-version dependence of `MemoryPeak`, back-compat for v2 readers.
5. A crisp **recommendation**: ship fornix-native scope accounting (yes/no), and the
   division of labor between fornix-native capture and workload self-instrumentation.

Start with the source read and the host probe, then write the report. Ask before
implementing.
</content>
