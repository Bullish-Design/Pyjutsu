# Project 15 — Fornix as a Live-Testing & Evaluation Harness for Pyjutsu

**Status:** Research / investigation (no code written)
**Date:** 2026-07-30
**Author:** research pass over `../fornix` (v0.4.1) and this repo (pyjutsu 0.13.0)

> **Question posed:** can the `fornix` library (a disposable/contained/reproducible
> repo-copy runner in the parent directory) be used for *intensive live testing and
> evaluation* of pyjutsu — thoroughly, rigorously, and with detailed **performance
> data**?

**One-line verdict:** **Yes for isolation, parallelism, and safety; No, out of the
box, for performance data.** Fornix gives cheap btrfs-CoW forks, hard cgroup ceilings,
default-deny network/secrets, and a clean producer→oracle→reducer pipeline that maps
cleanly onto pyjutsu's existing differential-testing model. But fornix captures **zero
timing/resource-usage data** today — every `Result` is isolation + exit-code + diff,
with no duration, CPU-seconds, or peak-memory field. To get the "detailed performance
data" the request asks for, we must add instrumentation (two concrete seams identified
in §6). This document lays out the fit, the gaps, a proposed architecture, and a
phased plan.

---

## 1. Executive summary

| Dimension | Fit | Notes |
|---|---|---|
| Isolation of test runs | **Excellent** | btrfs CoW subvolume per box; srt (bubblewrap) FS/net confinement; each box blind to siblings. |
| Parallel fan-out | **Excellent** | `producer \| parallel fornix box \| reducer`; per-box entropy id; atomic result writes. |
| Resource ceilings | **Excellent** | cgroup-v2 `MemoryMax`/`CPUQuota`/`RuntimeMaxSec`; OOM/timeout *classification* (137). |
| Reproducibility | **Strong** | verbatim `devenv print-dev-env` capture replayed in-sandbox; jj-frozen fork tree. |
| Differential oracle model | **Strong fit** | pyjutsu already diff-tests vs the pinned `jj` CLI — that *is* an oracle; fits `--check`. |
| **Performance measurement** | **Gap** | No timing/CPU/mem in any fornix model. Must instrument (§6). |
| Agent "fix the failing test" loop | **Not our use case** | Fornix's shipped pipeline fixes tests with an agent; pyjutsu eval wants stress/fuzz/diff, not fixes. We use the `box` primitive, not the whole contrib loop. |
| Build cost per box | **Watch item** | `_pyjutsu` is a native maturin extension; each box needs the built `.so` (§5.3). |

**Recommendation:** worth a **bounded pilot** (Phase 1 below) — it is low-commitment
because fornix's `box` is a standalone CLI verb and the substrate is *already
provisioned on this host* (§4). Do not adopt the shipped `fornix-detect-failures /
fornix-test-check` contrib pipeline wholesale — it is a test-*fixing* agent loop; we
want a test-*exercising* harness, which reuses only `fornix box` + a custom oracle.

---

## 2. What fornix actually is (grounding)

Fornix (`../fornix`, v0.4.1, NixOS-only) runs a command in a **disposable, contained,
reproducible copy** of a repo and captures exactly what changed. Five verbs; the one
that matters here is **`box`**:

```
fornix box [--id ID] [--check CMD] [--discard] (-- <cmd…> | --item -)
```

`box` = btrfs CoW fork of `repo-main` + host-captured dev env replayed in-sandbox + srt
sandbox (default-deny egress, secret denial, cgroup limits) + diff capture + optional
`--check` oracle (a *second* sandboxed run on a fresh patched copy the workload never
touched). It **exits 0 on machinery success** — the workload's exit code and the check
verdict are *data* on the emitted `result` JSON, never the process exit. That is what
makes `producer | parallel fornix box` safe under `set -e`.

**The `result` schema fornix owns** (`../fornix/src/fornix/models.py:44`):

```jsonc
{ "id": "...", "exit_code": 0, "diff": "<host path>/patch.diff",
  "touched_files": ["..."], "log": "<host path>/run.log",
  "check": {"cmd": "...", "passed": true}, "meta": { /* echoed from item */ } }
```

Note what is **absent**: any `duration`, `started_at/ended_at`, `cpu_seconds`, or
`peak_memory`. This absence is the crux of §6.

