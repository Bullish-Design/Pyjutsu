# Pyjutsu — implementation kickoff prompt

> Paste this as the first message in a clean session inside the **new, empty Pyjutsu repo**.
> Copy `PYJUTSU_CONCEPT.md` into that repo as `docs/PYJUTSU_CONCEPT.md` first — it is the
> canonical spec. This prompt is the orientation, environment, and first moves on top of it.

---

You are implementing **Pyjutsu** (`import pyjutsu`): a **general-purpose, Pythonic +
Pydantic binding to jujutsu's Rust engine (`jj-lib`) via PyO3/maturin**. It is **not**
gitman-specific — gitman is merely one consumer. **Read `docs/PYJUTSU_CONCEPT.md` first; it
is the authority.** This prompt summarizes the spine and tells you how to start.

## The one-sentence thesis

Python drives jj today by shelling out to the `jj` CLI and parsing template/text output;
Pyjutsu removes that ceiling by binding `jj-lib` **in-process** — native graph/op-log/working-copy
access and **real `jj-lib` transactions** (one atomic operation), with no subprocess and no
text parsing — paying for it with a Rust build and `jj-lib`'s instability, which we tame by
**pinning the jj version** and **differential-testing against the pinned `jj` CLI**.

## Non-negotiable constraints

- **jj-lib via PyO3, in-process. No subprocess backend, no CLI fallback, no compat shim.**
  The existing `../Pyjutsu` CLI-wrapper is being **completely replaced** — **zero
  backwards-compatibility** with its API/models/names/behavior. Design fresh.
- **Pin jj hard.** `Cargo.toml` pins `jj-lib = "=0.38.0"` (commit `Cargo.lock`). `devenv.nix`
  pins the Rust toolchain, `maturin`, **and the matching `jj` 0.38.0 CLI binary** (from
  nixpkgs rev `26eaeac4e409d7b5a6bf6f90a2a2dc223c78d915`, the same pin gitman uses) for
  differential tests. Pyjutsu's version encodes the jj it targets (`pyjutsu 0.38.*` ↔ jj 0.38).
- **Runs only inside a `devenv.sh` shell.** All tooling (cargo, maturin, python, jj, pytest)
  via `devenv shell -- ...`. Never bare host tools.
- **Thin Rust, rich Python.** `_pyjutsu` (Rust) exposes **opaque handles + plain data only**;
  **never leak `jj-lib` types to Python**. All Pydantic models, ergonomics, defaults, typing,
  and the public contract live in pure-Python `pyjutsu`.
- **Faithful, un-opinionated primitives.** No workflow/policy (no lanes, no "frozen trunk").
  Mirror jj. Policy belongs to consumers like gitman.
- **Differential testing against the pinned `jj` CLI is the primary correctness + drift net.**

## Architecture (concept §4) — three layers

```
pyjutsu   (pure Python, PUBLIC)  → Pydantic models, Workspace/Transaction facade, typing, docs
_pyjutsu  (Rust, PyO3, THIN)     → opaque handles (PyWorkspace/PyTransaction) + plain data
jj-lib    (Rust crate, =0.38.0)  → engine: Workspace, ReadonlyRepo/MutableRepo, Commit,
                                    RevsetExpression, Transaction, OpStore/Operation,
                                    LocalWorkingCopy, git backend (gix/git2)
```

- **Stateful things** (`Workspace`, open `Transaction`) cross the FFI as opaque `#[pyclass]`
  handles; **values** (commits, lists, diff stats) cross as **plain dicts/tuples** that the
  Python layer feeds to `Model.model_validate(...)`. Keep the Rust surface tiny.
- jj-lib churn is absorbed in the thin Rust shim → the public Python API stays stable across
  jj upgrades.

## Public API contract (concept §5, §11) — build to this

Object-oriented around `Workspace`; reads return Pydantic models; mutations run inside a
transaction that maps to **exactly one jj operation**.

```python
ws = Workspace.load(path)                       # or Workspace.init(path, colocate=True) / .clone(...)
ws.working_copy(); ws.resolve("trunk()")        # -> Commit
ws.log("trunk()..@", limit=50)                  # -> list[Commit]; revset string is jj's own
ws.bookmarks(); ws.operations(limit=20); ws.diff_stat(id)
with ws.transaction("msg") as tx:               # == 1 jj operation, atomic
    c = tx.new(parents=[...]); tx.describe(c, "..."); tx.set_bookmark("feat", c)
ws.undo(); ws.restore_operation(op); ws.at_operation(op)
ws.git_fetch(remote="origin"); ws.git_push(bookmark="feat", remote="origin", allow_new=True)
```

- **Workspaces (jj's worktrees, concept §11):** `ws.workspaces()` lists ALL (from the shared
  view); `ws.working_copy()` is this handle's `@`. **`add_workspace(path, name, at=...)` is
  eager** — it does the record-creation transaction **and** the checkout inside the call and
  returns a ready handle (errors if `path` is non-empty). `forget_workspace(name)` removes the
  record, not the directory. **Stale-`@` detection is mandatory:** `is_stale()` /
  `update_stale()`; never mutate a stale working copy silently.
