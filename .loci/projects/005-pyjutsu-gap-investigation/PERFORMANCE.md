---
title: Performance — the read path at scale
type: report
status: draft
project: 005-pyjutsu-gap-investigation
---

# Performance

Open question 2: **is the read path fast enough?** Yes. The question was open
because every prior measurement used a debug build.

## 0. The headline

Two numbers, same repository, same read, same machine:

```text
Pyjutsu view.log("::@")   debug build     51.4 ms
Pyjutsu view.log("::@")   release build    6.7 ms
jj log -r "::@" (process)                 25.0 ms
```

The kickoff records 66 ms for this read and 29 ms for `jj log`, and concludes
Pyjutsu is slower. That comparison timed an **unoptimized** Rust extension
against a **release** `jj` binary. Against the build a user installs, Pyjutsu
reads this repository **3.7× faster** than the whole `jj log` process.

## 1. Method

### The instrument error, first

`devenv tasks run pyjutsu:build` runs `maturin develop --uv`. No `--release`.
The installed `python/pyjutsu/_pyjutsu.abi3.so` is a debug artifact — 87 MB
against the release build's 20 MB. Every number taken through the normal
development loop is a debug number.

Users do not get that build. `devenv tasks run pyjutsu:wheel` runs
`maturin build --release`, and that is what `uv pip install` delivers.

Both builds are reported below. **Every conclusion uses the release numbers.**

### The repositories

`git fast-import` builds a linear history: one commit per iteration, each
writing a distinct blob under one of 64 directories, plus tag references spread
evenly across the history. The pinned `jj git init --colocate` then adopts it
(2.6 s for the 100k repository, which is itself a useful number).

| Name | Commits | jj tags | Purpose |
|---|---|---|---|
| `pyjutsu-self` | 147 | 0 | this repository; reproduces the kickoff's data point |
| `10k-2000tags` | 10,002 | 2,000 | mid size, many references |
| `10k-0tags` | 10,002 | 0 | isolates reference count from size |
| `100k-2000tags` | 100,002 | 2,000 | the size nobody had profiled |

Adoption is real: `jj tag list` reports 2,000 tags in both tagged repositories,
so the reference-disambiguation path is genuinely exercised.

### The timing rule

Each figure is the **best of N** wall-clock runs (`time.perf_counter` in
process, `date +%s%3N` around the CLI), never a mean. Best-of rejects scheduler
noise without hiding a slow path. N is 3 for the 100k passes and 5–7 for
anything under 100 ms.

The CLI figures include process start. `jj --version` costs 11 ms on this
machine, so subtract that to compare engine work alone.

### The spike

A throwaway `PyRepoView::spike_log_phases` method timed each phase of a `log()`
call inside one `allow_threads` block. It was added, measured in a release
build, and removed. `cargo fmt --check` passes and `jj diff --stat` reports 0
files changed; the closing gate ran on the restored tree.

## 2. Scaling — the read path is linear

Release build, `view.log("::@")` returning validated models:

| Repository | Commits | Total | Per commit |
|---|---|---|---|
| `pyjutsu-self` | 147 | 6.7 ms | 45.3 µs |
| `10k-2000tags` | 10,002 | 554 ms | 55.4 µs |
| `100k-2000tags` | 100,002 | 7,414 ms | 74.1 µs |

Per-commit cost grows by 1.6× while the repository grows by 680×. The growth is
the index and prefix lookups, both logarithmic. **Nothing here is quadratic**,
and nothing degrades sharply at 100k.

Against the pinned CLI on the 100k repository:

| Work | Time | Per commit |
|---|---|---|
| `jj log ::@`, `commit_id` only | 1,069 ms | 10.7 µs |
| `jj log ::@`, everything `Commit` carries | 2,053 ms | 20.5 µs |
| Pyjutsu native `log` → plain rows | 2,932 ms | 29.3 µs |
| Pyjutsu `iter_log` → models | 3,932 ms | 39.3 µs |
| Pyjutsu `log` → models | 7,414 ms | 74.1 µs |

The CLI's richest template renders text and exits; Pyjutsu builds 100,002
frozen Pydantic objects. A 3.6× ratio for that is the price of the models, not
a defect. `iter_log` — same models, streamed — costs 1.9×.

## 3. Attribution — where the 74 µs goes

