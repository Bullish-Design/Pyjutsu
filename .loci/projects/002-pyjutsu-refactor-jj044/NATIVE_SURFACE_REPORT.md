---
title: Non-jujutsu surface — removal plan
type: report
status: draft
project: 002-pyjutsu-refactor-jj044
---

# Non-jujutsu surface — removal plan

## Purpose

Phase 2.5 recorded *where* Pyjutsu reaches past jj-lib. This report answers the
next question: **which of those sites must exist at all.**

The rule applied here is stricter than the Phase 2.5 rule. Phase 2.5 asked
"does jj-lib provide this?" This report asks "does **jujutsu** provide this?"
A feature that jj itself does not offer is not a gap in the binding. It is
scope that Pyjutsu should not carry.

Evidence comes from the local crate sources, not documentation:

```text
~/.cargo/registry/src/index.crates.io-*/jj-lib-0.42.0
~/.cargo/registry/src/index.crates.io-*/jj-lib-0.44.0
```

## Verdict table

| # | Site | gix sites | Verdict |
|---|------|-----------|---------|
| 1 | `src/workspace/tags.rs` — annotated tag write | 7 | **REMOVE.** Replace with jj-lib lightweight tags. |
| 2 | `repo_view.rs::patch_id_hex` — `gix::hash` | 1 | **REPLACE.** Use the `sha1` crate. Digest is unchanged. |
| 3 | `workspace.rs:18` — `gix::remote::fetch::Tags` | 1 | **REMOVE.** The 0.44 `add_remote` signature drops the parameter. |
| 4 | `revset.py::_quote` | 0 | **REPLACE.** Bind `jj_lib::dsl_util::escape_string`. |
| 5 | `apply_head_ref_packed` + `write_git_ref` + `delete_git_ref` | ~17 | **MOVE OUT.** Git ref repair, no jj concept. Deprecate. |
| 6 | `prune_orphaned_keep_refs` | 6 | **REPLACE.** Bind `Store::gc` as `Workspace.gc`. See second pass. |
| 7 | `git_refs` | 5 | **KEEP.** Read-only drift detection. Cheap and stable. |
| 8 | `remotes` — fetch URL read | 2 | **KEEP.** jj-lib exposes no URL reader in 0.42 or 0.44. |
| 9 | `ensure_jj_git_excluded` | 0 (std::fs) | **KEEP.** jj-cli parity, plain file I/O. |
| 10 | trunk `HEAD` write | 0 (std::fs) | **KEEP.** jj-cli parity, three lines, no gix. |
| 11 | `src/config/revsets.toml` | 0 | **KEEP.** Vendored from jj-cli. No alternative. |
| 12 | `hooks.py`, `run_jj` | 0 | **KEEP.** Pyjutsu originals. |

Direct gix call sites: **33 today → 13 after items 1–3 → 7 after item 6 →
7 after item 5** (item 5's sites move to the consumer, not to another Pyjutsu
module). The seven survivors are `git_refs` (5) and `remotes` (2). Both are
read-only, and neither lets a gix type cross the FFI.

## 1. Annotated tags — remove

### Why

Jujutsu has no annotated-tag concept. `MutableRepo` carries exactly one tag
writer, and it writes a lightweight tag:

```text
jj-lib 0.42  repo.rs:1817   pub fn set_local_tag_target(&mut self, name, target)
jj-lib 0.44  repo.rs:1850   pub fn set_local_tag_target(&mut self, name, target)
```

Pyjutsu's annotated tag is therefore not a binding of jj. It is a Git feature
that Pyjutsu adds on top of jj, written straight to the object database with
`gix::Repository::tag`. It is also the single largest gix liability per line of
value in the crate.

### Replacement

The whole path already exists in jj-lib, in both 0.42 and 0.44:

1. Resolve the target revset to one commit (unchanged code).
2. `tx.repo_mut().set_local_tag_target(name, RefTarget::normal(commit_id))`.
3. `git::export_refs(tx.repo_mut())` writes `refs/tags/<name>`
   (`git.rs:1287`, dispatching `GitRefKind::Tag` through `export_refs_to_git`).
4. `tx.commit(...)` publishes the operation.

`push_tag` needs **no change**. It is already pure jj-lib
(`git::push_refs` with `GitPushRefTargets.tags`).

### What does not break

Annotated tags that arrive from a remote keep working. Import peels tag objects
(`git.rs:449-476`), and export copies an existing annotated object rather than
overwriting it with the commit oid (`find_git_tag_oid_to_copy`, `git.rs:1408`).
Pyjutsu loses the ability to **author** an annotated tag. It keeps the ability
to carry one.

### What breaks

`Workspace.create_tag(name, target, message, force)` loses `message`. A
lightweight tag has no message and no tagger. This is a public API change and
needs a Pyjutsu minor version note. Options, in preference order:

1. Drop the parameter. Honest signature, one-line consumer edit.
2. Keep it and raise on a non-empty value. Louder, but keeps a dead parameter.

Recommend option 1.

