# 10 — Adopt: tag visibility on import + stale `refs/jj/keep/*` after `.jj` deletion

> **Found:** 2026-06-22, while recovering a colocated `.jj` in the **gitman** repo (re-adopting an
> existing `.git` via `pyjutsu.Workspace.init(".", colocate=True)`). The git side was correct, but a
> fresh adopt left the repo with a phantom "stray" commit that a downstream consumer (gitman) read as
> off-canonical and could not clear. Root-causing it surfaced two pyjutsu-side items plus one build-
> hygiene note. The bulk of the *consumer-facing* fix lives in gitman (see that repo's
> `06-stray-tags-and-divergent-reconcile`); this report is the **pyjutsu half**.

## TL;DR — what pyjutsu should decide / fix

| # | Item | Kind | Severity |
|---|------|------|----------|
| P1 | Stale `refs/jj/keep/*` survive a `.jj` deletion and are re-imported by the next adopt, resurrecting orphaned (and divergent) commits | Hygiene / concrete | medium |
| P2 | `adopt_existing_git` imports **tags** (jj-standard `import_refs`), so a tag pointing at an *off-main* commit becomes a visible head | Design decision | low–medium |
| P3 | The import-time version guard (`JJ_LIB_TARGET` vs linked `_ext.version()`) trips during an in-flight version bump when the Python metadata outruns the compiled extension | Build hygiene | low |

**Key correction to the first-pass framing:** this is **not** a "0.42 regression." `adopt_existing_git`
calls jj-lib's `git::import_refs` — the same import the `jj git import` CLI runs — which imports tags.
A *fresh* adopt therefore imports every current tag; the long-lived workspace that read as canonical
simply predated those tags (it had never run an import that pulled them in). The 0.42 build was merely
what happened to be installed during the recovery. So P2 is a **design question about the bootstrap-
adopt use case**, not a correctness bug in 0.42.

---

## Status (updated 2026-06-30)

Where each item stands across **both** repos as of this update:

| # | pyjutsu side | gitman (consumer) side |
|---|--------------|------------------------|
| **P1** — orphaned `refs/jj/keep/*` survive `.jj` deletion | **Fixed (shipped) — option (1).** `adopt_existing_git` now calls `prune_orphaned_keep_refs` **before** importing: it deletes every `refs/jj/keep/*` from the colocated `.git` (the fresh `init_external_git` store has authored none of its own yet, so all present are orphaned). Re-adopting a recovered repo no longer carries the dead workspace's ~50 GC-anchors forward; no hand-purge needed. Test: `test_init_adopt.py::test_readopt_prunes_orphaned_keep_refs`. **Mechanism correction (verified vs jj-lib 0.42):** keep-refs are *not* re-imported — `import_refs`/`import_head` only scan `refs/heads/**`, `refs/remotes/**`, `refs/tags/**`, `HEAD` (`diff_refs_to_import`), never `refs/jj/keep/**`. So this prune is **hygiene** (stops orphaned-ref accumulation + lets `git gc` reclaim the dead objects), *not* the cure for the visible divergence — that commit was anchored by the **tag** (P2), fixed consumer-side. | **Defensively covered (shipped).** gitman `reconcile`/`adopt` no longer *dead-end* on the divergent change P1 manufactures: they target & name stray/range rows by `commit_id`, so a divergent stray adopts into two distinct lanes or abandons cleanly (issue 06 §G2, **PR #28, merged**). gitman does **not** prune the keep-refs themselves — P1 remains the upstream *cure*; G2 is the consumer's *floor*. |
| **P2** — adopt imports tags → off-main tagged commit is a visible head | **Decision shipped as (A): keep jj-standard.** `adopt_existing_git` still imports tags via `git::import_refs` (unchanged, faithful to `jj git import`). Option (B) (`import_tags=False`) not added — revisit only if more consumers hit it. | **Fixed (shipped).** `state._stray_revset` now excludes `tags()`, so a tagged off-main commit is no longer flagged as stray (issue 06 §G1, **PR #28, merged**). This is the agreed consumer-side home for "tags aren't work." |
| **P3** — version guard trips mid-bump | **Fixed (shipped) — pyjutsu 0.9.0, `feat/11-tx-split`.** Two changes remove the footgun: (1) a **`build.rs`** parses the resolved `Cargo.lock` and emits `PYJUTSU_JJ_LIB_VERSION`, so `_ext.version()` is now **build-derived** — `JJ_LIB_TARGET` is just an alias of it (no second hand-maintained jj-lib copy that can drift). (2) A new `_ext.pyjutsu_version()` (`CARGO_PKG_VERSION`) lets the Python guard check `__version__ == pyjutsu_version()` instead of comparing two version numbers — that fires **only** on a genuinely stale compiled extension (a bump not followed by `maturin develop`), with a clear "stale build — rebuild" message, and imports clean once rebuilt. No more false trip on editable installs. `test_build.py` covers the invariant. | n/a (build-time only). |

**Net:** the *consumer-facing* symptom that triggered this report (a release tag's orphaned, divergent
commit reading as un-clearable "stray work") is **fully addressed in gitman** — both the false-stray
(G1) and the un-recoverable-divergence (G2) halves shipped in PR #28. **P1 is now also shipped on the
pyjutsu side** (adopt prunes orphaned `refs/jj/keep/*`), so a `.jj` recreate no longer drags the dead
workspace's GC-anchors forward. **Correction to the earlier framing:** P1 is *not* the root cause of the
divergence — reading jj-lib 0.42 confirms `import_refs` never scans `refs/jj/keep/**`, so a keep-ref
cannot resurrect a commit *as a visible head*. The visible off-main commit was anchored by the **tag**
(P2), and that "tags aren't work" call lives in the consumer (gitman G1). P1's prune is **hygiene**:
it removes the orphaned-ref pile-up that forced the hand-purge and lets `git gc` reclaim the dead
objects. (gitman issue 06 §"Open questions" floated extending `_heal_colocated_refs` to drop orphaned
`refs/jj/*` as an alternative self-heal — **not built**; the upstream hygiene fix lives here.)

---

## Background — how it surfaced

The gitman repo is a colocated jj repo. During an unrelated recovery its `.jj` was removed and
re-created with `Workspace.init(".", colocate=True)` (the adopt-existing path, workspace.rs:167). After
re-adopt:

- `gitman doctor` → HEALTHY (colocated, trunk frozen) and `@` was a clean empty child of trunk.
- **But** `gitman status` → OFF-CANONICAL: *"change(s) `poosovxy…` belong to no lane (edited outside
  Gitman?)."* — on a repo whose working tree was clean and whose trunk matched `origin/main`.

