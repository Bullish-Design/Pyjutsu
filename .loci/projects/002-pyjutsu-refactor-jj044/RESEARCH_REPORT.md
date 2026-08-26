---
title: jj-lib 0.44 refactor and upgrade research
type: report
status: active
project: 002-pyjutsu-refactor-jj044
date: 2026-08-26
---

# jj-lib 0.44 refactor and upgrade research

## Target and evidence boundary

Pyjutsu must remove four compatibility surfaces before it upgrades to jj-lib 0.44.0.
The final gate must pass against the pinned Jujutsu command-line interface (CLI).
Phase C and Phase D remain outside this implementation.

The baseline used Rust 1.94.1, Cargo 1.94.0, Python 3.13.13, and jj 0.42.0.
All gate commands passed. Cargo ran 7 tests. Pytest exited 0.
The preserved evidence is in `artifacts/20260826T173502Z-baseline/`.

## 2026-08-26 — A1 patch-id hash

The dependency tree already resolved `sha1` 0.10.6 through `sha1-checked`.
Adding `sha1 = "0.10"` reuses that version and does not add a second build.

The fixed diff adds `h.txt` with the bytes `x\n`.
The old gix hasher returned `9acba72932d8936dab73915f34a54bceb923689f`.
The new direct SHA-1 implementation returns the same digest.
The evidence is in `artifacts/20260826T173954Z-a1-prechange/` and
`artifacts/20260826T174036Z-a1-postchange/`.

Two mechanisms were possible: Git object hashing and a plain SHA-1 state.
The existing gix call used a plain SHA-1 state without Git object framing.
The direct `sha1` crate therefore preserves the byte contract.
The fixed-diff regression rejects a digest change.

Primary sources:

