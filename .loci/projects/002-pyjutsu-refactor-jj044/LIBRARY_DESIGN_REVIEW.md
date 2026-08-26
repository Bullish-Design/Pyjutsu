---
title: Library design review — remaining gix, and what is missing
type: report
status: draft
project: 002-pyjutsu-refactor-jj044
---

# Library design review

A fresh reading, with no carry-over from
[[NATIVE_SURFACE_REPORT.md]]. Part 1 re-asks whether the surviving `gix` code
should exist. Part 2 asks the opposite question: what does jj offer that
Pyjutsu does not.

Evidence is the local crate source of jj-lib 0.44.0 and the Pyjutsu tree at
commit `dabe76a`.

## Part 1 — the surviving gix, re-examined

### The ref-repair trio is one feature, not three

The earlier report kept `git_refs` and moved out `write_git_ref` /
`delete_git_ref`. That split is wrong.

`git_refs` is a **detector**. `write_git_ref` and `delete_git_ref` are the
matching **repair**. They exist for one workflow — gitman's reconcile: read the
on-disk refs, compare against jj's view, force the difference away. A detector
with no repair has no caller. Splitting them leaves five gix sites in Pyjutsu
to serve a workflow that now lives somewhere else.

They move together, or they stay together.

**Is the functionality necessary?** Not to a jj binding. Nothing in it is a jj
concept. `git_refs` is `git for-each-ref`; the writers are
`git update-ref --stdin`. The 80-line `apply_head_ref_packed` exists purely to
reimplement, through the gix file-store transaction API, the directory/file
handling that `git update-ref` performs natively.

**The usual objection does not hold.** One might keep them to avoid depending
on the git binary. Pyjutsu already depends on it: `git_fetch`, `git_push`, and
`push_tag` all build `GitSubprocessOptions::from_settings` and jj-lib shells
out to `git` for the network (`git_subprocess.rs`), and `git_default_branch`
runs a `git` subprocess directly. The no-git-binary property was already false
before this review.

**Best implementation:** move all three to gitman as two subprocess calls.
Pyjutsu keeps `git_import` / `git_export` / `sync_colocated`, which are the jj
answers to ref drift and are pure jj-lib. This removes 22 of the 33 gix sites
and deletes the one call site Phase 2.5 flags as needing its own port task at
every gix bump (F4).

### `remotes` — necessary, and gix is right

Provides `jj git remote list`: name plus fetch URL. Pyjutsu already binds
`add_remote`, `remove_remote`, `rename_remote`, and `set_remote_url` to jj-lib.
Dropping the reader would leave write-only remote CRUD, which is not a
defensible API.

jj-lib 0.44 has `set_remote_urls` (`git.rs:2599`) and `get_all_remote_names`
(`git.rs:2353`) — a writer and a name lister, no URL reader. jj-cli must read
the git config for its own `remote list`. Pyjutsu makes the same call.

Alternatives considered and rejected:

- **Names only, no URLs.** Loses information the CLI shows. A caller needs the
  URL to know where a push goes.
- **Parse `.git/config` directly.** Reimplements a config parser, including
  includes and conditional includes. Strictly worse than the two gix calls.
- **Use jj-lib's gix.** jj-lib 0.44 `lib.rs` contains no `pub use gix`, so
  there is no re-export to borrow. The direct edge and its exact-version pin
  must stay.

Keep it. Two read-only calls, no gix type crossing the FFI.

### End state

`gix` survives in exactly one Pyjutsu function, and appears in `Cargo.toml`
for that one function. Not zero — one. Reaching zero would require dropping
remote URLs from the API, which costs more than it saves.

```text
gix call sites   33 → 2
gix in Cargo.toml   stays, for `remotes` alone
```

## Part 2 — what jj offers that Pyjutsu does not

Pyjutsu binds 30 of jj-lib's public modules. The unbound ones are not all
equal: some are internals, and some are user-facing features with no Python
route.

### Tier 1 — holes that force callers out of the library

**1. Conflict content and resolution.**

`Conflict` today carries `path`, `num_sides`, `num_bases`. That reports that a
conflict exists and nothing more. A caller that hits one must leave Pyjutsu —
read the working copy by hand, or shell out to `jj resolve`.

jj-lib has the whole path, unbound:

```text
conflicts.rs:450   materialize_merge_result       conflict → marked text
conflicts.rs:838   parse_conflict                 marked text → sides
conflicts.rs:1050  update_from_content            resolved text → tree
conflicts.rs:305   ConflictMarkerStyle            diff/snapshot/git styles
```

