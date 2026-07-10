# 13 — PLAN: pyjutsu 0.10.0 bindings for gitman's single local-authored trunk model

**Date:** 2026-07-09
**Status:** PLAN — ready to build. Synthesised from three read-only investigations of jj-lib
0.42.0 source (B1/B2/B3 below). Answers all three §10 open questions in `OVERVIEW.md`.
**Version target:** pyjutsu `0.9.0` → `0.10.0`.
**jj-lib pin:** `=0.42.0` (`Cargo.toml:16`). **No item needs a newer jj-lib** — the pin holds.

> jj-lib 0.42 source referenced throughout:
> `/home/andrew/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/jj-lib-0.42.0/`.
> pyjutsu surface: Rust in `src/workspace.rs` (all git/wc verbs are `Workspace` methods),
> Python facade in `python/pyjutsu/workspace.py`, type stubs in
> `python/pyjutsu/_pyjutsu.pyi`, tests under `tests/`.

---

## Headline: the release is mostly *verification*, not new code

The investigations **shrink** 0.10.0 sharply from the OVERVIEW's assumptions:

| Item | OVERVIEW assumption | Verified reality (jj-lib 0.42) | 0.10.0 work |
|------|--------------------|--------------------------------|-------------|
| **P1** force-with-lease | jj refuses non-FF before `push_refs`; add a flag or rebuild targets | **No FF guard exists.** jj-lib *always* force-pushes with `--force-with-lease`; pyjutsu already supplies the lease and surfaces failures as `GitError`. **The feature already ships.** | **Probe test + docstring correction.** No new API. |
| **P2** untrack | bind `jj file untrack` + maybe `snapshot.auto-track` writers | No native `untrack` API; compose from public tree/wc primitives. auto-track writers **not needed** (gitignore fires first). | **The only substantial new code:** one `untrack_paths` verb. |
| **P3** colocated sync | maybe add index reset if `reset_head` is HEAD-only | `reset_head` **already resets HEAD *and* the git index** (unconditional `reset_index` → writes `.git/index` via gix). | **Verification test** + thin optional `sync_colocated()` verb. |

Net: **P2 is the one real feature.** P1 and P3 are correctness/regression tests plus small
ergonomic surface. This is the single most important finding — the OVERVIEW's P1 premise
("jj refuses a non-fast-forward bookmark move before it ever reaches `push_refs`") is **wrong
for jj-lib 0.42** and must not drive an implementation.

---

## Resolved open questions (§10)

### Q-P1 — Where does jj 0.42 enforce the non-FF push refusal?  **It doesn't.**

jj-lib 0.42 has **no** fast-forward / ancestor / "move-backward" check anywhere in its push
path. Every push it performs is a test-and-set force-with-lease:

- `git::push_refs(mut_repo, subprocess_opts, remote, targets, callback, options)`
  (`git.rs:3176`) lowers each `(RefNameBuf, Diff{before, after})` from `GitPushRefTargets`
  (`git.rs:3152`) into a `GitRefUpdate` and calls `push_updates` (`git.rs:3291`).
- `push_updates` **always** emits `RefSpec::forced(...)` — inline comment: *"We always
  force-push. We use the push_negotiation callback … to check that the refs did not
  unexpectedly move on the remote."* (`git.rs:3306-3309`).
- Subprocess layer confirms: *"All pushes are forced, using --force-with-lease to perform a
  test&set operation on the remote"* (`git_subprocess.rs:258-261`); it passes
  `--force-with-lease=<lease>` per ref (`git_subprocess.rs:290-294`) plus the **un**-forced
  refspec so the lease is honoured (`git_subprocess.rs:296-302`).
- `GitPushOptions` (`git.rs:3169`) carries **only** `remote_push_options: Vec<String>` — no
  force field, no FF toggle. Nothing to flip.
- The only "refuse to move a bookmark backward" prompt lives in the **jj CLI crate** (not
  vendored, not jj-lib). pyjutsu calls `push_refs` directly and is **not** subject to it.

**The lease.** The expected-old-oid is the `before` field of each `Diff` — pyjutsu already
sets it to the remote-tracking bookmark position (named path `workspace.rs:1289-1297,1327`;
bulk path `workspace.rs:1267-1281`). On mismatch, git returns the ref in
`GitPushStats.rejected` (`git.rs:185-196`, reason e.g. `(stale info)`/`(failure lease)`),
`all_ok()` is false (`git.rs:198-203`), and pyjutsu already maps that to `GitError`
(`workspace.rs:1354-1367`).