`tests/test_tags.py` asserts `cat-file -t refs/tags/v1.0 == "tag"` and a
`tagger` line. Rewrite the file: the oracle becomes `jj tag list` plus
`cat-file -t == "commit"`. The comment "Tags are pyjutsu's last raw-git
surface" becomes true in the opposite direction — after this change, tags are
pure jj.

### Effect

`src/workspace/tags.rs` drops all 7 gix sites and roughly half its length. The
gix 0.84 → 0.85 port no longer has to touch tag creation at all.

## 2. `patch_id_hex` — replace the hasher

`repo_view.rs:429` calls `gix::hash::hasher(gix::hash::Kind::Sha1)`. That
hasher is a plain SHA-1 state with no Git object framing. The `sha1` crate
produces byte-identical output, so **patch ids do not change**.

This also retires finding F1. With this edit, Pyjutsu calls no gix hashing API,
so the `features = ["sha1"]` line planned for the pin move is not needed.
Delete that step from the upgrade order.

F2's decision stands unchanged: the digest stays SHA-1 in every repository,
including SHA-256 ones, and the docstring already says so.

## 3. `gix::remote::fetch::Tags` — free with the bump

`jj-lib` 0.44 removes the `fetch_tags` parameter from `git::add_remote`
(0.42 `git.rs`: five parameters; 0.44 `git.rs:2371`: four). The import at
`workspace.rs:18` then has no user. No design decision is needed.

## 4. `_quote` — bind the real function

`jj_lib::dsl_util::escape_string` is public in **both** 0.42 and 0.44
(`dsl_util.rs:474`), and `dsl_util` is a public module (`lib.rs:50`). The hand
port in `python/pyjutsu/revset.py` is unnecessary duplication.

Expose it as a small native function and call it from `revset.py`. This deletes
finding F5 and removes one entry from the per-upgrade re-verification list. The
vendored list shrinks to `src/config/revsets.toml` and `NO_GC_REF_NAMESPACE`.

## 5. Git ref repair — move out of Pyjutsu

`write_git_ref`, `delete_git_ref`, and their engine `apply_head_ref_packed` are
the deepest coupling in the crate. They drive the gix file-store transaction
API directly, disable the reflog, clear the loose `refs/heads/` tree, and pack
every head to survive directory/file conflicts in fractal lane names.

None of this is jujutsu. It is `git update-ref` plus `git pack-refs`, written
against a private-feeling corner of gix. Phase 2.5 already flags it as the one
call site that needs its own port task on every gix bump (F4).

The caller is gitman's reconcile path. `git update-ref --stdin` solves the same
D/F problem natively, and gitman already depends on the git binary.

Recommendation: **deprecate, then remove.** Do not do it inside this project.
The removal is a downstream-visible API change with a real consumer, so it
deserves its own lane after the 0.44 bump is green:

1. Mark both methods deprecated in `python/pyjutsu/workspace.py`.
2. Port gitman's reconcile path to `git update-ref --stdin`.
3. Delete the methods, `apply_head_ref_packed`, `HeadRefOp`, and
   `tests/test_git_ref_write.py`.

Until step 3, the code must still be ported to gix 0.85. Budget it as F4 says.

## 6–8. What stays, and why

**`prune_orphaned_keep_refs`.** jj-lib owns `refs/jj/keep/` but exposes no
narrow purge. `Store::gc` (`store.rs:255`) runs a full backend collection with
object pruning — wrong cost and wrong side effect. Keep the vendored
`NO_GC_REF_NAMESPACE` literal and the re-verification rule.

One correction: the doc comment at `workspace.rs:244` calls this "a narrow
purge on every load". It is not. The only caller is `adopt_existing_git`
(`workspace.rs:136`). Fix the comment.

**`git_refs`.** Read-only. Reading the on-disk refs and seeing them differ from
jj's `@git` view is the whole feature, so no jj-lib API can replace it. Five
stable gix calls, no low-level transaction work.

**`remotes`.** jj-lib 0.44 has `set_remote_urls` (`git.rs:2599`) but no
reader; `get_all_remote_names` returns names only. The gix fetch-URL read
stays. No gix type crosses the FFI, so the blast radius is one function.

Items 9–12 need no change. `ensure_jj_git_excluded` and the trunk `HEAD` write
are plain `std::fs` calls that mirror `jj git init --colocate`; neither touches
gix, and jj-lib's `reset_head` is not a substitute (it detaches HEAD at the
working-copy parent, a different operation).

## Second pass on the four "keep" items

The first pass kept items 5–8 on the ground that jj-lib offers no substitute.
That test is too weak. Ask instead what each one *provides*, and why it entered
a jj binding at all.

`docs/PYJUTSU_CONCEPT.md` §12 fixes the designed v1 git surface at
`git_fetch`, `git_push`, `git_export`, `git_import`, `sync_colocated`. Every
item below arrived later, from gitman's reconcile path (projects 13 §P5 and
14 §P2/§P4). None was designed in. That is the shape of the problem.

### `remotes` — necessary, keep

