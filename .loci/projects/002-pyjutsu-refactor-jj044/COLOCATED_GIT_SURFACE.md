---
title: The colocated git surface — what gix should provide
type: report
status: draft
project: 002-pyjutsu-refactor-jj044
---

# The colocated git surface

## The premise changed, so two earlier conclusions change

The first two reports optimised for one thing: fewer `gix` call sites. That
metric was wrong, and the reasoning below supersedes it where they conflict.

**`gix` is not a cost Pyjutsu can avoid.** jj-lib depends on it
(`jj-lib/Cargo.toml:97`, feature `git`). Every Pyjutsu wheel already links a
full `gix` build. The direct edge in `Cargo.toml:45` adds a version pin and a
feature declaration — it does not add a dependency, a compile, or a byte of
wheel.

**What actually costs is API depth, not call count.** These are not equal:

```rust
git_repo.references().prefixed("refs/heads/")      // shallow, stable, ~zero port cost
git_repo.find_remote(name).url(Direction::Fetch)   // shallow, stable

ref_store.transaction()                             // deep, volatile
    .packed_refs(PackedRefs::DeletionsAndNonSymbolicUpdatesRemoveLooseSourceReference(odb))
    .prepare(edits, Fail::Immediately, Fail::Immediately)
```

Counting both as "one gix site" hid the only real risk. Finding F4 was right
for exactly one call site and wrong as a general budget.

### Two reversals

1. **`git_refs` and the ref writers stay in Pyjutsu.** Moving them to gitman
   was downstream of "minimise gix". With that goal gone, they are ordinary
   members of a colocated-git API. The depth warning on
   `apply_head_ref_packed` stands on its own and is handled below.
2. **Annotated tags are not deleted — they are renamed.** See §2.

The one conclusion that does **not** change is the keep-ref prune: replacing it
with `Store::gc` is right regardless, because it removes a *vendored private
constant*, not because it removes gix.

## 1. The free capability budget

jj-lib 0.44 enables these gix features (`jj-lib/Cargo.toml:97`):

```toml
features = ["attributes", "blob-diff", "index", "max-performance-safe",
            "sha1", "sha256", "zlib-rs"]
```

Cargo unifies features, so everything those gate is **already compiled into
the wheel**. Using it costs nothing but the code that calls it.

| Capability | Gate | Status |
|---|---|---|
| Refs — read, write, transactions | none | free |
| Objects — read, write, tags | none (`pub mod tag`) | free |
| Git config — read and write | none (`config_snapshot`, `config_snapshot_mut`) | free |
| `HEAD` — read, set | none (`head`, `head_name`, `head_id`) | free |
| Git worktrees | none (`worktrees`) | free |
| Reflog — read | none (`Reference::log_iter`, `Head::log_iter`) | free |
| Git index | `index` | free |
| Submodules | `attributes` | free |
| Blob diff | `blob-diff` | free |
| `rev_parse`, `describe` | `revision` | **not enabled** |
| Blame | `blame` | not enabled |
| `status`, dirwalk | `status`, `dirwalk` | not enabled |
| Network — fetch, push, ls-remote | `credentials` + transports | not enabled |
| Mailmap | `mailmap` | not enabled |

The budget is generous, and it is not an accident: jj-lib enables what a
colocated Git backend needs, which is most of what a colocated-repo tool needs.

## 2. Annotated tags — rename, do not delete

Dropping annotated tags was correct **as a jj API**. It is wrong as a deletion.

Two facts force the reader back in regardless of the earlier decision:

- Pyjutsu will meet annotated tags whether or not it writes them. They arrive
  by fetch, and jj-lib preserves them on export
  (`find_git_tag_oid_to_copy`, `git.rs:1408`).
- jj can show only the name and target. The message, tagger, and date live in
  the tag object, and jj has no reader for them.

So a Pyjutsu that cannot read an annotated tag message is a worse tool for
colocated repos than `git` is, on the most common release convention there is.

**Proposal.** Split the verb by which world it belongs to:

```python
ws.create_tag(name, target)                      # jj: lightweight, via set_local_tag_target
ws.git.create_tag(name, target, message)         # git: annotated object, via gix
ws.git.tag(name) -> GitTag                       # {target, annotated, message, tagger, date}
```

`src/workspace/tags.rs` **moves** under the git namespace instead of being
deleted. It already works and is already tested. The jj-side `create_tag`
becomes pure jj-lib as planned.

This resolves the tension honestly: the jj API makes no claim jj cannot back,
and the git API is labelled git.

## 3. Proposed surface, ranked

Everything in tiers A and B is inside the free budget.

### Tier A — fills a real colocated gap

**1. Annotated tag read and write.** §2. `find_tag` (`object.rs:112`),
`tag` (`object.rs:338`), `tag_reference` (`reference.rs:15`).

**2. Git config read and write.**

```python
ws.git.config_get(key) -> str | None
ws.git.config_set(key, value)
```

jj-lib already assumes callers hold a gix config: `save_git_config` takes
`&gix::config::File` (`git.rs:2218`). Colocated users need `core.hooksPath`,
`user.signingkey`, and per-remote settings, and today have no route to any of
them. `config_snapshot` / `config_snapshot_mut` are ungated.