**Consequence:** a **content-equal, hash-divergent** local trunk (gitman's migration case)
already pushes successfully *today* — provided remote-tracking is current (i.e. a prior
`git_fetch`). The "refusal" gitman saw by hand was git's own lease check, which is the exact
safety property we want. → see P1 plan.

### Q-P2 — Entry points for `file untrack` + `snapshot.auto-track`.

**No dedicated jj-lib `untrack` API.** `jj file untrack` is composed from public primitives.
Untracking = remove path from `@`'s tree **and** drop its working-copy file-state, leaving the
file on disk:

1. Build a matcher from the paths (fileset parse, as pyjutsu already does at
   `workspace.rs:532`).
2. `MergedTreeBuilder::new(wc_tree)` (`merged_tree_builder.rs:44`); for each entry from
   `MergedTree::entries_matching(matcher)` (`merged_tree.rs:261`) call
   `set_or_remove(path, Merge::absent())` (`merged_tree_builder.rs:55`); then
   `write_tree().await` (`merged_tree_builder.rs:60`).
3. `mrepo.rewrite_commit(&wc_commit).set_tree(new_tree).write()` (+ `rebase_descendants`),
   the idiom pyjutsu already uses at `workspace.rs:618`.
4. **The load-bearing call:** `LockedWorkingCopy::reset(&new_commit)` (trait
   `working_copy.rs:129`; impl `local_working_copy.rs:2459`). It diffs old→new tree; paths
   absent in the new tree go to `deleted_files` (`:2469`) and are stripped from `file_states`
   via `merge_in(...)` (`:2508`). **It never touches the file on disk** — matches
   `jj file untrack` exactly. This makes `maybe_current_file_state.is_none()` true for the
   path on the next snapshot.
5. `locked_ws.finish(op_id)` (`working_copy.rs:151`).

**`snapshot.auto-track` is just a config string, not a typed API.** It is read ad-hoc and
compiled to a matcher passed as `SnapshotOptions.start_tracking_matcher`
(`working_copy.rs:222`) — pyjutsu already does this at `workspace.rs:516-535` (`get_string
("snapshot.auto-track")`, default `"all()"`, `fileset::parse(...).to_matcher()`). There is no
separate typed writer to bind; "writing it" = writing a config-layer string.

**Gitignore fires first.** In snapshot's per-file decision (`local_working_copy.rs:1642-1691`):
if a path is **not currently tracked** AND gitignored → dropped at `:1651`, *before* and
*independently* of the auto-track check at `:1653`. So a `.gitignore`d path is **never**
re-added, regardless of `snapshot.auto-track` (even the default `all()`). `reset` (step 4)
supplies the required `is_none()` precondition.

**Verdict:** `untrack_paths` alone + gitman's `.gitignore` write is the minimum-viable, correct
path. `auto_track_patterns`/`set_auto_track_patterns` writers are **not needed** for gitman and
are **dropped** from 0.10.0 (tracked as future polish; see P2 below).

### Q-P3 — Does `git::reset_head` reset the index, or HEAD only?  **Both.**

`git::reset_head(mut_repo, wc_commit)` (`git.rs:1745`):
- moves HEAD to `wc_commit`'s first parent **only if it changed** (`git.rs:1760-1780`,
  conditional), then
- **unconditionally** calls `reset_index(mut_repo, &git_repo, wc_commit).await`
  (`git.rs:1789`).

`reset_index` (`git.rs:1825`) rebuilds a fresh `gix::index::File` from `@`'s **parent** tree
(`git.rs:1830,1846-1848`), records intent-to-add for new-in-`@` files (`:1855`), and **writes
it to `.git/index`**: `index.write(...)` (`git.rs:1874-1876`) — a direct filesystem write,
independent of whether the jj transaction later commits. A file removed by a landed change is
dropped from the index by this write.

Backend is **gix (gitoxide) 0.84**, shared with pyjutsu (`get_git_repo` → `gix::Repository`,
`git.rs:423`; pyjutsu `Cargo.toml:37` `gix = "=0.84.0"`). No `git2`.