- **Models (Pydantic v2):** `Commit`, `ChangeId`/`CommitId`, `Bookmark`, `Operation`,
  `Conflict` (N-sided/`Merge`, faithful — not a bool), `DiffStat`, `Signature`,
  `WorkspaceInfo`.

## Environment / build setup (do this in M0)

- **Reuse gitman's devenv shape** (rolling nixpkgs, `languages.python` 3.13 + venv + uv with
  `uv.sync.enable`, a reusable `nix/pyjutsu.nix` task module, quiet non-interactive
  `enterShell`). Ask for gitman's `devenv.nix`/`nix/gitman.nix` if not to hand. **Add**: a
  Rust toolchain (`languages.rust.enable` or fenix), `maturin`, and the pinned `jj` 0.38.0
  binary (the `nixpkgs-jj` input at the rev above).
- **maturin mixed layout:** `pyproject.toml` with `[build-system] requires=["maturin>=1.5"],
  build-backend="maturin"`; `Cargo.toml` (crate `_pyjutsu`, `pyo3` with `abi3-py313`,
  `jj-lib = "=0.38.0"`); `src/lib.rs` (Rust ext); `python/pyjutsu/` (pure-Python package).
- Dev verification: `pyjutsu:build` (maturin develop), `pyjutsu:test` (pytest +
  `cargo test`), `pyjutsu:lint` (ruff + clippy), `enterTest` = build + both test suites.

## How to work

1. **Plan first.** Read the concept, propose a milestone plan + repo skeleton, and **confirm
   before writing implementation code**.
2. **Spike the binding risk before anything else** (this is M0's heart). Prove the single
   riskiest slice end to end: from Python, `import pyjutsu; Workspace.load(path)` a real
   colocated repo via jj-lib and read `@`'s `change_id` / `commit_id` / `description`. This
   one slice validates: the maturin build, the `jj-lib = 0.38.0` pin actually compiling, the
   PyO3 opaque-handle model, and the `Arc`/`Send`/lifetime constraints. If jj-lib 0.38 won't
   build cleanly under the pinned toolchain, surface that immediately — it gates everything.
3. **Build vertically with a differential test each step:** one read (`log(revset)` →
   `list[Commit]`) compared against the pinned `jj log`; then one transaction (`describe` as a
   native jj-lib transaction → one op) compared against `jj`'s op-log effect.
4. **Then breadth** per concept §12: reads → transactions → op log → workspaces → git interop.

## Suggested milestones (refine in your plan)

- **M0** — devenv (rust + maturin + pinned jj 0.38) + maturin mixed skeleton + `_pyjutsu`
  returning the linked jj-lib version + `Workspace.load` + read `@` as a `Commit`. Differential
  harness scaffold (spins up a scratch repo, can invoke the pinned `jj`).
- **M1** — reads: `resolve`, `log`, `bookmarks`, `operations`, `diff_stat`, conflicts →
  Pydantic models + differential tests + golden fixtures.
- **M2** — transactions (native, one op each): `new`/`describe`/`edit`/`abandon`/`rebase`/
  `squash`/bookmark ops/`snapshot`; op log: `undo`/`restore_operation`/`at_operation`.
- **M3** — workspaces (`workspaces`/`add_workspace` eager/`forget_workspace`/`is_stale`/
  `update_stale`) + git interop (`git_fetch`/`git_push`/`git_import`/`git_export`/`remotes`);
  package the abi3 wheel; CI wheel build.

## Watch out for (concept §8, §11)

- **`jj-lib` is explicitly unstable** → hard pin, thin Rust layer, differential tests,
  deliberate upgrade cadence; never expose jj-lib types to Python.
- **FFI panic safety** — a panic across the boundary aborts the process; wrap fallible paths,
  `catch_unwind`, map jj-lib errors to a `PyjutsuError` hierarchy.
- **GIL** — release it (`Python::allow_threads`) on snapshot/fetch/push/large revset eval.
- **`Send`/`Sync` + lifetimes** — `#[pyclass]` handles must be `Send` and must not outlive
  their repo; the shared repo is `Arc`-shared, working copies are path-affine.
- **Reads can mutate** — jj snapshots `@` on many ops; provide explicit `snapshot()`, an
  `ignore_working_copy` read mode, and `at_operation` for side-effect-free reads.
- **Stale working copy** across workspaces — detect + `update_stale`, never silently operate.
- **Two concurrency layers** — per-workspace WC lock + op-log optimistic concurrency
  (divergent operations are real; surface them).
- **Wheel build matrix** — abi3 to limit it; the git backend (gix/libgit2) is heavy → watch
  size/build time; ship sdist (needs Rust) + prebuilt wheels.
- **Stay policy-free** — faithful jj primitives only.

## Guardrails

- Don't add a CLI/subprocess backend, a migration shim, or any old-Pyjutsu compatibility.
- Don't leak `jj-lib` types across the FFI or put business logic in Rust.
- Don't bake in workflow policy (lanes, frozen trunk, etc.).
- Don't run bare host tooling — everything through devenv.
- Don't add AI-generated attribution to commits/PRs/docs.

**Start by reading `docs/PYJUTSU_CONCEPT.md`, then come back with a milestone plan and the
repo skeleton (maturin mixed layout + devenv) for approval — and run the M0 jj-lib build
spike early, since it gates the whole approach.**
