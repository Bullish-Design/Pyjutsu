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
