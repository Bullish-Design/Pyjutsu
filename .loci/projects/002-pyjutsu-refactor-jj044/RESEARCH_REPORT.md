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