The flagged change `poosovxy` resolved to commit **`c2a8443` "Bump version to 0.2.0"**, which is **off
the main line** (the 0.2.0 release commit was rebased out of `main`'s ancestry by a later history
rewrite; only a tag still references it).

---

## P1 — stale `refs/jj/keep/*` survive `.jj` deletion and pollute re-adopt

**Severity:** medium (turns a `.jj` re-create into a corrupted-looking import; the user must hand-purge
git refs to recover).

### Evidence

After `rm -rf .jj` and re-adopt, `git show-ref` still listed **~50** `refs/jj/keep/<sha>` refs (jj-lib
writes these to keep commits alive against git GC; they live in **`.git`**, not `.jj`). Among them were
**three** different commits all titled "Bump version to 0.2.0":

```
f8691bc  refs/jj/keep/f8691bc…  Bump version to 0.2.0
14504fe  refs/jj/keep/14504fe…  Bump version to 0.2.0
c2a8443  refs/jj/keep/c2a8443…  Bump version to 0.2.0   ← also the target of tag v0.2.0
```

Because those keep-refs persisted in `.git`, **every** fresh adopt re-imported them, resurrecting the
extra copies and producing a **divergent change** (one jj change-id mapped to multiple commits). They
had to be purged by hand before re-adopting:

```
git for-each-ref --format='%(refname)' 'refs/jj/' | while read -r r; do git update-ref -d "$r"; done
```

### Root cause

`Workspace.init(colocate=True)` adopt (and `git_import`) call `git::import_refs`, which imports
everything currently in `.git/refs/**` — including leftover `refs/jj/keep/*` from a *previous* jj
workspace that was deleted without pruning them. jj-lib's normal lifecycle prunes/refreshes these refs
as part of operating a live `.jj`; a `.jj` that is `rm`'d out of band leaves them orphaned, and the
next adopt treats them as authoritative refs to import.

### Fix shipped (2026-06-30) — option (1)

Option (1) below was implemented: `adopt_existing_git` calls a new `prune_orphaned_keep_refs(repo)`
**before** `import_head`/`import_refs`. It enumerates `git_repo.references().prefixed("refs/jj/keep/")`
and deletes them via one `edit_references` batch (each guarded `ExistingMustMatch`, mirroring jj-lib's
own `to_ref_deletion`). Scope is deliberately just `refs/jj/keep/**` (the P1 namespace); `refs/jj/root`
and `refs/jj/remote-tags/` are left untouched. Safety: the adopt path always runs against a *freshly*
`init_external_git`'d store that has authored no keep-refs of its own yet, so every `refs/jj/keep/*`
present is necessarily a leftover from the deleted `.jj`. jj re-creates the keep-refs it needs for the
heads it imports immediately after (`import_head_commits`). Covered by
`test_init_adopt.py::test_readopt_prunes_orphaned_keep_refs` (verified failing without the prune).