Provides `jj git remote list`: each remote's name and fetch URL. This **is**
jujutsu functionality. jj-lib exposes `get_all_remote_names` and
`set_remote_urls` (`git.rs:2599`) but no URL reader in either 0.42 or 0.44, so
jj-cli must read it from the git config the same way. Pyjutsu makes the same
call jj-cli makes.

Two gix sites, read-only, no gix type crosses the FFI. Keep it.

### `git_refs` — necessary, keep, but know what it is

Provides on-disk ref truth. jj cannot answer this question: jj's answer to ref
drift is `jj git import`, which *resolves* the drift instead of reporting it.
`tests/test_git_refs.py` is explicit — an out-of-band `git update-ref` must be
visible through `git_refs` while still absent from `bookmarks()`.

A zero-gix route exists: run `git::import_refs` into a transaction, read
`tx.repo().view().git_refs()`, and drop the transaction without committing.
Do **not** take it. It runs the full import — descendant rebasing and
unreachable-commit abandonment — inside a throwaway view, to answer a read-only
question. The gix version is a `references().prefixed()` walk plus
`peel_to_id()`: five calls, the most stable corner of the gix API, and the same
call shape jj-lib uses in `recreate_no_gc_refs`. Its port cost per gix bump is
near zero.

Keep the gix walk. It is a Pyjutsu-original observability feature, and it
should be documented as one rather than as a jj-lib gap.

### `prune_orphaned_keep_refs` — **not** necessary, replace

This is the weakest of the four, and the first pass was wrong to keep it.

What it provides is disk hygiene, and only that. Its own doc comment concedes
the point: `refs/jj/keep/*` cannot resurrect a commit as a visible head, and
the import path confirms it — `diff_refs_to_import` (`git.rs:940-1000`) scans
local branches, remote branches, tags, and `refs/jj/keep` is not among them.
The incident that motivated it (project 10 §P1) was caused by a **tag**, and
was fixed consumer-side in gitman. What survives is the accumulation cleanup.

The price is high for that: six gix sites, a vendored copy of jj-lib's private
`NO_GC_REF_NAMESPACE`, and a permanent per-upgrade re-verification obligation
on a constant Pyjutsu is not allowed to import.

jj already owns this job. `Store::gc` is public (`store.rs:255`), takes
`&dyn Index` — available from `Repo::index()` (`repo.rs:361`) — and a
`SystemTime` cutoff. It is what `jj util gc` runs, and its
`recreate_no_gc_refs` step (`git_backend.rs:851`) does this prune correctly as
a side effect.

Recommendation: delete `prune_orphaned_keep_refs` and add
`Workspace.gc(keep_newer=...)` binding `Store::gc`. This trades a private
constant and a bespoke ref transaction for a public jj-lib API, and adds a real
jj feature Pyjutsu is missing.

The behavior change is honest and small: after a re-adopt, orphaned keep refs
survive until the next `gc()` instead of being purged during the adopt. Nothing
imports them and nothing displays them; they hold dead objects on disk.

If that is judged unacceptable, the fallback is to call `Store::gc` from
`adopt_existing_git` rather than the hand-rolled walk. That is slower — it is a
full backend collection — but it still deletes the vendored constant.

### `write_git_ref` / `delete_git_ref` — confirmed move-out

The second pass strengthens item 5 rather than changing it. These are the
clearest case: `git update-ref` plus `git pack-refs`, no jj concept anywhere,
never in the designed surface, ~17 gix sites, and the one call site Phase 2.5
says needs its own port task on every gix bump.

## Recommended order

Items 1, 2, and 4 delete code that would otherwise need porting to gix 0.85.
Do them **before** the pin moves.

| Lane | Content | Blocks the bump? |
|------|---------|------------------|
| `jj044-refactor/patch-id-hash` | Item 2 — `sha1` crate. No behavior change. | No, but do it first: it is the smallest. |
| `jj044-refactor/escape-string` | Item 4 — native `escape_string` binding. | No. |
| `jj044-refactor/lightweight-tags` | Item 1 — remove annotated tags. API change. | **Yes.** Do it before the bump. |
| `jj044-refactor/native-gc` | Item 6 — `Workspace.gc` replaces the keep-ref prune. | **Yes.** It deletes a vendored constant the bump would re-verify. |
| Phase 3 pin move | Item 3 falls out for free. | — |
| Post-bump lane | Item 5 — deprecate ref repair. | No. |

## Changes to the Phase 2.5 record

- **F1 is retired** by item 2. Do not add `features = ["sha1"]` at the pin move.
- **F5 is retired** by item 4. The re-verification list loses `_quote`.
- **F3 is retired** by item 6. The re-verification list loses
  `NO_GC_REF_NAMESPACE`, and the upstream request to export the constant is no
  longer needed. The list shrinks to `src/config/revsets.toml` alone.
- **F4 is unchanged and still applies.** `apply_head_ref_packed` must be ported
  to gix 0.85 because its removal lands after the bump.
- The Phase 2.5 inventory's row 1 changes class from GAP to **out of scope**.