- [Jujutsu 0.44 release](https://github.com/jj-vcs/jj/releases/tag/v0.44.0) — target release.
- [jj-lib 0.44 crate manifest](https://github.com/jj-vcs/jj/blob/v0.44.0/lib/Cargo.toml) — target dependency and gix feature evidence.
- [RustCrypto SHA-1 implementation](https://github.com/RustCrypto/hashes/tree/master/sha1) — direct digest implementation.

This evidence proves patch-id compatibility for the fixed diff.
It does not yet prove the jj-lib 0.44 port or SHA-256 repository support.

## 2026-08-26 — A2 native string escaping

The local jj-lib 0.42.0 source defines `dsl_util::escape_string` at
`src/dsl_util.rs:474`. The function escapes string contents without adding
quote delimiters. jj-lib's revset renderer adds those delimiters separately.

The first full run imported a stale editable extension and failed during test
collection. Cargo had compiled the new symbol, but the task runner reused the
old `.so`. Running the build task's direct `maturin develop --uv` command
replaced the artifact. A native import then succeeded.

The next focused run showed that `_quote` must retain responsibility for the
outer quote delimiters. The minimal fix calls the native function for all
escaping and adds only the delimiters in Python. Quotes, backslashes, newlines,
tabs, non-ASCII text, and the empty string now parse through jj 0.42.0.

The failed gate evidence is in `artifacts/20260826T174754Z-a2-gate/`.
The recovery evidence is in `artifacts/20260826T174754Z-a2-gate-recovery/`.
The green rerun is in `artifacts/20260826T175012Z-a2-gate/`.

This evidence proves that the pinned CLI accepts every required literal class.
It does not prove behavior against jj-lib 0.44.0 until B1 moves the pin.

## 2026-08-26 — A3 lightweight tags

jj-lib 0.42.0 exposes `MutableRepo::set_local_tag_target` in `repo.rs:1817`.
It exposes `git::export_refs` in `git.rs:1231`. The export API writes new and
updated tags as lightweight Git refs.

The jj path resolves one commit, refuses an existing local tag unless
`force=True`, sets the local target, exports refs, rebases descendants, and
commits one operation. The refusal restores the old gix
`PreviousValue::MustNotExist` contract explicitly.

The default call now creates a lightweight tag. The pinned jj CLI lists it,
and Git reports object type `commit`. A lightweight tag also reaches a bare
remote through the unchanged `push_tag` path.

The message form still writes an annotated object. It emits a
`DeprecationWarning` that names `ws.git.create_tag`. Existing positional
message callers continue to work. Git still reports object type `tag`, the
message body, and the tagger line.

A fetched annotated tag keeps its object ID, type, and message across a local
lightweight-tag export. This confirms that the jj path does not degrade an
incoming annotated tag.

The first compile attempt found only Rustfmt drift. The second found that
jj-lib 0.42 exports `RefTarget` from `op_store`, not a `ref_target` module.
The minimal import correction compiled without other API changes.

Focused evidence is in `artifacts/20260826T175754Z-a3-focused/`.
The green gate is in `artifacts/20260826T180027Z-a3-gate/`.

## 2026-08-26 — A4 native garbage collection

The pinned `jj util gc --help` says that obsolete objects and operations older
than two weeks are pruned by default. The Python facade mirrors that policy by
passing `now - 2 weeks` as `keep_newer` when the caller supplies no cutoff.

The local jj-lib 0.42.0 source exposes `Store::gc(&dyn Index, SystemTime)` at
`store.rs:254`. Its Git backend first calls `recreate_no_gc_refs`: indexed heads
remain anchored, obsolete keep-refs older than the cutoff are deleted, and then
Git backend collection runs with the same cutoff. This is the correct owner of
the namespace and replaces Pyjutsu's vendored ref-transaction purge.

The focused tests age a deliberately unreachable loose keep-ref to 2000. A
no-argument `gc()` preserves the reachable head ref, removes that orphan, and
leaves the head operation unchanged. The re-adopt test proves the behavior
change: deleting `.jj` and initializing again leaves the old ref in `.git`
until `gc()` removes it. The refs are never imported into the jj view.

Focused evidence is in `artifacts/20260826T182000Z-a4-focused/`.

The first full gate stopped at Ruff after both Rust checks passed because the
new test's third-party imports were not sorted. Reordering those two imports is
the complete correction; the full gate was restarted rather than resumed.

Primary sources:

- [Jujutsu v0.42.0 source](https://github.com/jj-vcs/jj/tree/v0.42.0) — pinned CLI and library release.
- [jj-lib Git backend GC](https://github.com/jj-vcs/jj/blob/v0.42.0/lib/src/git_backend.rs) — keep-ref refresh and backend collection.
- [Jujutsu GC behavior discussion](https://github.com/jj-vcs/jj/discussions/4709) — maintainer explanation that `jj util gc` removes unused keep-refs and runs Git GC.

This evidence proves the 0.42 default, native lifecycle ownership, and no-op-log
contract. The same anchors must be rechecked once B1 moves the pin to 0.44.0.

The restarted full gate is green in `artifacts/20260826T183000Z-a4-final/`:
7 Rust tests passed, all 395 Python tests passed, and the aggregate verification
task exited successfully.

## 2026-08-26 — B1 and B2 pin move and gix port

The pin move is not a self-contained green step. Moving `jj-lib` to `=0.44.0`
and `gix` to `=0.85.0` produced 12 compiler errors across four source files.
The raw output is preserved in `artifacts/20260826T184000Z-b1-pin/`. B1 and B2
therefore land as one commit, because the project forbids a commit on a red gate.

The 12 errors reduce to seven jj-lib API changes: `StoreFactories::default` is
replaced by `default_backend_factories`; the two working-copy factory functions
moved module; the two Git workspace initializers take a `gix::hash::Kind`;
`GitFetch::fetch` and `git::add_remote` each lost one argument;
`Index::is_ancestor` and `MutableRepo::track_remote_bookmark` became async; and
`RevsetParseContext` lost `use_glob_by_default`.

`cargo tree -i gix` resolves a single `gix v0.85.0`. Nothing pulls a second
version, so the direct `gix` edge and jj-lib's own edge unify as required.

`apply_head_ref_packed` is the one call site the plan budgets on its own,
because it drives the low-level file-store transaction API. Under gix 0.85 it
compiled and ran without a source change. Its dedicated suite passed 8 tests.
Evidence is in `artifacts/20260826T190000Z-b2-ref-repair/`.

jj-cli is not published to the Cargo registry, so the vendored revset table was
re-diffed against the pinned CLI binary instead of upstream source. jj 0.44.0's
`jj config list --include-defaults revset-aliases` reproduces every vendored
alias byte for byte and adds `builtin_log()`. Vendoring that one alias keeps the
staleness test an exact-equality oracle.

jj 0.44 also removes `ui.revsets-use-glob-by-default` from the CLI defaults,
which confirms the jj-lib field removal is intended and not a private-API
accident. The test now asserts the key is absent.

Primary sources:

- [Jujutsu v0.44.0 source](https://github.com/jj-vcs/jj/tree/v0.44.0) — target release.
- [jj-lib 0.44 crate manifest](https://github.com/jj-vcs/jj/blob/v0.44.0/lib/Cargo.toml) — gix features `sha1` and `sha256`.
- Pinned CLI binary, nixpkgs rev `a5c43f1df1e17386c951571ec4a7942d2e9cda2e` — jj 0.44.0, the revset-alias and dropped-setting oracle.

This evidence proves the port compiles and the existing suite passes under
0.44.0. It does not yet prove SHA-256 repository behavior; B3 covers that.

## 2026-08-26 — B3 SHA-256 repositories

The pinned CLI is the primary source for the object-format policy. Its
`--include-defaults` output lists `git.object-hash = "sha1"`, and `jj git init
--help` lists no flag for it, so configuration is the only input. Creating a
repository with `jj git init --colocate --config git.object-hash=sha256` yields
`extensions.objectformat = sha256` in the colocated `.git` and 64-hex commit
ids. That command is the oracle Pyjutsu's `init` now matches.

jj-lib takes the format as a `gix::hash::Kind` and defines none of the key, the
values, or the default. Searching the jj-lib 0.44.0 source for `object-hash`
returns no configuration handling, only `gix` calls. The mapping is therefore
jj-cli policy, vendored in `git_object_hash`.

`gix::hash::Kind::Sha1` and `Kind::Sha256` are each behind their matching gix
feature (`gix-hash-0.26.0/src/lib.rs:96-105`). Pyjutsu names both, so both are
now declared on Pyjutsu's own gix edge. This is finding F1's rule applied to a
call the 0.44 port introduced, and it supersedes the earlier decision not to
declare `sha1`.

Two harness facts came out of the matrix run:

- Git refuses to transfer objects between repositories of different formats.
  Eight `test_git_net` failures and four tag-push failures were all one cause: a
  SHA-1 bare remote paired with a SHA-256 source repo. Creating the remote with
  a matching `--object-format` fixes every one. This is a harness limit, not a
  Pyjutsu defect.
- `test_patch_id_is_pinned_for_fixed_diff` pins a literal SHA-1 digest and
  passes unchanged under the SHA-256 matrix. The patch id keeps its exact value,
  not only its width, which proves the finding F2 decision directly.

Primary sources:

- Pinned CLI binary, jj 0.44.0 — `git.object-hash` default and the `jj git init` option list.
- [jj-lib 0.44.0 `git_backend.rs`](https://github.com/jj-vcs/jj/blob/v0.44.0/lib/src/git_backend.rs) — `object_hash` threaded into repository creation.
- `gix-hash` 0.26.0 `src/lib.rs` — the two `Kind` variants and their feature gates.

This evidence proves repository creation, the full read and write surface, and
patch-id stability under SHA-256.

## 2026-08-26 — B4 release 0.17.0

The release itself needs no new research. The documentation sweep does, because
a version number in a comment is a claim that can be wrong in two different ways.

Nineteen references still named jj 0.42. Each was classified before it was
touched. A claim about what the pinned version *does* — "the default preserves
two weeks", "bulk push never deletes", "`MutableIndex` carries no `Send` bound" —
is false once the pin moves, so each was re-checked against the pinned 0.44 CLI
or the 0.44 source, then retargeted. A claim about *when* something changed —
"jj-lib 0.42 dropped `auto_local_bookmark`" — stays true and was left alone.

Three anchors moved or were confirmed:

- `jj util gc --help` under 0.44 still says two weeks. The `keep_newer=None`
  default needs no change.
- `jj git push --help` under 0.44 still separates `--all` / `--tracked` from
  `--deleted`, so the "bulk push never deletes" contract holds.
- `MutableIndex: Any` is at `index.rs:186` in jj-lib 0.44, not `:178`. The trait
  still carries no `Send` bound, so `PyTransaction` stays `unsendable`.

The re-verification list changed size during this project rather than only
shrinking. Phase A retired `_quote` and `NO_GC_REF_NAMESPACE` as planned, but B3
added one: jj-lib defines neither the `git.object-hash` key nor its `"sha1"`
default, so that mapping is vendored jj-cli policy and must be re-diffed at each
upgrade alongside `src/config/revsets.toml`.

Primary sources:

- Pinned CLI binary, jj 0.44.0 — `jj util gc --help`, `jj git push --help`.
- jj-lib 0.44.0 `src/index.rs:186` — the `MutableIndex: Any` declaration.