> **Verified vs jj-lib 0.42:** the original "re-imported" wording overstated the mechanism.
> `import_refs`/`import_head` walk only `refs/heads/**`, `refs/remotes/**`, `refs/tags/**`, `HEAD`
> (`diff_refs_to_import`, `git.rs`) — **never** `refs/jj/keep/**`. Keep-refs are GC anchors recreated
> only by `gc()` (`recreate_no_gc_refs`, `git_backend.rs`). So a stale keep-ref keeps the orphaned
> commit *object* alive but does not, by itself, import it as a visible head. The prune is therefore
> **hygiene** (ref pile-up + GC reclamation), not the divergence cure — that's the tag (P2 → gitman G1).

The options as originally framed (kept for the record):

1. **On `Workspace.init` adopt, prune orphaned `refs/jj/*`** before/while importing — **← shipped (keep/ only).**
2. Provide an explicit `Workspace.prune_jj_refs(path)` (or a `clean=True` flag on `init`) so a consumer
   recovering a workspace can opt in. *(Not taken — the auto-prune on adopt covers the recovery path
   with zero new API surface; revisit if a consumer needs to prune without re-adopting.)*
3. At minimum, **document** that deleting `.jj` by hand must be paired with pruning `refs/jj/*`.
   *(Superseded by the auto-prune, but the `init` docstrings now state the prune behaviour.)*

### Test plan

- Create a colocated repo, run a few jj ops (populating `refs/jj/keep/*`), `rm -rf .jj`, re-adopt →
  assert no resurrected/divergent commits; visible heads = git branch/tag tips only.

---

## P2 — adopt imports tags, so an off-main tagged commit becomes a visible head

**Severity:** low–medium (correct per jj semantics, but surprising for the colocated-bootstrap use
case and the proximate trigger of the downstream off-canonical state).

### Evidence

The repo has tags `v0.1.0` (→ on-main), `v0.2.0` (→ **off-main** `c2a8443`), `v0.2.1` (→ on-main).
After a fresh adopt with **only** `main` + the three tags present as refs (keep-refs purged per P1),
`c2a8443` was still a visible head:

```
revset (main..) ~ ::(bookmarks() | remote_bookmarks()) ~ @
  → c2a8443  "Bump version to 0.2.0"   (non-empty, no bookmark, not @)
```

Deleting the other refs didn't remove it; it is anchored solely by the `v0.2.0` tag. It was also
**divergent**: change-id `poosovxy` mapped to both `c2a8443` (off-main, tagged) and `c90ef6c`
(on-main "Pyjutsu bootstrap fixes") — a historical rewrite jj records as one change with two commits.

### Root cause (this is jj-standard, not a bug)

`adopt_existing_git` (workspace.rs:167) →

```
git::import_head(tx.repo_mut())
git::import_refs(tx.repo_mut(), &options)   // workspace.rs:181-182
```

`import_refs` is the same import `jj git import` runs; it imports branches → bookmarks, remote
branches, **and tags**. A tagged commit is *reachable* (the tag refs it) so `abandon_unreachable_commits`
does not drop it → it stays a visible head. The real `jj` CLI would show the same off-main tagged commit
in `jj log`. So pyjutsu is faithfully matching jj here.

### The decision (pyjutsu half of the "bit of both")

