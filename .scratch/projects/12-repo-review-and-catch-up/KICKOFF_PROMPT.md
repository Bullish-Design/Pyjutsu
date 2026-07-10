# Kickoff — Pyjutsu catch-up: land 0.9.0, refresh docs, then work the deferred backlog

> Paste this whole file as the opening prompt of a **fresh** session started from the Pyjutsu repo
> root (`~/Documents/Projects/Pyjutsu`). It is self-contained; the two design docs it points to live
> beside it in `.scratch/projects/12-repo-review-and-catch-up/`.

## Purpose & current state

You are working on **Pyjutsu** — a Pythonic + Pydantic binding to jujutsu's Rust engine (`jj-lib`)
via PyO3/maturin: native graph / op-log / working-copy / conflict / git access **in-process**, no
subprocess, no text parsing. It is the low-level foundation that **gitman** (the primary consumer) is
built on; gitman already depends on it in-process (`pyjutsu>=0.8`, imported across 8 modules) and adds
all lane/workflow policy on top. Keep Pyjutsu **un-opinionated** — faithful jj primitives only, no
lane or workflow logic.

**Where things stand (verified 2026-07-01):** the library is **0.9.0**, feature-complete against its
own v1 spec, and **fully green** (`224 passed`). The recent power-surface work has all landed in code:
the v0.7.0 `run_jj` escape hatch, streaming `iter_log`, and revset builder; the 0.8.0 port to jj-lib
0.42.0; and the **0.9.0 native `tx.split` / `tx.select_tree`** (hunk-level, sub-file commit splitting),
adopt-time orphaned-keep-ref pruning, and a build-derived version guard.

**The catch:** 0.9.0 exists **only on branch `feat/11-tx-split`** — it has **not** landed on `main`
and is not released, so the `tx.split` that unblocks gitman's sub-file-split tier isn't consumable
yet. That is the single biggest outstanding item.

## Read these first

- `.scratch/projects/12-repo-review-and-catch-up/OVERVIEW.md` — what Pyjutsu is, the exact current
  API surface, the verified state, and a concept-vs-reality gap table (cites real modules/functions).
- `.scratch/projects/12-repo-review-and-catch-up/PLAN.md` — the sequenced plan you'll execute.
- Skim `docs/PYJUTSU_CONCEPT.md` (the canonical spec — §5 surface, §10 gitman relationship, §12
  scope) and `README.md`. Note both have **stale Status headers** (README says 0.8.0; CONCEPT's
  Status line says M2/0.3.0/jj-lib 0.38) — fixing that is PLAN step 2.

## Your first concrete task

**PLAN step 1 — land 0.9.0 on `main`.** Confirm you're on `feat/11-tx-split` with a clean tree
(`git status`), re-verify green in devenv (`devenv shell -- python -m pytest tests/ -q`), then merge
`feat/11-tx-split` → `main` (fast-forward if possible). Check the repo's tag convention (`git tag`;
older bumps used `vX.Y.Z`) and tag `v0.9.0` if that's the pattern. Then check whether the 0.9.0 wheel
needs to reach gitman's resolver (gitman pins pyjutsu from **vendomat's prebuilt wheelhouse** via
`UV_FIND_LINKS`, per `../gitman/pyproject.toml` — not PyPI); if that step belongs to another repo,
hand it off with a clear note rather than guessing.

**Then** proceed to PLAN step 2 (refresh the stale README + CONCEPT Status headers — docs only) and,
if there's appetite, step 3 (two-revset `diff(from, to)`, the one real read-surface gap left in
CONCEPT §12). Steps 4–5 are optional polish / demand-driven backlog.

## Conventions (non-negotiable — read before running anything)

- **Run every in-repo command inside devenv:** `devenv shell -- <cmd>`. Never bare
  `uv`/`python`/`pytest`/`cargo` — the shell here is fresh and lacks the project env. Build:
  `devenv shell -- maturin develop`. Test: `devenv shell -- python -m pytest tests/ -q`. Lint:
  `devenv shell -- cargo clippy`.
- **Never run bare `cargo fmt`.** The committed Rust tree is **not** fmt-clean under this devenv's
  rustfmt (pre-existing drift); `cargo fmt` rewrites unrelated files and pollutes your diff. Hand-match
  the surrounding style. CI does not gate on `fmt --check`.
- **Route version control through the repo's tooling.** gitman/jj live *inside* devenv; this checkout
  itself is a plain colocated `.git`, so use `git` here. **Branch off `main` first** for any new work;
  **commit incrementally**; **do not push without an explicit ask.**
- **Verify before you commit.** A step isn't done until `devenv shell -- python -m pytest tests/ -q`
  is green and (for Rust changes) `cargo clippy` is clean. Keep your diff to intentional changes only
  (`git diff --stat` — no fmt churn on untouched files).
- **No AI-authorship trailers** in commits, PRs, docs, or comments.
- **jj-lib 0.42 source is unpacked locally** for confirming behaviour rather than trusting prose:
  `~/.cargo/registry/src/index.crates.io-*/jj-lib-0.42.0/src/`.
- When you finish an item, add a short "shipped" note to this project's OVERVIEW/PLAN (as prior
  rounds did in `10-…/issue-overview.md` §P1) so the next session sees an accurate state.