### Lifecycle (from `../fornix/src/fornix/box.py:189` `run_box`)
1. **Fork + scaffold** (`scaffold_sandbox`): `btrfs subvolume snapshot` of repo-main
   (O(metadata), the reason fan-out is cheap), then `jj git init --colocate` + `jj new`
   so `@-` freezes the fork tree and `@` is empty; capture `devenv print-dev-env` →
   `.fornix-env.sh`. Atomic — a scaffolding failure rolls the subvolume back.
2. **Run** the workload argv under srt + a named systemd scope
   (`fornix-<id>.scope`) with the cgroup limits. Output tee'd to `results/<id>/run.log`.
   Exit code captured as *data*.
3. **Diff**: `jj diff -r @ --git` → `patch.diff` (+ `touched-files.txt`), honoring an
   optional `writable_paths` allowlist.
4. **Check** (iff `--check`): fork a *fresh* copy of repo-main the workload never
   touched, `git apply --binary` the patch, run the oracle under srt with
   `network=none`; `passed = (oracle exit 0)`.
5. **Print** one `result` JSON line; optionally `--discard` the subvolumes.

### The contrib pipeline (what we will *not* adopt wholesale)
`fornix-detect-failures | parallel fornix box --check fornix-test-check | fornix-dedupe
| fornix apply -` is an **autonomous test-fix** loop: detect failing tests (JUnit XML),
fan an *agent* (`claude -p "fix the failing tests"`) across clusters, verify each fix
with a rich `accepted|partial|rejected|stale|vacuous` verdict (red-at-base → target →
related → full), dedupe equivalent diffs (Jaccard over hunks), land survivors as jj
changes. **This fixes code; pyjutsu evaluation exercises code.** We reuse the `box`
isolation primitive and the `--check` oracle seam, and write our own producer/oracle.

---

## 3. What pyjutsu testing looks like today (grounding)

- **270 test functions across 44 flat `tests/test_*.py` files.** Run with
  `pytest -q -n auto` (xdist across 8 cores) inside the devenv shell; built with
  `maturin develop --uv`. Tasks: `devenv tasks run pyjutsu:{build,test,lint}`.
- **Differential testing is the core method.** `tests/diff/jj_cli.py` (`JjCli`) shells
  out to the **pinned `jj` 0.42.0** CLI and parses its output into comparable Python
  values; tests apply an operation through *both* pyjutsu and `jj` and assert repo-state
  parity (change graph, commit ids, bookmarks, op-log). Byte-identical commit ids are
  guaranteed by an isolated config with a fixed identity **and a pinned
  `commit-timestamp`** exported via `JJ_CONFIG` into both the subprocess and the
  in-process binding. The standard mutation-test pattern `shutil.copytree`s a fixture
  repo so both engines start byte-identical.
- **Gaps that motivate this project:**
  - **No performance/benchmark tests at all** — no `pytest-benchmark`, no `timeit`, no
    latency surface anywhere in the repo.
  - **No property-based testing** — no `hypothesis`, no `@given`.
  - Everything is subprocess-bound (real `jj`/`git` per test), so the suite is already
    CPU-parallel — but also relatively heavy per test.
- **Public surface to exercise** (from `python/pyjutsu/__init__.py`): `Workspace`
  (lifecycle, git interop, remotes, working-copy/ops, reads), `RepoView`
  (side-effect-free reads), `Transaction` (describe/new/edit/abandon/rebase/squash/
  restore/split/select_tree/bookmark ops), 15 frozen Pydantic models, the `revset`
  builder, and the `run_jj` escape hatch.

**Implication:** pyjutsu's own `JjCli` differential oracle is *exactly* the shape of a
fornix `--check` command. The natural fornix use is not "fix failing tests" but
"generate a large/adversarial space of operation sequences, run each in an isolated
box, and let a differential oracle certify pyjutsu ≡ jj — while measuring latency."

---

## 4. Substrate feasibility on THIS host (verified 2026-07-30)

Fornix is NixOS + btrfs + srt + cgroup-v2 by construction, and `doctor` hard-fails
without them. I probed this machine directly:

| Requirement | State on this host | Verdict |
|---|---|---|
| btrfs work volume `/cortex/fornix` | **Present** — `/dev/loop0` btrfs, `compress=zstd`, `user_subvol_rm_allowed` | ✅ provisioned |
| cgroup-v2 cpu+memory delegation | `cpu io memory pids` delegated to user slice | ✅ |
| Unprivileged userns | `max_user_namespaces = 255736` | ✅ |
| bubblewrap (`bwrap`) | present in `/nix/store` | ✅ |
| `srt` on PATH | **absent in pyjutsu shell** — provided by fornix's devenv (`llm-agents` flake) | ⚠ run from fornix devenv |
| `jj` on PATH | **absent in pyjutsu shell** — pyjutsu's *own* devenv pins jj 0.42.0; fornix's pins its own | ⚠ see §5.4 |
| `fornix` installed | absent | ⚠ `pip/uv` install from `../fornix` or run via its devenv |

