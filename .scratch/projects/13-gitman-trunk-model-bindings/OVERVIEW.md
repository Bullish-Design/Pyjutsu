# 13 — Pyjutsu bindings for gitman's single local-authored trunk model

**Date:** 2026-07-09
**Status:** ✅ **SHIPPED in pyjutsu 0.10.0** (commits `416f9d5` feat + `2ad40e8`/`a5081d6` docs). This
is the original *scoping* doc; `PLAN.md` re-derived the items from jj-lib 0.42 source and **shrank the
release sharply** — read `PLAN.md` for what was actually built and the "Shipped — deltas" banner just
below for the corrections to this OVERVIEW's assumptions.
**Driver:** `gitman/.scratch/projects/19-trunk-model-deep-dive/ANALYSIS.md` (esp. the ADDENDUM). That
analysis moves gitman to a **single local-authored trunk model** and relocates the enabling
jj↔git-boundary fixes *into pyjutsu*, because we own pyjutsu and its current "out of scope" limits are
our choices, not constraints.
**Version target:** pyjutsu `0.9.0` → `0.10.0` (done).
**jj-lib pin:** `=0.42.0` (`Cargo.toml:16`) — every item shipped against 0.42; the pin held.

---

## ✅ Shipped in 0.10.0 — deltas from this scoping doc (2026-07-09)

`PLAN.md`'s read of jj-lib 0.42 corrected two assumptions below; the net release was **one real
feature + two verifications**, 11 tests green:

- **P1 force-with-lease — NO new code (⟲ this OVERVIEW's premise was wrong).** jj-lib has *no*
  fast-forward guard: `git_push` *already* performs an **unconditional force-with-lease** (lease =
  remote-tracking ref; `git.rs::push_updates` always force-pushes). So there is **no `force=` flag** to
  add — a content-equal, hash-divergent trunk already pushes when the lease is current, and is rejected
  when the remote moved out-of-band. Docstrings corrected; probe test added. **Carries into gitman:**
  `push --reset-origin` needs no raw git/flag, *but* strict-FF on the everyday push is now a **gitman
  policy** (refuse non-FF → `pull`), not an engine guarantee — gitman's project-19 ANALYSIS was
  corrected to match (its earlier "engine-enforced FF" claim was wrong).
- **P2 `untrack_paths` — SHIPPED (the one real feature).** `MergedTreeBuilder.set_or_remove(absent)` +
  `LockedWorkingCopy::reset`: drops the tree entry and file-state, **leaves the file on disk**;
  `PrefixMatcher` untracks a subtree; composes with gitignore. The `auto_track` fileset writers were
  **dropped** (Q-P2: gitignore fires first, so they're unneeded).
- **P3 `sync_colocated` — SHIPPED + verified.** Confirmed jj-lib's `reset_head` rebuilds colocated git
  HEAD **and** the `.git/index` from `@`'s parent tree unconditionally (`git.rs:1789/1874`) — so the
  index-lag was already fixed; the new verb is a thin idempotent way to trigger it without a refs
  change. Regression test locks in gitman 15-RC6 (`check-ignore` no longer lies).
- **P4 (`is_ancestor`/`patch_id`) and P5 (tag write) — deferred as planned** (promotion triggers in
  `PLAN.md §P4/P5`); `tags.py` remains gitman's only raw-git surface.
- **Bonus:** `tx.untrack_bookmark(name, remote)` (remote-tracking-bookmark untrack) also landed.

**Remaining work is entirely gitman-side** (consume these bindings — the Tiers in gitman project 19).
The section below is the original scoping and is kept for provenance; where it disagrees with this
banner or `PLAN.md`, they win.

---

## Why gitman needs these (one paragraph)

gitman is becoming the sole author of every trunk SHA: lanes fold into local trunk via `land`, and
local trunk reaches origin via a fast-forward `push`. In that model **every trunk push is a
fast-forward** and no re-hash twins form — *provided* three boundary behaviours hold that pyjutsu does
not currently expose: (1) a **lease-checked force** for the one-time migration of repos that already
carry re-hash residue; (2) **untracking** machine-local files so they never snapshot into lanes/trunk;
(3) a **total colocated sync** (HEAD *and* index) so raw-git tooling that shares the `.git` never
lies. Everything else gitman needs (fetch, FF push by bookmark name, remote add, detached-HEAD-safe
export) pyjutsu already provides — see §7.

## Design principles

- **jj-lib native, in-process.** No new `git` subprocess surface. The whole point is to drive
  gitman's raw-git surface toward **zero** (only `tags.py` remains, and P5 optionally retires that).
- **Lease by remote-tracking ref.** jj-lib's push already carries the expected-old value (the
  last-fetched `<bookmark>@<remote>`), which *is* a force-with-lease. We expose the force flag; we do
  **not** add a blind `--force`.
- **Idempotent, `@`-neutral sync.** Colocated HEAD/index repair must be safe to call after every
  mutation and a no-op when already in sync (as `reset_head` is today).
- **Each item ships with an in-process probe** against a bare origin + colocated work repo (the
  established pyjutsu test pattern), and maps to a specific gitman field-report RC.

---

## Work items

Priority: **P1/P2 are must-haves** (no clean gitman workaround exists). **P3 is a correctness fix**
(verify-first — may already be covered by `reset_head`). **P4/P5 are optional polish.**

### P1 — Lease-checked force-push  *(must-have; unblocks gitman `push --reset-origin`)*

**Motivation.** gitman's migration escape for legacy re-hash-twin repos (gitman's own `main` is
`1 behind / 1 ahead origin` right now) must overwrite a content-equal but hash-divergent
`origin/<trunk>` *once*, safely. gitman field reports 13/15 did this by hand with raw
`git push --force-with-lease` + a manual content audit. We want it in-process and lease-safe.

