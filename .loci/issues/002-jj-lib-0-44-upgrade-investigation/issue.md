---
title: Upgrade jj-lib and the differential jj CLI from 0.42.0 to 0.44.0
type: issue
status: active
loci:
  schema: 1
  id: 01a01bde-9ce0-7000-9d01-9194bc52fe10
  projects: []
---

## Summary

Upgrade Pyjutsu from `jj-lib` 0.42.0 to 0.44.0 and pin the differential `jj`
CLI to the same release.

The upgrade is recommended. An isolated compile trial reached a clean
`cargo check` after a small, concrete Rust port. The main scope is dependency
and factory API migration, followed by deliberate support for Jujutsu 0.44 tag
and object-hash behavior.

Jujutsu 0.44 still requires Rust 1.89. The current Rust toolchain floor can
remain unchanged.

## Required dependency and environment changes

- Change `jj-lib = "=0.42.0"` to `jj-lib = "=0.44.0"` in `Cargo.toml`.
- Change the direct `gix` pin from `=0.84.0` to `=0.85.0`.
- Keep one unified `gix` version.
- Enable at least `gix` feature `sha1` when `default-features = false`.
  Otherwise, `gix-hash` 0.25 does not compile.
- Regenerate `Cargo.lock`.
- Update `nixpkgs-jj` in `devenv.yaml` to the Nixpkgs revision that packages
  Jujutsu 0.44.0:
  `a5c43f1df1e17386c951571ec4a7942d2e9cda2e`.
- Regenerate `devenv.lock`.
- Require `jj --version` to report exactly 0.44.0.
- Keep `rust-version = "1.89"`.

The lockfile update includes `jj-lib-proc-macros` 0.44.0, `gix` 0.85.0,
`itertools` 0.15, and the associated updated transitive dependencies.

## Required Rust port

The unmodified source produced 12 compile errors against jj-lib 0.44. The
following changes made the isolated trial compile.

### Default backend factories

In `src/workspace.rs`, import these functions from
`jj_lib::default_backend_factories`:

```rust
default_backend_factories
default_working_copy_factories
default_working_copy_factory
```

Do not import the working-copy factories from `jj_lib::workspace`.

Replace both uses of:

```rust
StoreFactories::default()
```

with:

```rust
default_backend_factories()
```

`StoreFactories` no longer implements `Default`.

### Async APIs

In `src/repo_view.rs`, run `repo.index().is_ancestor(...)` through
`pollster::block_on()`.

In `src/transaction.rs`, run `repo.track_remote_bookmark(symbol)` through
`pollster::block_on()`.

Both operations became asynchronous.

### Revset parse context

Remove `use_glob_by_default` from the `RevsetParseContext` construction in
`src/revset.rs`.

Jujutsu 0.43 deprecated the setting. Jujutsu 0.44 removed the field.

### Repository object hash

`Workspace::init_colocated_git()` and `Workspace::init_internal_git()` now
require a `gix::hash::Kind` argument.

Use SHA-1 initially to preserve the current public behavior. Add a typed
public option in the same release if practical:

```python
Workspace.init(path, object_hash="sha1")
Workspace.init(path, object_hash="sha256")
```

Clone must receive equivalent object-hash handling where the underlying API
supports it.

### Git signatures

- `GitFetch::fetch()` now takes four arguments. Remove its final `None`.
- `git::add_remote()` now takes four arguments. Remove `Tags::None`.
- Remove the obsolete `gix::remote::fetch::Tags` import.
- Update comments that describe the removed tag argument.

## Behavior changes that require decisions and tests

### Native tags

Jujutsu 0.44 fetches and tracks tags similarly to bookmarks.

Pyjutsu currently constructs `GitFetch` with:

```rust
tag: StringExpression::none()
```

That will continue to suppress tags and will diverge from the pinned CLI.

Add:

- optional tag patterns for `git_fetch()`;
- local, remote, and tracked tag rows;
- `track_tag()` and `untrack_tag()`;
- tag deletion;
- explicit tag selection for push.

Use `MutableRepo::track_remote_tag()` and `untrack_remote_tag()`.

`git_push(all=True)` currently pushes bookmarks only. In Jujutsu 0.44,
`jj git push --all` also pushes tags. Change the behavior for CLI fidelity in
this pre-1.0 release, or add an explicit compatibility control. Test and
document the network-side effect.

Keep `create_tag()` as the existing annotated-Git-tag operation. Native
`jj tag set` records a Jujutsu tag target and exports a lightweight Git tag.
Add separate lightweight tag set/delete operations rather than changing the
annotated-tag contract silently.

### Colocated synchronization

Jujutsu 0.44 disables automatic colocated Git import and export by default
because of a race.

Pyjutsu calls jj-lib directly. It exposes explicit import/export operations
and performs automatic synchronization in some paths. Audit concurrent
access and document the intentional difference from the CLI default.

### Snapshot and fetch behavior

Jujutsu changed immutable working-copy snapshot behavior in 0.43 and adjusted
it again in 0.44. Re-run snapshot, stale-workspace, edit, abandon, and
operation-count differential tests.

