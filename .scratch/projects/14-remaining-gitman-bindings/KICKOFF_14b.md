# Kickoff — project 14b: D/F-safe `write_git_ref` for 3-level fractal refs → adopt P4 in gitman

> Paste the section below as the opening message of a **clean session** started in
> `/home/andrew/Documents/Projects/pyjutsu`. It is fully self-contained: the failing case, the
> 0.12.1 code that got partway, the fix approach (with the one empirical unknown flagged), the
> acceptance test, and the full build → wheelhouse → re-pin → adopt sequence.

---

**Finish pyjutsu project 14b — make `write_git_ref` D/F-safe for three-level fractal ref names, then adopt P4 in gitman to reach raw-`git`-zero.**

You're in the pyjutsu repo (`/home/andrew/Documents/Projects/pyjutsu`), a maturin/PyO3 binding to **jj-lib 0.42.0**, pinned `jj-lib = "=0.42.0"` (do NOT bump it — `gitman doctor` asserts the pin). Current version **0.12.1**, `main` at `60e6319` (+ a docs commit `2f25976`). gix is **0.84.0**. Its consumer is gitman at `/home/andrew/Documents/Projects/gitman`.

## The problem (one specific bug)
`Workspace.write_git_ref(name, target)` (`src/workspace.rs`, `fn write_git_ref` ~line 1930) force-writes `refs/heads/<name>` in the colocated `.git` — a reconcile-only recovery escape hatch. It fails on **three-level fractal lane names** with pre-existing mixed loose refs:

- Given loose `refs/heads/T` (a file) and loose `refs/heads/T/api/handler` (which makes `refs/heads/T/api/` a **directory**), calling `write_git_ref("T/api", <oid>)` raises `_pyjutsu.GitError: failed to write ref 'T/api': An IO error occurred while applying an edit`.
- It's a **bidirectional** directory/file (D/F) conflict: a loose file blocks `T/api` from below, a loose directory blocks it from above.

**History:** 0.12.0 wrote a plain loose ref via `git_repo.edit_reference` (failed on any `/`-collision). **0.12.1** (`60e6319`) routed the write through the file-store transaction:
```rust
let mut ref_store = git_repo.refs.clone();
ref_store.write_reflog = gix::refs::store::WriteReflog::Disable; // reflog path has the same D/F; gix won't pack reflogs
ref_store.transaction()
  .packed_refs(gix::refs::file::transaction::PackedRefs::DeletionsAndNonSymbolicUpdatesRemoveLooseSourceReference(Box::new(git_repo.objects.clone())))
  .prepare(Some(edit), gix::lock::acquire::Fail::Immediately, gix::lock::acquire::Fail::Immediately)?
  .commit(None)?;
```
This fixed **flat and two-level** names (`T` loose → write `T/api`), but only removes the *target's own* loose source. It does NOT pack the **conflicting** loose refs (`T`, `T/api/handler`), so the three-level case still fails — gix's transaction still can't acquire the loose lock for `T/api`.

## The fix to implement
Mirror what `git update-ref` does implicitly (`git pack-refs`): **pack every `refs/heads/*` ref (at its current oid) into `packed-refs` in one transaction**, plus the new/updated target — so *no loose head ref remains* to create a D/F conflict. Approach:
1. Enumerate all refs under `refs/heads/` (mirror the P2 `git_refs` impl in `src/workspace.rs`, which uses `git_repo.references()?.prefixed(...)?` + `peel_to_id_in_place()`). Collect `(FullName, ObjectId)` — resolve borrows by collecting into a `Vec` before building edits.
2. Build one `Vec<RefEdit>`: an `Update` (force, `PreviousValue::Any`, `LogChange` default) for every existing head ref at its *current* oid, and the target at its *new* oid (dedup: if the target already exists, update it in place; if not, add it).
3. Commit that whole set through the same file-store transaction + `DeletionsAndNonSymbolicUpdatesRemoveLooseSourceReference` + reflog disabled. All heads land in `packed-refs`, their loose files removed.
4. **Critical unknown to verify empirically:** does gix's `prepare` still try to lock the *conflicting* loose paths (e.g. create `refs/heads/T/api.lock`) even when they're in the packed edit set? If it does and still fails, delete the conflicting loose refs explicitly first (a separate loose-delete pass or a `RefEdit` `Change::Delete`), or investigate a broader `PackedRefs` mode. Resolve this against real `cargo` errors and the gix 0.84 source (find it under the cargo registry) — do NOT guess.

Keep the public API identical: `write_git_ref(self, name, target) -> None`, `delete_git_ref(self, name) -> None` (idempotent). `delete_git_ref` needed no change in 0.12.1 (deletion has no D/F write conflict) — re-verify it still holds for 3-level. Don't reintroduce a reflog (the reflog path has the same D/F and gix won't pack it).