**Current state.** `git_push` (`python/pyjutsu/workspace.py:172`; Rust `src/workspace.rs:1206`) builds
`GitPushRefTargets` with a `before`/`after` diff and calls `git::push_refs(..., &GitPushOptions::
default())`. The docstring is explicit: *"Force-push … remain out of scope."* jj refuses a
non-fast-forward bookmark move before it ever reaches `push_refs`.

**Proposed API.**
```python
def git_push(self, remote, bookmark=None, *, allow_new=False, delete=False,
             all=False, tracked=False, force_with_lease=False) -> Operation | None: ...
```
- `force_with_lease=True` allows a **non-fast-forward** update of the named bookmark(s), using the
  current remote-tracking ref `<bookmark>@<remote>` as the expected-old value (the lease).
- If the remote's actual ref ≠ the tracking ref (origin moved since the last fetch), the update is
  **rejected** → `GitError` → the caller must `fetch`/`pull` first. This is the property that makes it
  safe: even a forced push can never clobber genuine new upstream work.
- Mutually sensible with `bookmark`/`allow_new`; rejected in the bulk (`all`/`tracked`) modes for now.

**jj-lib mechanism.** The lease already exists — `push_refs` uses the ref's expected-old-oid for an
atomic update. The work is to (a) bypass jj's *own* non-FF refusal when `force_with_lease` is set, and
(b) surface the lease-mismatch rejection cleanly as `GitError`. Confirm exactly where jj 0.42 performs
the FF check (CLI-layer vs `push_refs`); if it's in `push_refs` via `GitPushOptions`, this is a flag
flip, otherwise replicate the ref-target build without the FF guard.

**Semantics / edge cases.**
- Requires a prior `git_fetch` so the tracking ref is current (document as a precondition; gitman
  gates on a content-check first).
- No tracking ref yet (bookmark never pushed) → behaves like a normal create (`allow_new`), no lease.
- Never moves a bookmark the local view doesn't hold (same guard as today).

**Tests.** Bare origin; local trunk = content-equal hash-divergent twin of origin → assert
`force_with_lease` succeeds and origin ends at the local SHA. Then advance origin out-of-band and
assert a stale-lease push is **rejected** (not silently forced).

**gitman consumer.** `push --reset-origin` (content-gated, once-per-repo). Retires ANALYSIS §Amendment
4's raw-git escape — the surface stays in pyjutsu.

---

### P2 — Untrack / stop-tracking a path (+ auto-track exclusion)  *(must-have; unblocks `gitman untrack`)*

**Motivation.** Machine-local files that were committed before being gitignored — the recurring
offender is `.claude/settings.local.json` — stay **tracked**, so every lane appends to them (merge
conflicts) and every `save`/`land` re-snapshots them (trunk churn). gitman field reports 13 *and* 15
hit this; today's only fix is a 4-step `rm → save → restore-on-disk → land` dance (15-RC5).

**Current state.** No binding. The `snapshot.auto-track` fileset and jj's `jj file untrack` are not
exposed.

**Proposed API.**
```python
def untrack_paths(self, paths: list[str]) -> Operation | None: ...
    # Stop tracking each already-tracked path: remove it from @'s tree, LEAVE the file on disk.
    # Matches `jj file untrack`. No-op (returns None) if nothing was tracked.
```
Plus, to make exclusions durable so the next snapshot doesn't re-add the file:
```python
def auto_track_patterns(self) -> list[str]: ...          # read snapshot.auto-track fileset
def set_auto_track_patterns(self, patterns: list[str]) -> None: ...  # write it (config)
```