The downstream symptom — a release tag's orphaned commit masquerading as "stray work" — is best fixed
**in the consumer** (gitman: exclude tagged commits from its stray signal; see that repo's project).
That keeps pyjutsu's import faithful to jj.

The open pyjutsu question is whether the **colocated-bootstrap adopt** specifically should be more
conservative. Options:

- **(A) Keep jj-standard (recommended default).** Adopt imports tags, matching `jj git import`. Document
  that adopting a repo with off-main tags will surface those commits as heads (expected, like jj). Push
  the "don't treat tags as work" logic to consumers. Lowest surprise for anyone who knows jj.
- **(B) Add an opt-in `import_tags: bool = True` (or `bootstrap=True` preset) to `init`.** A consumer
  bootstrapping a tool onto an existing repo could adopt with `import_tags=False` to get a minimal
  branches-only view, avoiding off-main tag heads entirely. Diverges from jj only when explicitly asked.
- **(C) Always skip tags on adopt.** Rejected — silently diverges from jj semantics; tags would be
  missing until a later explicit import.

**Recommendation:** ship **(A)** + the gitman-side fix; consider **(B)** if more consumers hit this.
Do **not** do (C).

> **Done (2026-06-30):** (A) stands (adopt still imports tags, unchanged) and the gitman-side fix
> **shipped** — `state._stray_revset` excludes `tags()` (issue 06 §G1, PR #28, merged). (B) was not
> taken. See the Status table above.

### Test plan

- Adopt a repo carrying an off-main tag → assert the tagged commit is imported and visible (documents
  the default), and, if (B) is taken, that `import_tags=False` yields branches-only heads.

---

## P3 — version guard trips during an in-flight version bump (build hygiene)

**Severity:** low (transient; a rebuild fixes it).

### Evidence

Mid-session, importing pyjutsu raised:

```
PyjutsuError: broken build: pyjutsu 0.8.0 targets jj-lib 0.42.0 but the extension links jj-lib 0.38.0
```

`python/pyjutsu/__init__.py` hard-codes `__version__ = "0.8.0"` / `JJ_LIB_TARGET = "0.42.0"` and
guards them against the compiled `_ext.version()`. During an editable-install workflow the Python
metadata can advance (a version bump in the source tree) **before** the extension is rebuilt, so the
live metadata (0.42) outran the linked extension (0.38) and the guard fired — even though nothing was
actually broken. A `maturin develop` / reinstall resolved it.

### Notes / optional fix

The guard itself is good (it catches a genuinely broken build). The footgun is hand-maintained
`__version__`/`JJ_LIB_TARGET` drifting from the compiled artifact during a bump. Options: source
`JJ_LIB_TARGET` from the build (e.g. emit it from Cargo at compile time so it can't drift), and/or note
in the contributor docs that bumping the version requires a rebuild before the package imports. Low
priority; flagged for completeness since it briefly blocked the recovery.

> **SHIPPED (2026-06-30, pyjutsu 0.9.0):** both halves, plus a re-aim of the guard.
> - **jj-lib version is now build-derived.** A `build.rs` parses the resolved `Cargo.lock` and emits
>   `cargo:rustc-env=PYJUTSU_JJ_LIB_VERSION`; `src/lib.rs`'s `JJ_LIB_VERSION` is `env!(…)` of that, so
>   `_ext.version()` reflects the *actually linked* dependency and cannot drift. `JJ_LIB_TARGET` is now
>   just `= _ext.version()` in Python — the second hand-maintained copy is gone. (Parsing `Cargo.lock`
>   rather than shelling `cargo metadata` keeps `build.rs` robust in the nix/devenv sandbox; it panics
>   loudly if the lock has no `jj-lib` entry, so it can never fall back to a stale hardcoded number.)
> - **The guard now protects something real.** A new `_ext.pyjutsu_version()` returns
>   `CARGO_PKG_VERSION`; the Python tripwire is `__version__ == _ext.pyjutsu_version()`. That is the
>   genuine "did you forget to rebuild after a bump" signal — it fires on a stale `.so`, not on two
>   hand-maintained numbers, and does **not** false-fire once you actually `maturin develop`. The old
>   `JJ_VERSION != JJ_LIB_TARGET` comparison (two hand-maintained numbers) is dropped.
> - **Outcome:** bumping only `__version__` without rebuilding raises a clear "stale build — rebuild"
>   error; a correctly built tree imports clean; no duplicated hand-maintained jj-lib string.
>   Covered by `test_build.py::test_pyjutsu_version_matches_extension` + the two build-derived asserts.

---

## Confidence & caveats

- **Certain (observed):** the off-main `v0.2.0`→`c2a8443` stray after fresh adopt; the divergent
  change-id (`poosovxy` → `c2a8443` + `c90ef6c`); ~50 stale `refs/jj/keep/*` re-imported across adopts;
  the version-guard error.
- **Inferred (confirm against jj-lib):** that `import_refs` imports tags and keeps tagged commits
  reachable/visible. The code path (`import_head` + `import_refs`, workspace.rs:181-182) and jj's CLI
  semantics support this, but a quick check of jj-lib's `import_refs` tag handling would confirm before
  acting on P2.
- **Corrected:** the earlier "0.42 imports tags, 0.38 didn't" framing was wrong — no fresh 0.38 adopt
  was tested; the difference was *fresh-adopt-imports-current-tags* vs *a long-lived `.jj` that
  predated the tags*.

## References

- pyjutsu: `src/workspace.rs:161-204` (`adopt_existing_git`), `:181-182` (`import_head`/`import_refs`),
  `:940-985` (`git_import`/`export_refs`), `python/pyjutsu/__init__.py` (version guard).
- gitman counterpart: `.scratch/projects/06-stray-tags-and-divergent-reconcile/` (consumer-side fixes,
  **shipped in PR #28**: exclude tagged commits from the stray revset (G1); make `reconcile`/`adopt`/
  `abandon` operate by commit-id for divergent strays via a `_target` helper (G2)). The doc's
  `ISSUE_ANALYSIS.md` is the design-review that carried these to merge.
- Full recovery narrative: repoman `.scratch/projects/06-bootstrapping issues/…` (Follow-up C).