## Test (this is the acceptance gate)
Add to `tests/test_git_ref_write.py` a **three-level, mixed loose/packed** probe that currently fails:
- Create loose refs for `T`, `T/api`, AND `T/api/handler` (three levels — use the existing `_git` raw-`git` update-ref oracle or a jj export so they're genuinely loose on disk). Then `write_git_ref("T/api", <oid>)` MUST succeed, and all three names MUST still resolve to the right oids (verify via raw `git rev-parse`).
- The reverse: with `T/api` and `T/api/handler` present, `write_git_ref("T", <oid>)` MUST succeed.
- `delete_git_ref` on `T`, `T/api`, `T/api/handler` works and is idempotent.
Use the `bookmarked_repo`/`jj` fixtures + `_git` oracle pattern already in `tests/test_git_ref_write.py` and `tests/test_tags.py` (see `tests/conftest.py`).

## Build / test loop (inside devenv; SLOW — commands auto-background, wait for them)
- Build after EVERY `.rs` edit: `devenv shell -- bash -c 'cd "$DEVENV_ROOT" && maturin develop --uv'`
- Full gate: `devenv shell -- bash -c 'cd "$DEVENV_ROOT" && ruff check python tests && cargo clippy --all-targets -- -D warnings && .devenv/state/venv/bin/pytest -q && cargo test'`
- The stub `python/pyjutsu/_pyjutsu.pyi` is types-only (not compiled) — no signature change here, so it shouldn't need edits.

## Ship (pyjutsu side)
- Full gate green (ruff, clippy `-D warnings`, pytest, cargo test); new 3-level probes pass; note any test already failing on `main` before your change.
- Bump version to **0.12.2** in ALL of: `Cargo.toml`, `pyproject.toml`, `python/pyjutsu/__init__.py` (the `__version__` **string literal** — it's the stale-build tripwire, not just metadata), and `tests/test_build.py` (the `== "0.12.2"` literal). Golden `tests/golden/model_fields.json` should NOT change (no model shape change). Keep `jj-lib = "=0.42.0"`.
- Commit (conventional-commits style, **NO AI-attribution trailer** — repo convention) and push `main`.

## Then propagate to gitman and adopt P4 (this completes raw-`git`-zero for ref writes)
gitman consumes pyjutsu as a **prebuilt wheel from vendomat's wheelhouse** (`/home/andrew/Documents/Projects/vendomat`), not the sibling checkout. To get 0.12.2 into gitman:
1. In vendomat: `nix flake update pyjutsu` (its `flake.lock` pins the wheel source) → commit the `flake.lock`.
2. In gitman: **`rm -rf .devenv`** to bust devenv's eval cache — `devenv update pyjutsu/vendomat` alone does NOT move gitman's (rev-less, branch-tracked) pyjutsu input; without clearing `.devenv` the old wheelhouse store path keeps resolving. Then a fresh `devenv shell` re-syncs.
3. In gitman `pyproject.toml`, bump the floor to `"pyjutsu>=0.12.2"` (so a stale wheel fails loudly).
4. **Adopt P4**: in `src/gitman/reconcile.py`, `_heal_colocated_refs` currently uses raw `git update-ref` with an NB comment explaining why. Replace the two `subprocess.run(["git","update-ref",...])` loops with `session.ws.write_git_ref(name, jj_id)` and `session.ws.delete_git_ref(name)` (idempotent), drop `import subprocess`, and update the docstring/NB (P4 is now D/F-safe as of 0.12.2). This is the same swap that was reverted in the prior session — see the NB in that function for the exact before/after.
5. **Acceptance:** `devenv shell -- bash -c 'cd "$DEVENV_ROOT" && ruff check src tests && pytest -q'`. The key gate is `tests/test_phase3_concurrency.py::test_reconcile_refreshes_stale_grandchild_workspace` — it MUST now pass. Expect exactly **2 pre-existing, unrelated** failures to remain: `tests/test_remote_trunk_status.py::test_status_trunk_behind_best_effort` and `::test_status_diverged_trunk_reports_not_crashes` (a fixture `git push` env issue — confirm they reproduce without your changes; don't try to fix them).
6. gitman is **colocated and dogfoodable** — route the gitman commit through gitman itself: `gitman start p4-adopt` → edit → `gitman save -m "…"` → `gitman land p4-adopt` → `gitman push`. (No AI-attribution trailers.)

## Context / prior art to read
- pyjutsu `.scratch/projects/14-remaining-gitman-bindings/OVERVIEW.md` — the P4 section (the "⚠️ Partly fixed in 0.12.1" note has the full analysis + this exact plan).
- gitman `.scratch/projects/27-implementation-guides/PYJUTSU_14_BINDINGS_GUIDE.md` — the as-built notes section (P1 API correction, P4 status, the wheelhouse re-pin gotcha).
- Existing gix ref patterns to mirror in `src/workspace.rs`: the current `write_git_ref` (~1930), `delete_git_ref` (~1965), `git_refs` (P2, ref enumeration), `prune_orphaned_keep_refs` (~247, `RefEdit`/`Change::Delete`), `create_tag` (~1588, gix ref write).

## Definition of done
pyjutsu 0.12.2 pushed with the 3-level D/F fix green; vendomat + gitman re-pinned; gitman's `reconcile` swapped to P4 with `test_reconcile_refreshes_stale_grandchild_workspace` passing and only the 2 known-unrelated failures remaining; gitman change landed + pushed via gitman. Net result: gitman's only remaining raw-`git` surface is `gitshim.py` (colocate bootstrap + `symbolic-ref`). Report honestly per-step, including the real gix mechanism that resolved the bidirectional D/F.