**jj-lib mechanism.** `jj file untrack` updates the working-copy `TreeState` (drops the path's
file-state) within a snapshot, and requires the path to be **excluded from `snapshot.auto-track`**
first, or the very next snapshot re-adds it. Confirm the jj-lib 0.42 entry points
(`WorkingCopy`/`TreeState` untrack + the `snapshot.auto-track` config surface). If writing the fileset
config in-process is awkward, the minimum viable P2 is `untrack_paths` alone, with gitman ensuring the
path is `.gitignore`d (a snapshot won't auto-track an ignored path).

**Semantics / edge cases.**
- Untracking a path that is *not* gitignored and *not* excluded → the next op re-tracks it. Document:
  untrack composes with an ignore/exclusion.
- File stays on disk (harness keeps working); only the tree entry is removed.

**Tests.** Track a file, gitignore it, `untrack_paths` → assert removed from `@`'s tree, present on
disk, and a subsequent `snapshot()` does **not** re-add it. Assert `git check-ignore` (colocated)
then reports it ignored (composes with P3).

**gitman consumer.** `gitman untrack <path>` (one op) + `init`/`status` warning for tracked-ignored
paths. Optionally register known machine-local paths at `init`.

---

### P3 — Total colocated sync: HEAD **and** index  *(correctness; verify-first)*

**Motivation.** gitman field report 15-RC6: after a `land`, colocated git `HEAD` stayed detached at
the *pre-land* trunk and the git **index still tracked a just-removed file**, so raw
`git check-ignore` misreported it as *not ignored* until a manual `git rm --cached`. Any raw-git tool
sharing the `.git` (editors, `check-ignore`, CI checkout) can be lied to by a stale index.

**Current state.** `git_export` already calls `git::reset_head(tx, wc_commit)` to keep HEAD detached
at `@`'s parent (`src/workspace.rs:1067-1079`), run unconditionally so a refs-only no-op still repairs
a stale HEAD. **Open question:** does jj-lib's `reset_head` also reset the git **index** to the
commit's tree, or HEAD only? If it resets the index, RC6 was purely a *missing/late export* +
*un-repositioned `@`* (both gitman-side), and P3 is a no-op verification. If it resets HEAD only, we
must add index sync.

**Proposed API (if needed).**
```python
def sync_colocated(self) -> None: ...
    # Idempotent: reset colocated git HEAD (detached at @'s parent) AND the index to match @'s
    # parent tree. Safe to call after any mutation; a no-op when already in sync.
```
Even if `reset_head` covers the index, exposing an explicit idempotent `sync_colocated` is worth it so
gitman can repair defensively without depending on `git_export` "having changes."

**jj-lib mechanism.** Inspect `git::reset_head` in jj-lib 0.42. If it only touches HEAD, reset the
index via `gix` from the target tree (jj-lib may expose an index-writing helper; otherwise gix
directly, staying inside pyjutsu — not a gitman subprocess).

**Tests.** Land a change that removes a tracked-then-ignored file; reposition `@`; assert colocated
`git check-ignore -v <file>` matches `.gitignore`, `git status` is clean, and the index tree == `@`'s
parent tree — with **no** raw `git rm --cached`.

**gitman consumer.** gitman calls `sync_colocated` (or relies on the verified `git_export`) after
every trunk move, alongside repositioning `@` onto advanced trunk. (The `@`-reposition itself is
gitman-side: `update_stale()` / `tx.new([trunk])` after `land`/`pull`.)

---

### P4 — Content-relation primitive  *(optional polish)*

**Motivation.** gitman's new content-aware `status` asks *"does `origin/<trunk>` hold a commit whose
content is absent from local trunk?"* — to replace the hash-based `behind_remote` count that today
drives a **data-loss-capable** `run gitman adopt` hint (15-RC2). gitman can already answer this with
existing revsets (`empty-after-rebase` / `not view.log("{trunk}..{remote_tip}")`), so this item is
*legibility*, not a blocker.

**Proposed API (pick the minimum that helps).**
```python
def is_ancestor(self, a: str, b: str) -> bool: ...   # cheap DAG check, clearer than a revset count
# (only if empty-after-rebase proves insufficient across squash/merge/rebase re-hash:)
def patch_id(self, rev: str) -> str: ...             # stable content id for cherry detection
```

**jj-lib mechanism.** `is_ancestor` is a trivial wrapper over jj's DAG. `patch_id` is *not* a native
jj concept (jj keys on change-id); it would hash the commit's diff — only add it if the
empty-after-rebase signal turns out to miss a real case. **Recommendation:** ship gitman's
content-`status` on existing revsets first; add `is_ancestor` only if the code reads cleaner with it;
defer `patch_id` unless a concrete miss appears.

**Tests.** n/a until an API is chosen.

---

### P5 — Annotated tag write  *(optional; retires the last raw-git surface)*

**Motivation.** `tags.py` is gitman's sole surviving `git` subprocess (create/push annotated release
tags), because jj-lib tag support is **read-only**. Retiring it makes gitman 100% in-process.