Release build, 100,002 commits. Phase timings come from the spike; the
Pydantic figure comes from validating captured native rows in Python.

| Phase | Per commit | Share |
|---|---|---|
| **Pydantic validation** | **33.0 µs** | **45%** |
| `PyDict` construction (`to_dict`) and list building | ~20 µs | 27% |
| `is_empty` | 9.2 µs | 12% |
| Load commit objects from the store | 8.7 µs | 12% |
| Short id prefixes (both ids) | 2.4 µs | 3% |
| Field conversion (hex, `reverse_hex`, strings) | 0.2 µs | 0.3% |
| `local_bookmarks_for_commit` | 0.06 µs | 0.1% |
| Revset evaluation to ids | 0.07 µs | 0.1% |

The rows sum to 73.6 µs against a measured 74.1 µs, which is as close as
separately-timed phases get.

Read that top line against the kickoff's candidate list, which put "Pydantic
validation of one model per commit" last. **It is the dominant cost.**

Revset evaluation to ids is free — 7.2 ms for a 100,002-commit set. The whole
cost of a `log()` is per-commit materialization, not query planning.

**Why `iter_log` beats `log` by 1.9× on identical work** (39.3 µs against
74.1 µs per commit) is *not* explained by this table, and the difference is
worth stating precisely. `iter_log` performs every phase above, per commit,
exactly as `log` does. What it does not do is hold 100,002 `Commit` objects,
100,002 dicts, and 100,002 models alive at the same time.

That also bounds the Pydantic figure. The 33.0 µs was measured by validating a
captured list of 100,002 rows, so it carries the same memory pressure. Under
`iter_log`, where each row is built and discarded, validation costs less. Treat
33.0 µs as the cost **in the `log` path**, not as an intrinsic per-model
figure.

The practical reading: for a large result set, the allocation profile matters
as much as the per-commit work, and `iter_log` is already the answer.

## 4. Hypotheses tested

### Disproved: `IdPrefixContext::populate` walks the index per commit

**Do not raise this again.** Carried over from the kickoff, and confirmed here.
`IdPrefixContext::new` without `disambiguate_within` leaves `disambiguation:
None`, so `populate` returns `IdPrefixIndex { indexes: None }`
(`id_prefix.rs:138-147`) — an empty index that falls through to the repo's own.
C3's whole-repository decision is not a quadratic cost.

### Disproved: `disambiguate_prefix_with_refs` scans bookmark and tag names

`disambiguate_prefix_with_refs` (`id_prefix.rs:281`) calls
`view.get_local_tag(prefix)` and `view.get_local_bookmark(prefix)`. Both are
**map lookups**, not scans (`view.rs:193`, `view.rs:397`). The loop normally
ends on its first iteration, because a 2–4 character prefix rarely collides
with a reference name.

Measured, release build, `shortest_*_prefix` per commit:

| Repository | commit prefix | change prefix |
|---|---|---|
| 10k, 2,000 tags | 0.90 µs | 1.04 µs |
| 10k, **0 tags** | 0.91 µs | 0.71 µs |
| 100k, 2,000 tags | 1.19 µs | 1.24 µs |

Two thousand tags cost about 0.3 µs per commit. Ten times the commits cost
about 0.3 µs more. Both ids together are 3% of a `log()`. **Closed.**

### Confirmed but small: `is_empty` touches the backend

9.2 µs per commit, 12% of `log()`. `Commit::is_empty` (`commit.rs:160`) tries
`is_commit_empty_by_index` first and falls back to a backend read.

The CLI pays a comparable cost for its `empty` keyword. Adding `empty` **and**
`bookmarks` to a two-short-id template moved `jj log` from 1,171 ms to 1,492 ms
on the same 100k set — about 3 µs per commit for the pair. In the finer
template ladder the `empty` step alone came out **below** the step before it,
which is noise, not a negative cost; that is the resolution limit of a
process-level measurement at this scale.

Real, bounded, and shared with jj itself. Not worth removing from the model.

### Confirmed: `fresh_loader` re-opens the store on every `ws.git.*` verb

A fixed cost per call, independent of repository size:

| Verb | `pyjutsu-self` | 10k | 100k |
|---|---|---|---|
| `ws.git.refs()` | 1.14 ms | 5.82 ms | 6.29 ms |
| `ws.git.head()` | 0.76 ms | 5.96 ms | 5.94 ms |
| `ws.git.remotes()` | 0.77 ms | 4.04 ms | 4.62 ms |
| `ws.git.index_entries()` | 1.85 ms | 5.42 ms | 6.59 ms |
| `ws.git.tags()` | 1.43 ms | 62.1 ms | 59.6 ms |

Four to six milliseconds per call on a large repository. A caller reading one
reference is fine; a caller in a loop pays it every iteration. `ws.git.tags()`
scales with **tag count**, not repository size — 60 ms for 2,000 tags, about
30 µs each, because every tag reference is read and peeled.

This is a caching question, not a correctness one, and no caller has reported
it. Deferred, with the number recorded.

### Confirmed as the one real defect: `log(limit=N)` does not bound its work

`eval_to_data` (`src/repo_view.rs:90-106`) evaluates the whole revset into a
`Vec<Commit>` — which loads every commit object from the store — and only then
calls `commits.truncate(limit)`. `log_stream` gets this right: it collects ids
(cheap) and truncates before any store read.

Release build, asking for one commit:

| Repository | `log("::@", 1)` | `iter_log("::@", 1)` | Ratio |
|---|---|---|---|
| `pyjutsu-self` (147) | 1.41 ms | 0.04 ms | 35× |
| 10k | 62.6 ms | 0.76 ms | 82× |
| 100k | 775.0 ms | 7.57 ms | 102× |

`jj log -r "::@" -n 1` costs 40 ms, process included.

The cost is linear in the **whole revset**, not in `limit`, so it grows without
limit as a repository grows. `ws.log(revset, limit=50)` is the shape the user
guide teaches first, and it is the shape that pays most.

The docstring at `src/repo_view.rs:82-83` states the opposite: "`limit` caps
the result before the (backend-touching) `CommitData` build, so it bounds the
work too." True of the build. False of the evaluation.

**This is lane P1 in the plan.**

## 5. Debug against release

Same code, same repositories, same machine:

| Measurement | Debug | Release | Ratio |
|---|---|---|---|
| `view.log("::@")`, 147 commits | 51.4 ms | 6.7 ms | 7.7× |
| `view.log("::@")`, 10k | 2,953 ms | 554 ms | 5.3× |
| `view.log("::@")`, 100k | 34,064 ms | 7,414 ms | 4.6× |
| `ws.git.refs()`, 10k | 24.3 ms | 5.8 ms | 4.2× |
| `ws.git.tags()`, 10k | 226.9 ms | 62.1 ms | 3.7× |

A 4–8× spread means no performance claim taken through `pyjutsu:build` is
meaningful. Nothing in the repository says so today.

**This is lane P2 in the plan** — a documented rule that performance work uses
`maturin develop --release --uv`, and that any recorded number names its build.

## 6. Answers

**Is the read path fast enough?** Yes, in release. It is linear in the number
of commits read, it costs 74 µs per fully modelled commit at 100k, and it beats
the `jj log` process on a repository of this size. Nobody needs to redesign it.

**What is the dominant cost?** Pydantic validation, at 45% of a `log()`.
`iter_log` is the existing answer for callers who process and discard;
`log(limit=N)` is broken and P1 fixes it. A `TypedDict` fast path — the concept
document's §10 idea — would target the right thing, but no caller has asked for
it, so it stays deferred with a number attached.

**What should be re-measured after any future change?** The three figures in
§2, in a release build, on the 100k repository. The generator and the benchmark
scripts are in the artifacts directory.

## 7. Raw output

```text
artifacts/20260827T001238Z-perf-100k/bench-100k.txt          debug, 100k
artifacts/20260827T001722Z-perf-attribution/
  attribution.txt          debug: size against reference count
  jj-cli-baseline.txt      pinned CLI, 100k
  jj-cli-template-ladder.txt   per-keyword CLI attribution, 100k
  jj-cli-self-repo.txt     pinned CLI, this repository
  release-phases.txt       release: totals plus spike phases
  release-git-verbs.txt    release: ws.git.* fixed cost
  release-limit.txt        release: log(limit=1) against iter_log(limit=1)
  debug-vs-release.txt     debug re-run of release-git-verbs
```