Proposed surface:

```python
view.conflict_content(path, rev="@")      -> str    # materialized, marker style selectable
tx.resolve_conflict(path, content)        -> Commit # update_from_content
view.conflict_sides(path, rev="@")        -> list[str]  # parsed sides, no markers
```

This is the single largest gap. Conflict handling is the case where automation
most needs a library and least wants a subprocess.

**2. File content at a revision.**

There is no `jj file show`. A caller can diff two revisions but cannot read one
file at one revision without checking it out.

The plumbing is already present — `diff_stat.rs:117` calls `Store::read_file`
today. Only the public verb is missing.

```python
view.file_content(path, rev="@")   -> bytes
view.file_list(rev="@", paths=None) -> list[str]   # fileset-filtered
```

**3. Short id prefixes.**

Every model returns full 40-character ids. jj's entire UX rests on shortest
unique prefixes, and jj-lib provides the machinery (`id_prefix.rs:116`,
`IdPrefixContext`). Any caller that renders output must reimplement prefix
uniqueness — and will get it wrong, because correctness needs the index.

```python
view.shortest_prefix(id)  -> str
Commit.short_change_id    # populated from IdPrefixContext
```

### Tier 2 — real jj verbs with no binding

**4. Evolution / evolog.** `evolution::walk_predecessors` (`evolution.rs:86`).
The `Commit` model has no `predecessors` field. gitman rewrites commits
constantly; there is no API to follow a change across those rewrites. Pyjutsu
already sets `record_synthetic_predecessors: true` on import, so it records the
data it cannot read back.

**5. `duplicate` and backout.** `rewrite::duplicate_commits` (`rewrite.rs:1010`)
and `duplicate_commits_onto_parents` (`:1156`). The `rewrite` module is already
imported for `move_commits` / `squash_commits`, so these are close to free.

**6. `absorb`.** `absorb::absorb_hunks` (`absorb.rs:308`) with
`split_hunks_to_trees` (`:108`). Distributes working-copy edits into the
ancestor commits that introduced those lines. High value for automation, which
generates exactly the kind of scattered fixups absorb exists to file away.

**7. `fix`.** `fix::fix_files` (`fix.rs:181`), plus `ParallelFileFixer`. Runs
formatters over a commit range in-process. This maps directly onto the
verify-then-land loop, and today that loop must shell out.

**8. Commit signing.** `signing::Signer` (`signing.rs:166`), with
`gpg_signing` and `ssh_signing` backends. A land-and-push library cannot serve
a repository that requires signed commits. For some users this is not a
nice-to-have; it is the difference between usable and unusable.

### Tier 3 — worth having, lower urgency

| Gap | jj-lib | Why |
|-----|--------|-----|
| Graph edges for `log` | `graph::GraphEdge` (`graph.rs:33`) | `log()` returns a flat list. Rendering needs topology. |
| Blame | `annotate::FileAnnotator` (`annotate.rs:154`) | `jj file annotate`. |
| Description trailers | `trailer::parse_description_trailers` (`trailer.rs:60`) | Change-Id / Signed-off-by parsing; gitman does this by hand today. |
| Watchman | `fsmonitor` | Snapshot cost on large repos. |
| Bisect | `bisect::Bisector` (`bisect.rs:75`) | `jj bisect`. Niche, but wholly absent. |
| `util gc` | `Store::gc` (`store.rs:255`) | Already agreed; also retires the keep-ref prune. |

### One observation about the shape of the gaps

Every Tier 1 gap is a **read** that the library cannot perform: conflict text,
file bytes, short ids. Pyjutsu's mutation surface is close to complete —
`new`, `describe`, `edit`, `abandon`, `rebase`, `squash`, `restore`, `split`,
`select_tree`, bookmarks, workspaces, op log. Its read surface stops at
metadata and diffs.

That asymmetry is worth naming, because it predicts where the next gaps will
be found.

## Recommendation

Do not attach Part 2 to project 002. That project is a refactor plus a
dependency upgrade, and its value is that each checkpoint stays green.

1. Finish 002 as scoped, plus the removals in [[NATIVE_SURFACE_REPORT.md]].
2. Move the ref-repair trio out in its own post-bump lane (Part 1).
3. Open project 003 for the read surface: Tier 1 first, in the order given.
   Conflicts, file content, and short ids are each small, independent, and
   testable against the jj CLI as an oracle.
4. Treat Tier 2 as a backlog ranked by user demand. Signing is the one item
   that can block adoption outright rather than merely inconvenience a caller.