**Current state.** No binding; gitman shells `git tag -a` / `git push <remote> refs/tags/<tag>`.

**Proposed API.**
```python
def create_tag(self, name: str, target: str, message: str) -> None: ...  # annotated tag
def push_tag(self, remote: str, name: str) -> Operation | None: ...
```

**Mechanism.** jj-lib won't write tags, so implement via `gix`/`git2` directly against the colocated
`.git` (inside pyjutsu, still no gitman subprocess). Non-trivial (object write + ref + push).
**Recommendation:** **defer.** Low value vs. effort; `tags.py` is already narrow and safe. Track it,
don't build it yet.

---

## 7. What gitman needs that pyjutsu **already** provides (no work — just document)

So we don't over-scope, these are done:

- **Remote bootstrap (18):** `add_remote(name, url)` (`workspace.py:262`), `remotes()`,
  `remove_remote`, `rename_remote`, `set_remote_url` — all in-process. gitman's `remote add` is a thin
  wrapper; **no pyjutsu change.**
- **Trunk push sidesteps detached HEAD (18-RC2):** `git_push(remote, "<trunk>")` pushes the bookmark
  named `<trunk>` (jj maps it to `refs/heads/<trunk>`), so gitman never issues `git push HEAD`. **No
  pyjutsu change.**
- **Fetch + FF integration (`pull`):** `git_fetch(remote, bookmarks=…)` (`workspace.py:146`) with jj's
  string-pattern selection; jj auto-FFs a tracked local bookmark. **No pyjutsu change** (gitman just
  needs to *permit* the trunk FF under its guard — a gitman fix).
- **Detached-HEAD export:** `git_export` → `git::reset_head` (`workspace.rs:1067`). Present; P3 only
  extends it to the index if needed.
- **Default-branch discovery:** `git_default_branch(remote)` exists (used by `git_clone`), so gitman's
  `tags.py::remote_default_branch` raw-git call could later move in-process too.

---

## 8. Sequencing & release

1. **0.10.0-dev:** P1 (force-with-lease) + P2 (untrack + auto-track) — the two must-haves that unblock
   gitman Tier 2 (`push --reset-origin`, `untrack`).
2. **Same release:** P3 — first *verify* whether `reset_head` covers the index; add `sync_colocated`
   only if it doesn't. Unblocks gitman Tier 1's check-ignore honesty.
3. **Optional/later:** P4 `is_ancestor` (only if it improves gitman's content-`status` readability); P5
   deferred.

Cut `0.10.0` once P1–P3 land with probes. gitman then consumes it via the `devenv.nix` pin.

## 9. Test strategy

In-process pytest against a **bare origin + colocated work repo**, matching the existing pyjutsu probe
pattern (`Workspace.init(path, colocate=True)`; `git` is on PATH for bare-remote setup only). Each
item's probe asserts the *gitman* symptom it retires (stale-lease rejection, no-re-add-after-untrack,
truthful `check-ignore`), not just the happy path.

## 10. Open questions

- **P1:** where does jj 0.42 enforce the non-FF refusal (CLI vs `push_refs`/`GitPushOptions`)? Flag
  flip vs. custom ref-target build.
- **P2:** exact jj-lib 0.42 entry points for `file untrack` and reading/writing `snapshot.auto-track`.
  Is `untrack_paths` alone (relying on gitignore) enough for gitman, or do we need the fileset writers?
- **P3:** does `git::reset_head` reset the index, or HEAD only? (Determines whether P3 is code or just
  a verification test.)
- **P4:** does gitman's `empty-after-rebase` content check ever miss a real case that would justify a
  native `patch_id`? (Default assumption: no.)
- **Naming:** `force_with_lease=` flag on `git_push` vs. a distinct `git_push_force_with_lease(...)`
  method. (Leaning: a flag, to keep one push entry point.)

## Cross-refs

- gitman analysis: `gitman/.scratch/projects/19-trunk-model-deep-dive/ANALYSIS.md` (ADDENDUM).
- gitman decision being refined: `gitman/.scratch/projects/16-local-authored-trunk-model/DECISION.md`.
- Field reports these bindings retire: gitman projects 13 (raw-push desync), 15 (force-audit /
  untrack / HEAD-lag), 18 (bootstrap).
