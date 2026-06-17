# Pyjutsu 0.5.0 — fidelity-completion implementation (kickoff)

You are implementing **Pyjutsu** (`import pyjutsu`): the Pythonic + Pydantic binding to jujutsu's Rust
engine (`jj-lib`) via PyO3/maturin, in-process — no subprocess backend, no text parsing. The surface
through **0.4.0** is feature-complete and green at `v0.4.0` (binds `jj-lib =0.38.0`, `gix =0.78.0`;
pyjutsu is on its own independent semver). Working tree is clean on `main`; 165 tests pass.

## Your job this session
Implement the **three selected 0.5.0 refinements** — each closes a flag deliberately left by 0.4.0,
chosen for high value-to-effort, jj-lib-clean, and differentially testable:

1. **`git_push` gains `--all` / `--tracked`** bulk selection — today it pushes named bookmarks only.
2. **Snapshot honors `snapshot.auto-track`** → `start_tracking_matcher` — today it hardcodes
   "track everything".
3. **Snapshot `base_ignores` gains the global git-excludes layer** (`.git/info/exclude` +
   `core.excludesFile`) — today `empty()`; this completes the gitignore story 0.4.0 started (the
   repo/nested `.gitignore` is already honored by jj's snapshotter — verified).

## Authority order (read before touching code)
1. **`.scratch/projects/05-pyjutsu-0.5.0-fidelity-completion/REFINEMENTS_GUIDE.md`** — the detailed,
   verified plan: per-slice jj-lib APIs (`file:line` into the pinned source), Rust + Python sketches,
   the exact differential tests to add, and the traps (with explicit **VERIFY** markers where a fact
   needs runtime confirmation). **This is your primary spec. Read it in full first.**
2. `docs/PYJUTSU_CONCEPT.md` §5 (surface) + §12 (scope) — the canonical contract.
3. The **0.4.0** guide `.scratch/projects/04-pyjutsu-0.4.0-refinements/REFINEMENTS_GUIDE.md` — the
   immediate parent; these slices continue its slice-2 (push) and slice-4 (snapshot) flags. Mirror
   its patterns and its verify-and-flag discipline.
4. The code: `src/workspace.rs` (`git_push` for slice 1; `snapshot` for slices 2–3),
   `python/pyjutsu/workspace.py` + `python/pyjutsu/_pyjutsu.pyi`, `tests/test_git_net.py`,
   `tests/test_snapshot.py`, `tests/diff/jj_cli.py` (note `JjCli.append_config`, added in 0.4.0),
   `tests/conftest.py`, `src/revset.rs` (the `RepoPathUiConverter` pattern slice 2 reuses),
   `src/errors.rs` (error mappers; slice 2 adds a `FilesetParseError` one).
5. Memory (`.claude/projects/.../memory/`) — `[[pyjutsu-0-4-0-refinements-plan]]` (what 0.4.0 landed +
   the guide divergences it found), `[[m2-slice5-snapshot]]`, `[[m2-slice11-git-net]]`,
   `[[m2-transaction-not-send]]` record the patterns and landmines.

## How to work
- **One slice at a time, in the guide's order (1 → 2 → 3).** Slice 1 is additive and never touches
  the snapshot → tree → commit-id path; **slices 2 and 3 each can move a commit id**, so they are
  fenced last and ship with their own gitignore/auto-track fixtures and explicit tree/commit-id
  parity assertions.
- For each slice: implement Rust → Python facade + `.pyi` → add the differential tests named in the
  guide → **build, run the full suite, lint** → commit on `main`. Keep the suite green at every commit.
- **Honor the guide's VERIFY markers — confirm against the pinned source / CLI before trusting them.**
  Specifically: jj 0.38's exact **`--all`/`--tracked` selection** (deletions? new tracked?) via
  `jj git push --help` + a create/update/delete fixture (slice 1); whether jj-cli parses
  `snapshot.auto-track` with strict `fileset::parse` (slice 2); jj-cli's **`base_ignores` chain order**
  and the **gix `core.excludesFile` accessor** in `gix 0.78` (slice 3). **If any item is more than a
  few lines beyond the guide's estimate, ship the clean half and re-flag the rest** (the guide says
  exactly where this is acceptable: the `--all` deletion/`--tracked` edge cases, and the global
  `core.excludesFile` layer — `.git/info/exclude` alone is the clean, deterministic half). Don't fake it.
- After slice 3, **bump `0.4.0 → 0.5.0`** (`__init__.py`, `Cargo.toml`, `pyproject.toml`; rebuild so
  `Cargo.lock`/`uv.lock` refresh; commit lockfiles; tag `v0.5.0` annotated and push `main` + tag;
  leave `JJ_LIB_TARGET = "0.38.0"`). Update memory with what landed and any verification surprises.

## Non-negotiable constraints
- **Everything through devenv** — `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}`
  (confirmed in `nix/pyjutsu.nix`). **Never** bare `cargo`/`maturin`/`python`/`pytest`/`jj`. The
  pinned `jj` 0.38.0 CLI is the **differential oracle only**.
- **Thin Rust, rich Python:** `_pyjutsu` returns opaque dicts / scalars / `None`; **no jj-lib or gix
  type crosses FFI**. Models/ergonomics/policy stay in pure-Python `pyjutsu`.
- **GIL/`!Send` discipline:** slice 1 is subprocess+network (off the GIL, `!Send` types
  created+dropped in one closure on one thread, fresh loader per git verb); slices 2–3 run inside the
  existing off-GIL `snapshot` span — the cheap fileset/gitignore construction stays on the GIL before
  the working-copy lock (like 0.4.0's `max_new_file_size` read). Don't `drop(view)` (clippy flags
  dropping a reference — 0.4.0 hit this).
- **Pins frozen:** `jj-lib =0.38.0`, `gix =0.78.0`, `JJ_LIB_TARGET =0.38.0`; `Cargo.lock` committed.
- **Independent semver:** this is the `0.5.0` milestone, not a jj-aligned number.
- **No AI attribution** anywhere — commits, PRs, code comments, docs. Omit such trailers entirely.
- **Keep every other flag flagged, not faked:** force-push, `--change`/`-r <rev>` push selection, tag
  fetch (jj #7528), `--all-remotes`, interactive/partial squash, sparse/`-r` workspace, revset/fileset
  builder surfaces, diffs/hunks, async — all remain out of scope this milestone.

**Start by reading `REFINEMENTS_GUIDE.md` in full, then `git status` to confirm a clean `main`, then
implement slice 1. Land each slice green and committed before starting the next.**