**3. `HEAD` state.**

```python
ws.git.head() -> GitHead   # {name, oid, detached}
ws.git.set_head(name)
```

This replaces the raw `std::fs::write(".git/HEAD", ...)` at `workspace.rs:1091`
with `head_name()` / `head_id()` and a symbolic-ref edit. A filesystem hack
becomes an API call, and the trunk-name validation stops being hand-rolled.

**4. Git worktrees.**

```python
ws.git.worktrees() -> list[GitWorktree]
```

jj workspaces and git worktrees coexist badly, and jj-lib's own
`export_some_refs` walks `git_repo.worktrees()` to detach HEAD in each one.
This project's own baseline log tracks a stray Paseo worktree by hand. Free,
ungated, and directly useful to gitman.

**5. Object access.**

```python
ws.git.object_type(oid) -> str
ws.git.read_blob(oid) -> bytes
ws.git.exists(oid) -> bool
```

Validates an oid before a ref write, reads tag and blob objects, and gives the
git-side answer to "what is this hash". Core odb, free.

**6. Ref read and write — kept, not moved.** `git_refs`, `write_git_ref`,
`delete_git_ref` become `ws.git.refs()`, `ws.git.write_ref()`,
`ws.git.delete_ref()`. Same behaviour, honest home.

### Tier B — real, narrower

**7. Submodules.**

```python
ws.git.submodules() -> list[GitSubmodule]   # read-only
```

jj has **no** submodule support — `submodule_store` is a stub. A colocated repo
with submodules is invisible to Pyjutsu today. `gix-submodule` is already
compiled through `attributes`. Scope it read-only: listing and state. Do not
attempt updates; that is a working-copy mutation jj knows nothing about.

**8. Reflog read.**

```python
ws.git.reflog(ref="HEAD", limit=None) -> list[ReflogEntry]
```

jj's op log covers operations jj performed. It does not cover a `git reset` or
`git checkout` run outside jj — which is exactly the situation a colocated
recovery tool is called for. This is the missing half of the recovery view.

**9. Git index read.** The `index` feature is already on. Reporting staged
entries lets a caller detect the `git add` state that jj ignores. Read-only
only: writing the index behind jj's back is a trap, and jj-lib's `reset_head`
already owns index updates.

### Tier C — reject, and why

| Candidate | Verdict |
|---|---|
| Network via gix (fetch, push, `ls-remote`) | **No.** jj deliberately shells out to `git` for the network (`git_subprocess.rs`). Matching that choice is right. `credentials` plus a TLS transport is the heaviest thing in gix. |
| Blame via gix | **No.** jj-lib has `annotate` (`annotate.rs:154`). Bind jj's. Do not enable `blame`. |
| `git status` / dirwalk | **No.** jj's snapshot and working-copy model own this. Enabling `status` duplicates it and invites two answers to one question. |
| `git gc`, repack, fsck | **No.** `Store::gc` covers jj's half; leave git-side maintenance to the git binary. |
| `git describe` | **No — and the jj answer is better.** It needs the `revision` feature jj-lib does not enable. Implement it as a revset over jj's tag view: the nearest tagged ancestor. That is exact, pure jj-lib, and it also works on **non-colocated** repos, which `gix describe` cannot. |
| Mailmap | **No.** Not enabled, low value. |

`revision` is the only feature-flag decision on the table, and the
recommendation is not to take it.

## 4. Rules for the git namespace

1. **One namespace.** `ws.git.*`, backed by one module (`src/git_view.rs` or
   `src/git/`). Do not scatter git verbs across `Workspace`. The boundary
   between the two worlds should be visible in the call.
2. **No gix type crosses the FFI.** Already the rule for `remotes`. Keep it.
3. **Declare every feature Pyjutsu itself calls** on Pyjutsu's own gix edge —
   `attributes` for submodules, `index` for the index reader. F1's principle
   survives even though its specific instance (`sha1`) goes away with the
   patch-id change.
4. **Keep the version pin exact and synced to jj-lib's gix.** One resolved gix
   build. `cargo tree -i gix` stays in the gate.
5. **Depth rule, replacing the count rule.** Prefer `gix::Repository` methods.
   Anything reaching under `gix::refs::file::transaction` is a port hazard: it
   needs its own test and its own line in the upgrade budget. Today that is
   exactly one function, `apply_head_ref_packed`.

## 5. What this changes about the plan

- **Unchanged:** lightweight jj tags, `sha1` crate for `patch_id_hex`, native
  `escape_string`, `Store::gc` replacing the keep-ref prune, dropping the
  `gix::remote::fetch::Tags` import at the bump.
- **Reversed:** the ref-repair trio stays. Annotated tag code moves rather than
  dies.
- **New:** a `ws.git` namespace, built after the 0.44 bump lands, in the tier
  order above.

The read-surface gaps in [[LIBRARY_DESIGN_REVIEW.md]] — conflict content, file
bytes, short ids — are still the higher priority. They are jj-side, and they
block callers today. The git namespace is the second project, not the first.
