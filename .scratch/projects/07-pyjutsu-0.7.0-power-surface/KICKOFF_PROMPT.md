# Pyjutsu 0.7.0 — power-user surface implementation (kickoff)

You are implementing **Pyjutsu** (`import pyjutsu`): the Pythonic + Pydantic binding to jujutsu's Rust
engine (`jj-lib`) via PyO3/maturin, in-process — no subprocess backend (except the explicit escape
hatch in slice 3), no text parsing. The surface through **0.6.0** is feature-complete and green
(binds `jj-lib =0.38.0`, `gix =0.78.0`; pyjutsu is on its own independent semver). Working tree is
clean on `main` @ `v0.6.0`.

## Your job this session
Implement the **power-user surface** — three items pulled from the concept doc's "Later" bucket (§5
revset builder, §12 revset builder / streaming log / CLI fallback) that make pyjutsu pleasant to
dogfood as a daily driver, built as **three independent slices**:

1. **Revset builder** — a pure-Python `Revset` + `Pattern` API that *renders to* jj revset strings
   (it evaluates nothing), accepted anywhere a revset string is today. Removes f-string quoting
   hazards; escaping mirrors jj's own `escape_string`.
2. **Streaming log** — a lazy `Iterator[Commit]` for huge histories: evaluate the revset to commit
   **ids** eagerly (cheap, off-GIL), build the expensive `Commit` model **one at a time** per
   `__next__` (no self-referential lifetimes). One new `#[pyclass]` iterator.
3. **`run_jj(...)` escape hatch** — run the pinned `jj` **binary** against the workspace for ops not
   yet bound, returning **raw** stdout/stderr/exit. Parses nothing into models. Pure Python.

**Two decisions are already made — do not relitigate them:**
- **Async facade is DEFERRED.** Methods already release the GIL; document the
  `await asyncio.to_thread(ws.method, ...)` pattern (README + a docstring note) and write **no async
  code**. The `!Send` transaction model makes a real async facade costly for little gain.
- **"CLI fallback" = the minimal `run_jj` escape hatch only** — NOT a shipped CLI app (pyjutsu is
  import-only and stays that way), NOT a transparent auto-fallback behind typed methods, NOT a second
  subprocess backend. Those three heavier readings are explicitly out of scope.

## Authority order (read before touching code)
1. **`.scratch/projects/07-pyjutsu-0.7.0-power-surface/IMPLEMENTATION_GUIDE.md`** — the detailed,
   verified plan: per-slice jj-lib APIs (`file:line` into the pinned source), Rust + Python sketches,
   the exact differential tests to add, and the traps (with **VERIFY** markers). **This is your
   primary spec. Read it in full first.**
2. `docs/PYJUTSU_CONCEPT.md` §5 (surface; the revset builder "nicety") + §12 (scope: "Later" —
   revset builder, streaming log, CLI fallback, async) — the canonical contract this fulfills.
3. The code: **`src/revset.rs`** (`evaluate` — the one parse→resolve→evaluate path both the builder's
   output and the stream's iter build on); `src/repo_view.rs` (`eval_to_data`, `log`, `CommitData::build`,
   `store().get_commit` — the model-build pattern slice 2 reuses); `python/pyjutsu/repo_view.py` +
   `workspace.py` (the facade shape + where `str | Revset` coercion goes); `python/pyjutsu/models.py`
   + `_pyjutsu.pyi` + `__init__.py` (model + export pattern); `python/pyjutsu/errors.py` (the
   `PyjutsuError` hierarchy `JjCliError` joins); `tests/conftest.py` (`linear_repo`, `scratch_repo`
   fixtures) + `tests/diff/jj_cli.py` (`JjCli.change_ids` — the log-order oracle).
4. Memory (`.claude/projects/.../memory/`) — `[[pyjutsu-0-6-0-diff-surface]]` (parent milestone +
   slice discipline), `[[m2-transaction-not-send]]` (why async is deferred),
   `[[m1-read-layer-status]]` (the read-layer shape).

## How to work
- **One slice at a time, in the guide's order (1 → 2 → 3).** Slice 1 is pure Python (lowest risk,
  establishes `Revset`/`Pattern` + the `str | Revset` coercion every read accepts); slice 2 adds the
  one bit of FFI (the `PyCommitStream` iterator); slice 3 is a self-contained subprocess helper.
- For each slice: implement → facade + model + (slice 2) `.pyi` + `__init__` export → add the
  differential tests named in the guide → **build, run the full suite, lint** → commit on `main`. Keep
  the suite green at every commit. (Devenv task stdout is swallowed — run `python -m pytest -q` /
  `cargo test` / `ruff` / `clippy` *inside* `devenv shell -- bash -c '…'` to actually see results.)
- **Honor the guide's VERIFY markers** — confirm against the pinned `jj` CLI before trusting them.
  Specifically: that a builder-rendered string with escapes resolves identically to the hand-written
  string (slice 1 quoting), and that `iter_log` order matches `log` and the CLI (slice 2). **If an
  item runs more than a few lines beyond the guide's estimate, ship the clean part and flag the
  rest** — the builder's constructor set is meant to be small-but-representative (with `R.raw(...)`
  always available), not exhaustive; don't gold-plate it.
- After the last slice, **bump `0.6.0 → 0.7.0`** (`__init__.py`, `Cargo.toml`, `pyproject.toml`;
  rebuild so `Cargo.lock`/`uv.lock` refresh; commit lockfiles; tag `v0.7.0` annotated and push `main`
  + tag; leave `JJ_LIB_TARGET = "0.38.0"`). **Also refresh the stale README** (it still claims "M1
  complete; mutations not implemented" — wrong since 0.3.0). Update memory with what landed.

## Non-negotiable constraints
- **Everything through devenv** — `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}`. **Never**
  bare `cargo`/`maturin`/`python`/`pytest`/`jj`. The pinned `jj` 0.38.0 CLI is the **differential
  oracle** (and, in slice 3 only, the escape-hatch target).
- **Thin Rust, rich Python:** `_pyjutsu` returns opaque dicts / scalars / `None`; **no jj-lib type
  crosses FFI**. Slices 1 & 3 are pure Python; slice 2 adds exactly one `#[pyclass]` returning dicts.
- **The builder evaluates nothing** (renders strings; escaping mirrors `escape_string`, dsl_util.rs:440;
  over-parenthesize combinators). **Streaming is iterator-streaming** (eager ids → lazy model build;
  no self-referential lifetimes). **`run_jj` parses nothing** and is clearly labeled an escape hatch.
- **Differential parity:** builder = rendered-string + result parity; stream = `iter_log == log == CLI
  order`; `run_jj` = read-back parity vs the typed surface.
- **Pins frozen:** `jj-lib =0.38.0`, `gix =0.78.0`, `JJ_LIB_TARGET =0.38.0`; `Cargo.lock` committed.
- **Independent semver:** this is the `0.7.0` milestone, not a jj-aligned number.
- **No AI attribution** anywhere — commits, PRs, code comments, docs. Omit such trailers entirely.
- **Keep everything else flagged, not faked:** two-revset `diff(from,to)`, word/inline diff, async
  facade, transparent CLI fallback / subprocess backend / shipped CLI app, true lazy revset
  evaluation, and the whole carried backlog (force-push, `--change` push, tag fetch, `--all-remotes`,
  interactive squash, sparse/`-r` workspace, Windows) — all remain out of scope this milestone.

**Start by reading `IMPLEMENTATION_GUIDE.md` in full, then `git status` to confirm a clean `main`
(with 0.6.0 landed @ `v0.6.0`), then implement slice 1. Land each slice green and committed before
starting the next.**
