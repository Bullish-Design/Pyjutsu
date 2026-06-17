# Pyjutsu 0.4.0 — refinement implementation (kickoff)

You are implementing **Pyjutsu** (`import pyjutsu`): the Pythonic + Pydantic binding to jujutsu's Rust
engine (`jj-lib`) via PyO3/maturin, in-process — no subprocess backend, no text parsing. The v1 surface
(M0–M2) is **feature-complete and green at `0.3.0`** (binds `jj-lib =0.38.0`, `gix =0.78.0`; pyjutsu is
on its own independent semver). Working tree is clean on `main`; 152 tests pass.

## Your job this session

Implement the **four selected 0.4.0 refinements** — each was deliberately *flagged, not faked* during
M2, reviewed, and chosen for being high value-to-effort, jj-lib-clean, and differentially testable:

1. **`tx.rebase` gains `-r` (single-commit reattach) and `-b` (whole-branch)** — today it's `-s` only.
2. **`git_push` gains bookmark deletion + multi-bookmark push** — today one existing bookmark only.
3. **`git_fetch` gains glob / negative bookmark patterns** — today exact names only.
4. **Snapshot fidelity** — real `.gitignore` chain + honor `snapshot.max-new-file-size` from settings,
   replacing the hardcoded `empty()` ignores + 1 MiB cap.

## Authority order (read before touching code)

1. **`.scratch/projects/04-pyjutsu-0.4.0-refinements/REFINEMENTS_GUIDE.md`** — the detailed, verified
   plan: per-slice jj-lib APIs (`file:line` into the pinned source), Rust + Python sketches, the exact
   differential tests to add, and the traps. **This is your primary spec.** Read it in full first.
2. `docs/PYJUTSU_CONCEPT.md` §5 (surface) + §12 (scope) — the canonical contract.
3. The M2 slice guides under `.scratch/projects/03-pyjutsu-m2-mutations/` (esp. SLICE5, SLICE8,
   SLICE10, SLICE11) — where these refinements were originally flagged; the patterns to mirror.
4. The code: `src/transaction.rs` (slice 1), `src/workspace.rs` (slices 2–4),
   `python/pyjutsu/{transaction,workspace}.py` + `_pyjutsu.pyi`, `tests/test_rewrite.py`,
   `tests/test_git_net.py`, `tests/test_snapshot.py`, `tests/conftest.py`, `tests/diff/jj_cli.py`.
5. Memory (`.claude/projects/.../memory/`) — `[[m2-slice5-snapshot]]`,
   `[[m2-slice8-rebase-squash-restore]]`, `[[m2-slice10-git-interop]]`, `[[m2-slice11-git-net]]`,
   `[[m2-transaction-not-send]]` record *why* each was deferred and the landmines.

## How to work

- **One slice at a time, in the guide's order (1 → 2 → 3 → 4).** Slices 1–3 are additive and never
  touch the commit-id path; **slice 4 is fenced last** because it's the only one that can move a commit
  id — it ships with its own gitignore/large-file fixture and explicit tree-id parity assertions.
- For each slice: implement Rust → Python facade + `.pyi` → add the differential tests named in the
  guide → **build, run the full suite, lint** → commit on `main`. Keep the suite green at every commit.
- **Verify the guide's "verify while implementing" notes against the pinned source** before trusting
  them — specifically: the `-b` branch-roots revset (slice 1; ship `-r` first if `-b` is non-trivial),
  jj 0.38's **delete CLI flag** for the push oracle (slice 2), the fetch **union/intersection algebra**
  vs jj-cli (slice 3), and jj-cli's **gitignore chain order** + the `max-new-file-size` parse (slice 4).
  If any item is more than a few lines beyond the guide's estimate, ship the clean half and re-flag the
  rest (the guide says where this is acceptable) — don't fake it.
- After slice 4, **bump `0.3.0 → 0.4.0`** (`__init__.py`, `Cargo.toml`, `pyproject.toml`; rebuild so
  `Cargo.lock`/`uv.lock` refresh; commit lockfiles; leave `JJ_LIB_TARGET = "0.38.0"`). Update memory
  with what landed and any verification surprises.

## Non-negotiable constraints

- **Everything through devenv** — `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` (confirm
  task names in `devenv.nix`). **Never** bare `cargo`/`maturin`/`python`/`pytest`/`jj`. The pinned `jj`
  0.38.0 CLI is the **differential oracle only**.
- **Thin Rust, rich Python:** `_pyjutsu` returns opaque dicts / scalars / `None`; **no jj-lib or gix
  type crosses FFI**. Models/ergonomics/policy stay in pure-Python `pyjutsu`.
- **GIL/`!Send` discipline:** slice 1 is in-`Transaction` graph work (on the GIL); slices 2–3 are
  subprocess+network (off the GIL, `!Send` types created+dropped in one closure on one thread); slice 4
  snapshots off the GIL. Fresh loader per git verb; `rebase_descendants()` after any rewrite; `has_changes()`
  is the no-op signal.
- **Pins frozen:** `jj-lib =0.38.0`, `gix =0.78.0`, `JJ_LIB_TARGET =0.38.0`; `Cargo.lock` committed.
- **Independent semver:** this is the `0.4.0` milestone, not a jj-aligned number.
- **No AI attribution** anywhere — commits, PRs, code comments, docs. Omit such trailers entirely.
- **Keep every other flag flagged, not faked:** interactive rebase/squash, partial squash, tag fetch
  (jj #7528), `--all-remotes`, force-push, `snapshot.auto-track`, workspace `-r`/sparse, revset builder,
  diffs/hunks, async — all remain out of scope this milestone.

**Start by reading `REFINEMENTS_GUIDE.md` in full, then `git status` to confirm a clean `main`, then
implement slice 1. Land each slice green and committed before starting the next.**