pyjutsu already reaches this: `git_export` calls `reset_head` at `workspace.rs:1078`, **before**
the `has_changes()` early-return (`:1080`), so the index write runs on every export including
refs-only no-ops. (`reset_index`/`build_index_from_merged_tree` are private — `reset_head` is
the only public entry point, and it's the right one.)

**Verdict:** P3's "index still tracked a removed file" symptom is **already fixed** by the
existing `reset_head` call. P3 is a **regression test**, not new code. An explicit idempotent
`sync_colocated()` is still worth exposing (small) so gitman can repair defensively without
needing a refs mutation to trigger it.

### Q-P4 — does `empty-after-rebase` ever miss a real case justifying `patch_id`?

Not investigated (out of B scope, and OVERVIEW defers P4). Default assumption stands: **no**.
Tracked-not-built. See below.

### Q-Naming — `force_with_lease=` flag vs separate method.

Given Q-P1: force-with-lease is **already the effective default** of `git_push`, so a
`force_with_lease=True` flag would be actively misleading (`False` would falsely imply "no
safety"). **Decision: add no force flag.** Keep the single `git_push` entry point as-is (it is
already lease-checked). If a *blind* lease-less force is ever needed (it is not, for gitman),
expose it later as a distinctly-named `git_push_force(...)` — but jj-lib 0.42 cannot do a
lease-less force in-library (`spawn_push` hardcodes `--force-with-lease` and rejects a leading
`+`, `git_subprocess.rs:290-302`), so that would require an `ls-remote`-derived lease or a
subprocess — explicitly out of scope.

---

## 0.10.0 implementation plan (sequenced)

### Item 1 — P1: prove & document force-with-lease (verification-first)  *[do first — cheapest, de-risks the release]*

**No new API.** The work is to confirm the mechanism and correct the record.

- **Probe test** (`tests/test_git_force_with_lease.py`, new): bare origin + colocated work
  repo (per §9). (a) Make local trunk a **content-equal, hash-divergent twin** of
  `origin/trunk` (e.g. commit, push, then rewrite the commit description or amend so the SHA
  changes but the tree is equal — or rebase-in-place). `git_fetch` so remote-tracking is
  current. Assert `git_push(remote, "trunk")` **succeeds** and origin's ref now equals the
  local SHA. (b) Advance origin **out-of-band** (raw git push from a second clone, or a direct
  bare-repo update). Assert a subsequent `git_push(remote, "trunk")` is **rejected** → raises
  `GitError` mentioning the stale/lease reason (not a silent clobber).
- **Docstring correction** — `python/pyjutsu/workspace.py` `git_push` docstring + the Rust doc
  comment near `src/workspace.rs:1206`: remove/replace the "Force-push … remain out of scope"
  claim. State the real contract: *every push is force-with-lease against the last-fetched
  remote-tracking ref; a non-fast-forward move succeeds iff the lease holds; fetch first.*
- **If (and only if) the probe (a) unexpectedly fails** — i.e. some pyjutsu-side guard rejects
  the non-FF move — THEN, and only then, investigate that specific guard (it would be in
  pyjutsu's own target-build in `git_push`, `src/workspace.rs:1207+`, not in jj-lib). Treat an
  unexpected failure here as the trigger to escalate; do not pre-build a bypass.
- **gitman symptom retired:** ANALYSIS §Amendment 4's raw `git push --force-with-lease` escape
  for `push --reset-origin`; gitman's own `main` being `1 behind / 1 ahead origin`
  (field reports 13/15). The surface now lives in pyjutsu with no new flag — gitman calls
  `git_fetch` then `git_push(remote, trunk)` under its content-gate.

### Item 2 — P2: `untrack_paths` (the one real feature)

- **Rust** — new `Workspace::untrack_paths` in `src/workspace.rs`, modelled on the existing
  `snapshot` flow (`workspace.rs:489-599`) and the `set_tree` rewrite idiom
  (`workspace.rs:618-624`):
  1. `start_working_copy_mutation()` + `WorkingCopyFreshness::check_stale` (as
     `workspace.rs:561-586`).
  2. Optionally `snapshot(&options)` first to capture current disk state (what the CLI does).
  3. Build matcher from `paths` (`fileset::parse` → matcher, as `workspace.rs:532`).
  4. `MergedTreeBuilder::new(wc_tree)`; for each `entries_matching(matcher)` →
     `set_or_remove(path, Merge::absent())`; `write_tree().await`.
  5. `rewrite_commit(&wc_commit).set_tree(new_tree).write()` + `rebase_descendants()` +
     `tx.commit(...)`.
  6. `locked_wc.reset(&new_commit)` (drops file-state, file stays on disk).
  7. `locked_ws.finish(op_id)`.
  Return `Option<Operation>` (`None` when nothing was tracked → no-op, matching OVERVIEW).
  Reuse existing `map_workingcopy_err` / `map_git_err`. **Hand-match surrounding style; do not
  run bare `cargo fmt`.**
- **Python facade** — `untrack_paths(self, paths: list[str]) -> Operation | None` in
  `python/pyjutsu/workspace.py`, thin wrapper over the extension method (mirror how `snapshot`
  is wrapped).
- **`.pyi`** — add the signature to `python/pyjutsu/_pyjutsu.pyi` (and re-export as needed).
- **Optional hardening (recommend include):** after untrack, do a trial `snapshot` and inspect
  `SnapshotStats.untracked_paths` (`working_copy.rs:239-256`); if a just-untracked path is
  *not* now ignored/excluded (would be re-added next snapshot), surface a warning path — this
  mirrors the CLI's safety guard. Keep it non-fatal (gitman gitignores first).
- **Test** (`tests/test_untrack.py`, new): track a file, commit; `.gitignore` it;
  `untrack_paths([f])` → assert removed from `@`'s tree, **present on disk**, and a subsequent
  `snapshot()` does **not** re-add it. Assert colocated `git check-ignore <f>` reports it
  ignored (composes with P3).
- **Dropped from 0.10.0:** `auto_track_patterns` / `set_auto_track_patterns` (Q-P2: not needed
  — gitignore fires first). Track as future polish (promotion trigger below).
- **gitman symptom retired:** the 4-step `rm → save → restore-on-disk → land` dance for
  tracked-then-ignored machine-local files, e.g. `.claude/settings.local.json` (field reports
  13 & 15, 15-RC5). gitman gets `gitman untrack <path>` = one op.

### Item 3 — P3: verify colocated HEAD+index sync; expose `sync_colocated()`

- **Regression test** (`tests/test_colocated_sync.py`, new): in a colocated repo, land a change
  that **removes** a tracked-then-ignored file; reposition `@` (`tx.new([trunk])`); call
  `git_export` (or `sync_colocated`); assert colocated `git check-ignore -v <file>` matches
  `.gitignore`, `git status` is clean, and the git index tree == `@`'s parent tree — with **no**
  raw `git rm --cached`. This locks in the B3 finding as a guarantee.
- **`sync_colocated()` verb (thin, recommended):** new `Workspace::sync_colocated` in
  `src/workspace.rs` that resolves the workspace `@` commit and calls
  `git::reset_head(tx.repo_mut(), &wc_commit)` (exactly the existing `git_export` call at
  `workspace.rs:1078`), committing the op — **without** requiring a refs change to trigger it.
  Idempotent / no-op when already in sync (reset_head is). Facade
  `sync_colocated(self) -> None` in `workspace.py`; add to `_pyjutsu.pyi`.
- **gitman symptom retired:** 15-RC6 (detached HEAD stuck pre-land + stale index lying to
  `git check-ignore`). gitman calls `sync_colocated()` after every trunk move (alongside the
  gitman-side `@`-reposition via `update_stale()` / `tx.new([trunk])`).

### Release

Cut **`0.10.0`** once Items 1–3 land green (`devenv shell -- python -m pytest tests/ -q`).
Bump the build-derived version (0.9.0 introduced the build-derived guard — follow that same
mechanism; check the 0.9.0 bump commit `0c143a0` for the exact spot). gitman then pins it via
`devenv.nix`.

---

## P4 / P5 — tracked, not built

- **P4 — content-relation primitive (`is_ancestor` / `patch_id`).** Ship gitman's content-aware
  `status` on existing revsets (`empty-after-rebase` / `not view.log("{trunk}..{remote_tip}")`)
  first. **Promotion trigger:** add `is_ancestor(a, b) -> bool` (trivial DAG wrapper) only if
  gitman's `status` code reads materially cleaner with it; add `patch_id` **only** if a concrete
  case surfaces where `empty-after-rebase` misses a real content match across squash/merge/rebase
  re-hash. Until then: not built.
- **P5 — annotated tag write (`create_tag` / `push_tag`).** jj-lib is read-only on tags; this
  needs a direct gix object-write + ref + push against the colocated `.git`. **Promotion
  trigger:** only when gitman decides to retire its last raw-git surface (`tags.py`) — low value
  vs. effort today. Not built.
- **`set_auto_track_patterns` config writer.** **Promotion trigger:** only if a gitman use case
  needs to untrack a path it deliberately keeps *un-ignored* (leave it visible as untracked in
  `git status`). Gitman's model gitignores machine-local files, so this never arises. Not built.

## Blockers / pin check

**None.** Every 0.10.0 item is expressible against jj-lib `=0.42.0`; the pin holds. The only
"blocker" surfaced is conceptual, not technical: **the OVERVIEW's P1 premise is wrong for
jj-lib 0.42** (there is no FF guard to bypass), which *reduces* rather than blocks scope. Build
Item 1's probe first to confirm the already-shipping behaviour before touching anything else.

## Source citations (jj-lib 0.42.0 unless noted)

- **P1:** `git.rs:185-203, 264-276, 299-332, 3152-3174, 3176-3224, 3291-3320`;
  `git_subprocess.rs:258-307, 574-655`; pyjutsu `src/workspace.rs:1267-1297, 1324-1367`.
- **P2:** `working_copy.rs:109-155, 222, 239-256`; `merged_tree_builder.rs:44,55,60`;
  `merged_tree.rs:261`; `local_working_copy.rs:1642-1691, 2459-2511, 2863`; `settings.rs:68,72`;
  pyjutsu `src/workspace.rs:489-599, 516-535, 561-586, 599, 618-624`.
- **P3:** `git.rs:423, 1745-1789, 1825-1877`; pyjutsu `src/workspace.rs:1043-1080`,
  `Cargo.toml:37`.
