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

### Suggested fix (pyjutsu)

Pick one (in rough order of preference):

1. **On `Workspace.init` adopt, prune orphaned `refs/jj/*` that don't correspond to live workspace
   state** before/while importing — so re-adopting a repo whose `.jj` was deleted starts from the real
   git refs (branches + tags) only, not jj's internal bookkeeping.
2. Provide an explicit `Workspace.prune_jj_refs(path)` (or a `clean=True` flag on `init`) so a consumer
   recovering a workspace can opt in.
3. At minimum, **document** that deleting `.jj` by hand must be paired with pruning `refs/jj/*`, and
   surface a warning from adopt when stray `refs/jj/keep/*` are present.

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
- gitman counterpart: `.scratch/projects/06-stray-tags-and-divergent-reconcile/` (consumer-side fixes:
  exclude tagged commits from the stray revset; make `reconcile` operate by commit-id for divergent
  strays).
- Full recovery narrative: repoman `.scratch/projects/06-bootstrapping issues/…` (Follow-up C).