**Key finding:** the hard part (btrfs `/cortex/fornix` volume + cgroup delegation) is
**already provisioned**. The remaining pieces (srt/jj/fornix on PATH) come from
fornix's own devenv shell. pyjutsu's repo lives on the `@home` subvolume, a *different*
btrfs filesystem than `/cortex/fornix` — that is fine: fornix creates its own
`repo-main` subvolume under `FORNIX_ROOT=/cortex/fornix` and forks *that*; the source
repo's location is irrelevant to the CoW requirement (which is repo-main↔sandboxes
same-fs, both under `/cortex/fornix`).

**No passwordless sudo** on this host — provisioning-level changes (new btrfs volumes)
would need manual `sudo`, but none are required since the volume already exists.

---

## 5. Design: how to actually wire pyjutsu into fornix

### 5.1 Two usage modes

**Mode A — `box` as a bare isolation+limits harness (recommended first).**
Skip the contrib pipeline entirely. Drive `fornix box` directly with a pyjutsu
workload and read the `result` line:

```bash
fornix box --discard -- \
  python -m pyjutsu_eval.scenario --seed 12345 --ops 200
```

Each box: fresh CoW repo copy, dev env replayed, cgroup-limited, network-denied. The
workload is a Python driver that builds a random-but-seeded sequence of pyjutsu
operations and self-checks against `jj`. Result carries pass/fail via `exit_code` and
whatever we stash in `meta`.

**Mode B — `box --check` with a differential oracle.**
Use the anti-self-report guarantee: the workload mutates a repo with pyjutsu and emits
a patch; the `--check` oracle re-runs the *same* operation sequence via the `jj` CLI in
a fresh copy and asserts the resulting tree/graph matches. `passed` is trustworthy
because the oracle runs in a copy the workload never touched. This mirrors pyjutsu's
existing `_copy_repo` differential pattern, but hardened by fornix's isolation.

Mode A is simpler and sufficient for the first pilot; Mode B adds the strong
"pyjutsu can't fake a pass" property and is the rigorous end state.

### 5.2 The producer (fan-out driver)

A pyjutsu-specific producer emits one `item` (JSON) per scenario, e.g.:
- **Seed sweep:** N seeds × a randomized operation-sequence generator (new/describe/
  edit/abandon/rebase/squash/split/bookmark ops) — a lightweight fuzzer over the
  `Transaction` surface.
- **Matrix sweep:** cross repo shapes (linear, diverged, conflicted, many-bookmark,
  large-history) × operation categories.
- **Scale sweep:** the same operation at 10 / 1k / 100k commits, to produce latency
  curves (this is where performance data comes from).

```
python -m pyjutsu_eval.producer --seeds 500 --shapes all \
  | parallel --pipe -N1 fornix box --check pyjutsu-diff-check --item -    \
  | python -m pyjutsu_eval.reducer > report.jsonl
```

`meta` carries the scenario spec (seed, shape, op list, scale) into the box and back
onto the result — fornix echoes `meta` verbatim, so the reducer reconstructs full
provenance without a side channel.

### 5.3 The build-cost problem (important)

`_pyjutsu` is a **native maturin extension** statically linking all of jj-lib. A box's
replayed dev env provides the *toolchain* but **not the compiled `.so`**. Two options:

