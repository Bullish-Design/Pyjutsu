# Implementation prompt

Work in the Pyjutsu repository. Implement the complete issue in
`.loci/issues/002-jj-lib-0-44-upgrade-investigation/issue.md`.

Use the `build-run-investigation-loop` skill for this task. Read its full
instructions before you change code. Also read `AGENTS.md` and the full
`.agents/skills/my-ai/SKILL.md` file. Follow the repository's manager routing,
devenv, verification, commit, and push rules.

## Objective

Upgrade Pyjutsu and its differential CLI oracle from Jujutsu 0.42.0 to 0.44.0.

Complete the mechanical port, preserve existing behavior deliberately, and
add the high-value 0.44 functionality identified by the issue.

Start from a branch that already contains issue 001's configuration and
secondary-workspace fixes. If those changes are absent, stop and report the
missing prerequisite before changing dependency pins.

## Start with evidence

Before implementation:

1. Read the complete issue file.
2. Inspect every direct jj-lib and gix use in the repository.
3. Inspect Jujutsu 0.43 and 0.44 release notes.
4. Inspect the exact jj-lib 0.44 source and API documentation.
5. Run the current full gate against 0.42.0 and preserve its output.
6. Create the dated research report required by the investigation skill.
7. Record the current `cargo tree -i gix` result.

Use official Jujutsu, docs.rs, and Nixpkgs sources. Do not rely only on the
existing issue's prior investigation. Reconfirm its claims against the final
0.44.0 APIs.

## Atomic version and environment update

Update these pins together:

- `jj-lib` from exactly 0.42.0 to exactly 0.44.0;
- direct `gix` from exactly 0.84.0 to exactly 0.85.0;
- the pinned Nixpkgs Jujutsu input to the 0.44.0 package revision;
- `Cargo.lock` and `devenv.lock`.

Keep one `gix` version. With `default-features = false`, enable the required
SHA-1 feature explicitly.

Keep Rust 1.89 unless the pinned source proves a higher floor is required.

After the pin update, require:

```text
jj --version == 0.44.0
pyjutsu.JJ_VERSION == 0.44.0
cargo tree -i gix contains one gix 0.85.0
```

Do not leave the library and differential CLI on different versions.

## Mechanical Rust port

Apply the compile-proven changes from the issue, then verify each against the
actual 0.44 source.

- Import standard backend and working-copy factories from
  `jj_lib::default_backend_factories`.
- Replace `StoreFactories::default()` with
  `default_backend_factories()`.
- Await `Index::is_ancestor()` through the existing synchronous bridge.
- Await `track_remote_bookmark()` through the existing synchronous bridge.
- Remove `use_glob_by_default` from the revset parse context.
- Pass an explicit `gix::hash::Kind` to internal and colocated repository
  initialization.
- Update the changed `GitFetch::fetch()` signature.
- Update the changed `git::add_remote()` signature.
- Remove the obsolete `Tags` import and related comments.

Reach warning-free `cargo check` before adding new behavior. Preserve the
build log and classify any additional compiler failures before fixing them.

## SHA-1 and SHA-256 repositories

Expose an explicit typed public option for repository initialization:

```python
Workspace.init(path, object_hash="sha1")
Workspace.init(path, object_hash="sha256")
```

Choose the default from Jujutsu 0.44 behavior and the existing Pyjutsu
compatibility contract. Document the decision.

Propagate the option through the Python facade, native stub, PyO3 method, and
jj-lib initializer. Apply equivalent behavior to clone when the upstream API
allows callers to choose the object hash.

Audit the codebase for fixed 20-byte object-ID assumptions. Run the complete
core suite against both hash formats where practical.

## Native tag parity

Jujutsu 0.44 treats remote tags as first-class tracked references. Pyjutsu's
current `GitFetch` suppresses all tags.

Implement a coherent native tag surface:

- tag patterns for `git_fetch()`;
- default tag-fetch behavior that matches the pinned CLI;
- local, remote, and tracked tag rows;
- remote tag track and untrack operations;
- native lightweight tag set and delete operations;
- explicit tag selection for push;
- `git_push(all=True)` behavior that handles tags deliberately.

Use jj-lib tag APIs. Do not parse CLI output.

Preserve the current annotated Git tag API. Do not silently change
`create_tag()` into lightweight `jj tag set` behavior. Use separate public
operations and clear model names.

Treat changed fetch and push defaults as network-side effects. Add tests and
release documentation before changing the public default.

## Revset functionality

Add typed builder helpers for:

```text
forks()
merge_point(expression)
```

Test rendering and evaluation against the pinned 0.44 CLI. Keep raw revset
strings as the complete escape hatch.

## Colocated synchronization audit

Jujutsu 0.44 disables automatic colocated import and export by default because
of a race.

Audit Pyjutsu's explicit and automatic synchronization paths. Test concurrent
or stale-state behavior where the existing harness permits it.

Do not copy the CLI default without analysis. Pyjutsu is an in-process library
with an explicit synchronization API. Document any intentional difference and
the caller's concurrency responsibility.

## Regression coverage

Add or update differential tests for:

- tag fetch defaults, patterns, and exclusions;
- remote tag state and tracking;
- lightweight tag set/delete and annotated-tag preservation;
- selected tag push and `git_push(all=True)`;
- SHA-1 and SHA-256 initialization;
- `forks()` and `merge_point()`;
- immutable working-copy snapshot behavior;
- stale workspace behavior;
- edit, abandon, and operation-count behavior;
- fetch rebasing of rewritten bookmarked stacks;
- secondary-workspace configuration and authoring parity.

Use disposable local remotes for network tests. Never contact or mutate a real
external repository.

## Scope decisions

Consider jj-lib's `absorb` module only after the required upgrade and priority
features are green. Implement it only if it fits the transaction model without
interactive UI policy.

Do not reimplement `jj run`. Keep `run_jj()` as the escape hatch for CLI
orchestration over temporary workspaces and subprocesses.

Do not add CLI presentation features such as diff-stat bar formatting or
workspace-list presentation to the native binding.

If a priority feature cannot fit safely in this release, create a detailed
numbered follow-up issue under `.loci/issues/` through the loci CLI. Include
the proven blocker and required upstream API.

## Documentation and versioning

Update every explicit 0.42 reference in code, tests, stubs, README, and docs.

Update `tests/test_build.py` to require 0.44.0.

Bump Pyjutsu to 0.16.0 if this is the next compatibility release. Keep
`Cargo.toml`, `pyproject.toml`, `python/pyjutsu/__init__.py`, and tests
synchronized.

Document:

- the linked jj-lib and pinned CLI versions;
- tag fetch and push behavior;
- annotated versus lightweight tags;
- SHA-1 and SHA-256 initialization;
- colocated synchronization behavior;
- any intentional difference from the CLI.

## Verification and delivery

Run focused tests after each migration slice. Preserve failure logs and rerun
with fresh evidence after every fix.

Then run the repository's complete build, Rust tests, Python tests, lint, and
differential gate inside devenv.

Inspect the final dependency tree, diff, and repository status. Include all
relevant active changes. Commit only after the complete gate is green. Push
the completed commit to the current branch.

In the final report, include:

- exact dependency and toolchain versions;
- all Rust API migrations;
- new public APIs;
- tag compatibility decisions;
- object-hash behavior;
- synchronization findings;
- focused and full gate results;
- commit and pushed branch;
- any numbered follow-up issues.

Do not stop after the dependency bump or first successful compile. Continue
until the full 0.44 issue is implemented, differentially verified, documented,
committed, and pushed, or until a concrete external blocker requires user
action.