Jujutsu 0.43 also changed Git fetch to rebase descendants of rewritten
commits by change ID. Pyjutsu already calls `rebase_descendants()` after
import. Add a differential network test for a rewritten bookmarked stack.

## New functionality to expose

### Priority 1

1. Native remote tags: fetch patterns, list state, track/untrack, delete, and
   push selection.
2. SHA-256 repository initialization through a typed `object_hash` option.
3. Typed revset-builder helpers for `forks()` and `merge_point(expr)`.

Raw revset strings will support `forks()` and `merge_point()` after the parser
upgrade. The typed helpers keep the builder aligned with the linked library.

### Priority 2

Consider a native transaction operation based on jj-lib's `absorb` module.
Interactive hunk selection remains user-interface policy.

Do not reimplement `jj run` in the binding. It is CLI orchestration over
temporary workspaces and subprocesses. `run_jj()` remains the correct escape
hatch.

Do not add binding work for CLI presentation features such as file search,
templater `try()`, workspace-list formatting, or diff-stat bar width unless
Pyjutsu adds those presentation surfaces separately.

## Tests, documentation, and versioning

- Update `tests/test_build.py` from 0.42 assertions to 0.44.
- Update explicit 0.42 references in the README, user and developer guides,
  concept document, native stub, revset module, differential helper, stale
  tests, and Rust comments.
- Bump Pyjutsu from 0.15.0 to 0.16.0 if this is the next compatibility release.
- Update the version in `Cargo.toml`, `pyproject.toml`,
  `python/pyjutsu/__init__.py`, and `tests/test_build.py`.
- Add differential coverage for tag fetch defaults, patterns, exclusions,
  tracking, and `git_push(all=True)`.
- Test SHA-1 and SHA-256 initialization.
- Test `forks()` and `merge_point()` builder rendering and evaluation.
- Test immutable working-copy snapshots and rewritten-fetch rebasing.
- Re-run configuration parity tests from the secondary-workspace issue.
- Run `cargo tree -i gix` and require exactly `gix` 0.85.0.
- Run the full build, Rust tests, Python lint, and differential suite.

## Risks

- **High:** changed default tag fetch and push behavior can create network side
  effects. Decide and document the contract before implementation.
- **High:** correct secondary-workspace configuration loading should land
  first or in the same release.
- **Medium:** SHA-256 repositories can expose hidden 20-byte object-ID
  assumptions. Run the full core suite under both hash formats.
- **Medium:** operation counts and working-copy timing can change around
  immutable commits.
- **Low:** the mechanical Rust API port. The isolated compile trial proved the
  required compile changes.

## Recommended implementation order

1. Land correct configuration loading, or coordinate it in the same release.
2. Move the Cargo, `gix`, Nix CLI, and lock pins together.
3. Apply the mechanical Rust port and reach warning-free `cargo check`.
4. Add object-hash handling and SHA-256 tests.
5. Implement Jujutsu 0.44 tag fetch, track, and push semantics.
6. Run focused snapshot, stale, Git, revset, and workspace tests.
7. Run the full differential gate against `jj` 0.44.0.
8. Update version and documentation references.
9. Release as Pyjutsu 0.16.0 if that is the next release.

## Primary sources

- Jujutsu 0.43 release notes:
  https://github.com/jj-vcs/jj/releases/tag/v0.43.0
- Jujutsu 0.44 release notes:
  https://github.com/jj-vcs/jj/releases/tag/v0.44.0
- jj-lib 0.44 API documentation:
  https://docs.rs/jj-lib/0.44.0/jj_lib/
- jj-lib 0.44 crate metadata and Rust version:
  https://github.com/jj-vcs/jj/blob/v0.44.0/lib/Cargo.toml
- Default backend factories:
  https://github.com/jj-vcs/jj/blob/v0.44.0/lib/src/default_backend_factories.rs
- Jujutsu 0.44 Git implementation:
  https://github.com/jj-vcs/jj/blob/v0.44.0/lib/src/git.rs
- Jujutsu 0.44 repository and tag implementation:
  https://github.com/jj-vcs/jj/blob/v0.44.0/lib/src/repo.rs
- Jujutsu 0.44 index API:
  https://github.com/jj-vcs/jj/blob/v0.44.0/lib/src/index.rs
- Jujutsu 0.44 object-hash configuration:
  https://docs.jj-vcs.dev/v0.44.0/config/#default-object-hash-format
- Nixpkgs Jujutsu 0.44 package bump:
  https://github.com/NixOS/nixpkgs/commit/a5c43f1df1e17386c951571ec4a7942d2e9cda2e

## Definition of done

- Pyjutsu builds against exactly jj-lib 0.44.0.
- The pinned `jj` differential oracle reports exactly 0.44.0.
- The repository has one `gix` 0.85.0 dependency.
- The full differential gate passes.
- Tag fetch/push semantics are explicit and tested.
- SHA-1 and SHA-256 initialization behavior is explicit and tested.
- All public version references and documentation are synchronized.