1. **Pre-build in `repo-main`** (recommended): run `maturin develop --uv` once in
   fornix's `repo-main` before fan-out; every CoW fork inherits the built extension for
   free (CoW = O(metadata)). Workloads then just `import pyjutsu`. Fastest per box.
   - Caveat: pyjutsu's stale-build tripwire (`__init__.py` asserts `__version__ ==
     pyjutsu_version()`) means repo-main's build must match its source — rebuild
     repo-main whenever we sync new pyjutsu source into it via `fornix apply` or a
     manual refresh.
2. **Build inside each box**: costs a `maturin develop` per box (mold keeps re-link
   ~1s, but a cold build is far more). Only worth it if we are testing the *build*
   itself. Not recommended for the eval loop.

### 5.4 The jj-version subtlety

pyjutsu binds **jj-lib 0.42.0** and diff-tests against the **jj 0.42.0 CLI** (its own
devenv pins this). Fornix's devenv pins *its own* jj for the apply side. For a
differential oracle to be valid, **the `jj` CLI the oracle invokes must be 0.42.0**.
Because the box replays *pyjutsu's* captured dev env (from repo-main, which is a copy of
the pyjutsu repo with its devenv), the in-sandbox `jj` is pyjutsu's pinned 0.42.0 — good.
This must be asserted in the oracle (fail loudly if `jj --version` ≠ 0.42.0), reusing
the existing `tests/test_build.py` pin philosophy.

---

## 6. Performance data — the gap and how to close it

**The gap (confirmed by source read):** fornix records **no** timing or resource-usage
data. `run_box` (`box.py:189`) wraps `runner(spec)` with no clock; `srt.run` measures
nothing; the systemd scope runs **without `--collect`** and `limits.scope_was_killed`
reads *only* the `Result=` property (timeout/oom-kill) then immediately
`systemctl reset-failed`s the unit — discarding the `CPUUsageNSec` / `MemoryPeak` /
`MemoryCurrent` that systemd *did* track. The `Result` schema has no field for any of
it. What you *do* get for free: OOM/timeout classification (rc rewritten to 137).

Two clean seams to add performance data — **prefer (A) for a pilot, plan (B) for rigor:**

**(A) Instrument the workload (no fornix changes).** Wrap the pyjutsu operation in the
driver itself and emit structured timings that ride back on `meta` / a results sidecar:
- In-process: `time.perf_counter_ns()` around each operation, `tracemalloc` /
  `resource.getrusage(RUSAGE_SELF)` for peak RSS and CPU-user/sys time.
- Or wrap the argv with `/usr/bin/time -v` and parse `run.log`.
- Write a `perf.json` into `$FORNIX_RESULTS` (the box exposes `FORNIX_RESULTS`); the
  reducer joins it by box id. This is the fastest path and needs zero fornix patches.
  It gives per-operation latency and peak memory, which is what we actually want (the
  cgroup scope's aggregate is coarser than per-op timing anyway).

**(B) Patch fornix to surface systemd scope accounting (upstream-worthy).** Before
`reset-failed`, read `systemctl --user show fornix-<id>.scope -p
CPUUsageNSec,MemoryPeak,MemoryCurrent` and add `cpu_ns` / `mem_peak` /
`wall_ms` (clock around `runner(spec)`) to `Result` (schema already versioned at "2" →
bump to "3"). The unit name is deterministic, so this is a ~20-line change in
`limits.py` + `box.py` + `models.py`. Worth proposing to fornix upstream since "no perf
data" is a general limitation, not a pyjutsu-specific one.

**Recommended measurement design:** use **(A)** for *per-operation* microbenchmarks
(the real signal — e.g. `ws.log("::@")` latency at 100k commits, `tx.rebase` cost,
snapshot time) and optionally **(B)** for *whole-box* aggregate resource envelopes.
Run each scenario at ≥K repetitions per box and report median + p95 + min (guard
against noise; the cgroup `CPUQuota` deliberately throttles, so pin `CPUQuota=100%` or
unset it for timing boxes to avoid measuring the throttle instead of the code).

---

## 7. Rigor & thoroughness levers fornix unlocks

- **Blast-radius isolation:** a scenario that corrupts a repo or wedges the working
  copy cannot touch repo-main, sibling boxes, or the host — srt `denyRead` covers
  sibling sandboxes + home secrets; writes are allow-only to the sandbox.
- **Determinism harness:** reproduce any failing scenario by re-running its `item` (the
  seed + op list live in `meta`); `fornix run <id> -- ...` re-enters a kept box for
  interactive debugging.
- **Crash/hang classification for free:** OOM and RuntimeMaxSec kills surface as 137,
  so a pyjutsu operation that infinite-loops or blows memory is *detected and
  categorized*, not just "test hung."
- **Scale without fear:** fan out 500+ adversarial scenarios under `parallel -jN`; each
  is CoW-cheap and independently limited. This is far beyond what the current 270-test
  suite exercises.
- **Anti-self-report oracle (Mode B):** the `--check` copy the workload never touched
  makes "pyjutsu ≡ jj" a *trustworthy* verdict, not a self-graded one.

---

## 8. Risks, costs, and open questions

| Risk / cost | Severity | Mitigation |
|---|---|---|
| **No native perf data in fornix** | High (vs the request) | §6 (A) workload instrumentation; optionally (B) patch. |
| Native build per box | Medium | Pre-build in repo-main; CoW inherits `.so` (§5.3). |
| jj-version drift between fornix & pyjutsu devenvs | Medium | Oracle asserts `jj --version == 0.42.0` (§5.4). |
| cgroup `CPUQuota` throttle contaminates timings | Medium | Use dedicated timing boxes with `CPUQuota` unset/100%. |
| Fornix is a *dev* sandbox, not a hostile-code boundary | Low | pyjutsu is cooperating code; threat model matches. |
| `repo-main` must be refreshed to test new pyjutsu source | Low | Script a `sync + maturin develop` refresh step. |
| Fornix/srt/jj only in fornix's devenv shell | Low | Run the pipeline from `../fornix`'s `devenv shell`, or install fornix into pyjutsu's env with srt/jj on PATH. |
| Startup overhead per box (fork + env replay + jj colocate) | Medium | Measure it in Phase 1; amortize by running many ops per box, not one. |

**Open questions to resolve in Phase 1:**
1. Wall-clock cost of one empty `fornix box` (fork + env capture + srt spin-up) — sets
   the floor on fan-out economics. `devenv print-dev-env` has a 600s timeout and can be
   slow cold; is it cached across boxes?
2. Does pre-building `_pyjutsu` in repo-main survive the CoW fork + jj colocation
   cleanly (the exclude patterns keep `.fornix-env.sh`/`.home` out of diffs — does the
   built `.so` under the target tree interfere)?
3. Is fornix's pinned jj compatible, or must the oracle strictly use pyjutsu's 0.42.0?

---

## 9. Proposed phased plan

**Phase 0 — Substrate smoke test (½ day).** From `../fornix` devenv shell: `fornix
doctor`; provision a `repo-main` = pyjutsu clone under `/cortex/fornix`; `maturin
develop` it; run one `fornix box -- python -c "import pyjutsu; print(pyjutsu.JJ_VERSION)"`.
Measure the empty-box overhead (open question 1). **Go/no-go gate.**

**Phase 1 — Mode A pilot (1–2 days).** Write `pyjutsu_eval.scenario` (seeded op-sequence
driver with in-process `perf_counter`/`getrusage` timing → `perf.json`), a tiny
producer (seed sweep), and a reducer that aggregates `result`+`perf.json` into a
latency/pass table. Fan out ~50 scenarios under `parallel`. Deliver the first
performance dataset.

**Phase 2 — Mode B differential oracle (2–3 days).** Write `pyjutsu-diff-check` (the
`--check` command): re-run the scenario's op list via the `jj` CLI in the patched check
copy, assert change-graph/commit-id/bookmark/op-log parity (reuse `JjCli` logic from
`tests/diff/jj_cli.py`), assert jj version. Add the matrix + scale sweeps. This is the
rigorous "pyjutsu ≡ jj under adversarial fan-out, with latency curves" deliverable.

**Phase 3 (optional) — upstream perf patch + CI.** Propose §6(B) to fornix; wrap the
whole thing in a `devenv task` (`pyjutsu:fornix-eval`) that produces a dated report.

---

## 10. Bottom line

Fornix is a **strong, low-commitment fit** as an isolation + parallel-fan-out +
resource-ceiling harness for intensive live testing of pyjutsu, and its differential
`--check` model lines up almost exactly with pyjutsu's existing `JjCli` oracle. The
substrate is already provisioned on this host. The **single real gap versus the
request** is that fornix captures no performance data — closeable with modest workload
instrumentation (§6A) now and an optional ~20-line fornix patch (§6B) later. Recommend
proceeding to a **Phase 0 → Phase 1** bounded pilot before any deeper investment.

---

### Appendix — key source references
- Fornix: `../fornix/src/fornix/{box,models,limits,diff,env}.py`,
  `.../backends/srt.py`, `.../contrib/{detect_failures,test_check,dedupe,run_suite}.py`;
  `../fornix/README.md`, `../fornix/H2_H3_EVAL_GUIDE.md`.
- Pyjutsu: `tests/conftest.py`, `tests/diff/jj_cli.py`, `nix/pyjutsu.nix`,
  `docs/DEV_GUIDE.md` §5, `python/pyjutsu/__init__.py`.
- Substrate probe (this host, 2026-07-30): `/cortex/fornix` btrfs loop volume present;
  cgroup cpu/memory delegated; userns enabled; bwrap present; srt/jj/fornix supplied by
  fornix's devenv.
</content>
</invoke>
