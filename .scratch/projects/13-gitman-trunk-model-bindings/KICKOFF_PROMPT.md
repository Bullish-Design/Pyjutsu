# Kickoff — Project 12 tail + Project 13 analysis (pyjutsu)

> Paste this as the opening message of a **clean session** in
> `/home/andrew/Documents/Projects/Pyjutsu`. It drives two parallel workstreams:
> **(A)** finish the small process tail of project 12 directly, while **(B)** a set of
> subagents investigate jj-lib 0.42 to turn project 13's OVERVIEW into a buildable
> implementation plan. Do A yourself (fast, low-risk); fan B out to subagents (read-only
> source spelunking). Land A while B runs.

---

## 0. Orientation (read first)

**Pyjutsu** is a Pythonic + Pydantic binding to jujutsu's Rust engine (`jj-lib`) via
PyO3/maturin — in-process, no subprocess, no text parsing. jj-lib is hard-pinned `=0.42.0`
(`Cargo.toml:16`). Canonical spec: `docs/PYJUTSU_CONCEPT.md` (§5/§12 body is current; only
its top Status line is stale — see Task A2).

**Current repo state (verified 2026-07-09):**
- On branch `main`, tip `0fe3edf` (merge of PR #1). Working tree clean apart from
  untracked `.scratch/projects/`.
- **pyjutsu 0.9.0 is landed on `main`** — native `tx.split`/`select_tree`, adopt keep-ref
  prune, build-derived version guard. `devenv shell -- python -m pytest tests/ -q` → 224
  green as of the merge.
- Projects 01–11 are all COMPLETE/shipped. Project 12 is a repo-review whose only
  remaining items are the **process tail** in Task A below. **Project 13** (`OVERVIEW.md`,
  dated 2026-07-09) is the real new feature backlog: pyjutsu **0.10.0** bindings that
  unblock gitman's single-local-authored-trunk refactor. It is a scoping doc — **not
  started**.

**Ground rules (do not violate):**
- **devenv for every in-repo command.** `devenv shell -- <cmd>` (tests, python, cargo,
  maturin). Never bare `uv`/`python`/`pytest`/`cargo`.
- **Never run bare `cargo fmt`** — this tree is not fmt-clean under the devenv rustfmt and
  it rewrites unrelated files. Hand-match surrounding style; only fmt files you created if
  you must, and never commit drift in files you didn't touch.
- **VCS:** this checkout is a plain colocated `.git` (gitman/jj live *inside* devenv). Use
  `git` here. Branch off `main` first; commit incrementally. **Do not push, and do not
  push tags, without an explicit ask.** No AI-authorship trailers on commits/PRs/docs.
- **Never run a command that can fail silently** — check exit status, no `|| true` over
  uninspected errors. Treat an empty/"missing" result as a failure to investigate.
- Verify green (`devenv shell -- python -m pytest tests/ -q`) before every commit.

---

## Workstream A — Project 12 tail (do this yourself, first)

Two small, low-risk items. Branch: `chore/12-release-tail` off `main`.

### A1 — Tag the missing releases
The repo tags every release `vX.Y.Z`, but `git tag` stops at **`v0.7.1`** — both
**`v0.8.0`** and **`v0.9.0`** are missing.
- `v0.8.0` version bump commit: **`7c2dc54`** ("Port to jj-lib 0.42.0; bump 0.7.1 -> 0.8.0").
- `v0.9.0` split/version-guard commit: **`0c143a0`** (now on `main`).
- Create **annotated, lightweight-consistent** tags matching the existing convention
  (inspect `git for-each-ref refs/tags` / `git cat-file -t v0.7.1` to see whether prior
  tags are annotated or lightweight, and match that). Tag `v0.8.0` at `7c2dc54` and
  `v0.9.0` at `0c143a0` (or at `main`'s tip if that better matches how earlier bumps were
  tagged — check where `v0.7.1` sits relative to its bump commit and mirror it).
- **Local only. Do NOT push tags** — report them and ask before pushing.

### A2 — Refresh the two stale Status headers (docs-only)
Source must not be touched.
- `README.md:13` — currently `**Status: 0.8.0 — tracks jj-lib 0.42.0.** …` → **0.9.0**, and
  add one line noting the power surface now covers **sub-file `split`/`select_tree`**
  (mirror the existing "Transactions & git" / "Escape hatch: run_jj" prose style).
- `docs/PYJUTSU_CONCEPT.md:3` — the `**Status:**` paragraph still says M1/M2 = 0.2.0/0.3.0
  binding **jj-lib 0.38**. Update to **pyjutsu 0.9.0 / jj-lib 0.42.0** and note the landed
  split surface. **Leave §5/§12 body untouched** (already accurate).

### A3 — Sanity check
- `devenv shell -- python -c "import pyjutsu; print(pyjutsu.__version__, pyjutsu.JJ_VERSION)"`
  → should print `0.9.0 0.42.0`. Confirm no remaining `0.8.0` / `0.38` / `M2 = 0.3.0`
  status claims anywhere (`grep -rn`).
- Commit A1's notes (if any) + A2 docs on the `chore/12-release-tail` branch. **Do not push;
  do not open a PR without an ask.**

**Acceptance:** `v0.8.0` + `v0.9.0` tags exist locally; both Status headers read 0.9.0 /
jj-lib 0.42.0; live import prints 0.9.0; tests still green; nothing pushed.

---

## Workstream B — Project 13 analysis (fan out to subagents, in parallel with A)

**Goal:** answer project 13's three §10 open questions against **jj-lib 0.42.0 source**, so
`OVERVIEW.md` can become a concrete 0.10.0 implementation plan. This is **read-only research
— no code changes.** Read `.scratch/projects/13-gitman-trunk-model-bindings/OVERVIEW.md`
in full first; it defines P1–P5, the proposed APIs, and the gitman symptoms each retires.

**jj-lib 0.42 source is on disk (read-only):**
`/home/andrew/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/jj-lib-0.42.0/`
(also cross-reference pyjutsu's own usage in `src/workspace.rs`, `src/transaction.rs`).

Launch **three investigation subagents concurrently** (one per open question), each
returning a written finding with exact `file:line` citations into jj-lib 0.42 source and a
concrete recommendation. Then synthesize their findings into a plan yourself.

### Subagent B1 — P1: where does jj 0.42 enforce the non-fast-forward push refusal?
- Trace the push path: `jj_lib::git::push_refs` and `GitPushOptions` (and whatever
  `src/workspace.rs:1206` / `python/pyjutsu/workspace.py:172` currently call).
- Determine **exactly where** a non-FF bookmark move is rejected: is it inside `push_refs`
  (togglable via `GitPushOptions` / a force flag), or upstream in the CLI/view layer that
  pyjutsu doesn't touch?
- Establish how the **expected-old-oid (lease)** is supplied to the atomic ref update, and
  what error surfaces on a lease mismatch (so pyjutsu can map it to `GitError`).
- **Deliver:** is P1 a flag-flip on existing options, or must pyjutsu replicate the
  ref-target build without the FF guard? Cite the structs/functions. Note the naming
  decision (`force_with_lease=` flag on `git_push` vs a separate method).

### Subagent B2 — P2: jj-lib 0.42 entry points for `file untrack` + `snapshot.auto-track`
- Find the working-copy/`TreeState` path that `jj file untrack` uses (drop a path's
  file-state within a snapshot, leave the file on disk). Identify the public jj-lib API
  pyjutsu would call from `src/transaction.rs` / `src/workspace.rs`.
- Find the `snapshot.auto-track` **config surface**: how is the fileset read/applied during
  snapshot, and can it be written in-process (config layer), or is the minimum-viable path
  "`untrack_paths` + rely on `.gitignore` so snapshot won't auto-track an ignored path"?
- **Deliver:** the concrete entry points for `untrack_paths`, and a verdict on whether the
  `auto_track_patterns`/`set_auto_track_patterns` writers are needed for gitman or whether
  gitignore-reliance suffices. Cite source.

### Subagent B3 — P3: does `jj_lib::git::reset_head` reset the git index, or HEAD only?
- Read `git::reset_head` in jj-lib 0.42 (pyjutsu calls it at `src/workspace.rs:1067-1079`
  inside `git_export`). Determine precisely whether it writes/updates the **git index** to
  the target tree, or only moves HEAD.
- If HEAD-only: identify how to reset the index from a tree in-process (a jj-lib helper if
  one exists, else `gix` directly — staying inside pyjutsu, no subprocess).
- **Deliver:** verdict — is P3 **real code** (`sync_colocated` must add index reset) or
  just a **verification test** (reset_head already covers it)? Cite source. Note whether an
  explicit idempotent `sync_colocated` is still worth exposing regardless (OVERVIEW §P3
  argues yes).

### B-synthesis (you, after the three return)
Write `.scratch/projects/13-gitman-trunk-model-bindings/PLAN.md` containing:
- A resolved answer to each of the three open questions (with citations).
- A concrete, sequenced 0.10.0 implementation plan for **P1 + P2 + P3** — per item: the
  exact Rust surface (`src/…`) + Python facade (`python/pyjutsu/…`) + `.pyi` + tests
  (in-process probe against a bare origin + colocated repo, per OVERVIEW §9), and the
  specific gitman symptom/RC it retires.
- P4/P5 left as tracked-not-built (per OVERVIEW §8), with the trigger that would promote
  them.
- Any newly discovered blocker (e.g. a behaviour that genuinely needs a jj-lib newer than
  0.42 — flag loudly per OVERVIEW's pin constraint).
- **No source changes in this workstream** — it ends at a plan ready for its own build
  session.

**Acceptance for B:** all three §10 questions answered with jj-lib 0.42 `file:line`
citations; `PLAN.md` written and buildable without further source investigation; scope for
0.10.0 (P1+P2+P3) is unambiguous.

---

## Suggested order of operations
1. Branch `chore/12-release-tail`; kick off the three B subagents (read-only, background).
2. While they run: do A1 (tags) + A2 (docs) + A3 (verify green), commit on the branch.
3. Collect B1/B2/B3 findings; write `13/PLAN.md` (B-synthesis).
4. Report: tags created (unpushed), docs refreshed, and the 0.10.0 plan — then **stop for
   review before building 0.10.0 or pushing anything.**

## References
- `.scratch/projects/13-gitman-trunk-model-bindings/OVERVIEW.md` — the P1–P5 scoping doc.
- `.scratch/projects/12-repo-review-and-catch-up/{OVERVIEW,PLAN}.md` — the review; Task A =
  its Steps 1–2 tail.
- gitman drivers (cross-repo, read-only context): `../gitman/.scratch/projects/
  19-trunk-model-deep-dive/ANALYSIS.md` (ADDENDUM) and `16-local-authored-trunk-model/
  DECISION.md`.
- jj-lib 0.42 source:
  `/home/andrew/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/jj-lib-0.42.0/`.
