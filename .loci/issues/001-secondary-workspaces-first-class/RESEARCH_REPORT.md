# Research report — secondary workspaces

Date: 2026-08-19

## Target

Make secondary Pyjutsu workspaces match Jujutsu 0.42 authoring behavior.
Support zero, one, or many parent revisions during workspace creation.
Load user, repository, and workspace configuration from the correct Jujutsu identity.

Keep `jj-lib` at 0.42.0. Do not add `jj-cli` as a runtime dependency.
Do not implement workspace creation through a subprocess.

## Environment and baseline

- Pyjutsu commit: `9b1075526b19c801e631c8b42b1f1aafbfaf3ae2`
- Jujutsu: `jj 0.42.0`
- Rust: `rustc 1.94.1`
- Python: `3.13.13`
- Baseline command: `pytest -q tests/test_workspace_mgmt.py tests/test_workspace_load.py`
- Baseline result: 11 tests passed.
- Evidence: [`artifacts/20260819T214130Z-baseline/`](artifacts/20260819T214130Z-baseline/)

The green baseline does not prove the required behavior.
The current workspace test passes `-r root()` to the command-line interface comparison.
The shared test configuration also places author identity in `JJ_CONFIG`.
These fixtures mask both reported differences.

## Local code-path evidence

`src/workspace.rs::load_user_settings()` builds settings before jj-lib resolves workspace metadata.
It loads repository configuration from `<workspace>/.jj/repo/config.toml` only when that path is a file.
A secondary workspace stores a repository pointer at `<workspace>/.jj/repo`.
The current code therefore omits repository configuration for secondary workspaces.

`PyWorkspace::add_workspace()` calls `Workspace::init_workspace_with_existing_repo()` directly.
That primitive registers the workspace and creates its first working-copy commit on `root()`.
The Python facade and native stub expose no parent-revision argument.

Pyjutsu already has the required lower-level mutation pieces.
`src/revset.rs` evaluates revsets in a workspace context.
`src/transaction.rs::resolve_single()` enforces one commit per expression.
`merge_commit_trees()` and working-copy checkout paths already preserve Jujutsu tree and conflict semantics.

## Upstream evidence

- [Jujutsu 0.42 workspace add](https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/commands/workspace/add.rs)
  registers a workspace, selects parents, merges their trees, creates a new commit, edits it, and finishes the transaction.
  With no revisions, it uses the source working-copy commit's parents.
  It falls back to the root commit only when the source workspace has no working-copy commit.
- [Jujutsu 0.42 configuration loader](https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/config.rs)
  loads defaults, environment base values, user paths, secure repository config, secure workspace config, and environment overrides.
  It resolves conditional configuration with home, repository, workspace, hostname, and environment context.
- [jj-lib 0.42 workspace loader](https://github.com/jj-vcs/jj/blob/v0.42.0/lib/src/workspace.rs)
  resolves a secondary workspace repository pointer to the canonical repository path.
- [jj-lib 0.42 secure configuration](https://github.com/jj-vcs/jj/blob/v0.42.0/lib/src/secure_config.rs)
  provides repository and workspace configuration identities.
  `maybe_load_config()` avoids creating a new empty configuration during a normal load.

The exact pinned source also confirms secure storage roots named `repos` and `workspaces`.
User paths include `~/.jjconfig.toml`, the platform `jj/config.toml`, and `jj/conf.d`.
An explicitly empty `JJ_CONFIG` disables those default user paths.

## Failure boundaries

The configuration defect occurs before `Workspace::load()` constructs the repository loader.
The code has not resolved the shared repository path at that point.

The workspace-placement defect occurs after jj-lib registration.
Pyjutsu returns the root-based commit without running the command-line interface's second mutation.

## Considered approaches

### Follow the secondary `.jj/repo` pointer manually

Rejected. This would preserve the legacy `config.toml` assumption.
It would also duplicate jj-lib's repository identity logic.

### Add `jj-cli` as a runtime dependency

Rejected. The required behavior uses small policy adapters over public jj-lib APIs.
The issue explicitly keeps the binding layer independent from the command-line crate.

### Run `jj workspace add` as a subprocess

Rejected. This would bypass the native binding contract and make `run_jj()` the primary implementation.

### Register on root and let the consumer repair the workspace

Rejected. This exposes an incorrect intermediate topology and duplicates recovery logic in every consumer.

## Chosen implementation

Add a focused Rust configuration module.
It will reproduce the relevant Jujutsu 0.42 environment and user-path policy.
It will use the jj-lib workspace-loader factory to resolve the repository path.
It will use `SecureConfig::maybe_load_config()` for repository and workspace layers.
It will resolve conditional configuration before constructing final `UserSettings`.
Python will receive secure-config warnings through its warning system.

Extend the Python facade with `revisions` and normalize inputs before the native call.
Resolve every explicit expression before filesystem or repository mutation.
Validate the destination and workspace name before registration.
After registration, create and edit the requested working-copy commit with jj-lib primitives.
Finish the new working-copy mutation so disk content matches the new commit.

Accept the two-operation lifecycle used by Jujutsu 0.42.
If a failure occurs after registration, report the partial state and an explicit recovery action.
Do not remove user files automatically.

The pinned APIs also support sparse-pattern copying directly.
Implement `copy`, `full`, and `empty` unless focused tests expose a blocking incompatibility.

## Adapter removal conditions

The configuration adapter is not temporary for jj-lib 0.42.
Re-evaluate it only during a separate Jujutsu upgrade.
Remove policy code when jj-lib exposes an equivalent stable loader without adding `jj-cli`.

No temporary compatibility patch is planned.

## Evidence limits

The baseline proves only the old documented behavior.
It does not prove modern repository configuration, workspace configuration, conditional scopes, or arbitrary parents.
The implementation requires new differential tests before any fidelity claim is valid.

## Final evidence

The implementation now has focused differential coverage for default, explicit, stable change ID,
typed revset, multi-parent, conflict, file checkout, sparse, validation, and operation behavior.
Configuration coverage includes secure repository and workspace layers, precedence, path and
environment conditions, user paths, empty `JJ_CONFIG`, warning delivery, and no-create loads.

- Build gate: passed. See [`artifacts/20260819T220034Z-build/`](artifacts/20260819T220034Z-build/).
- Complete test gate: passed. See [`artifacts/20260819T220058Z-test/`](artifacts/20260819T220058Z-test/).
- Lint gate: passed. See [`artifacts/20260819T220137Z-lint/`](artifacts/20260819T220137Z-lint/).

The tests now induce a safe post-registration checkout failure. `tests/test_workspace_mgmt.py::
test_add_workspace_checkout_failure_raises_partial_workspace_error` builds a git commit whose tree
holds `.jj/marker`, imports it with `git_import()`, and passes it as the new workspace parent. Step 3
registers the workspace and creates `.jj` in the destination. Step 4 then fails, because jj refuses
to check out the reserved `.jj` path component. The induction is deterministic. It changes no
permission and uses no race.

The test asserts the whole `PartialWorkspaceError` contract: the message names the workspace, the
retained path, and `forget_workspace`; the destination files survive; the workspace stays registered
in both `workspaces()` and the `jj` CLI; and `forget_workspace()` clears the registration and leaves
the repository usable. It also confirms `PartialWorkspaceError` subclasses `WorkspaceError`.

A read-only destination does not reach this path. The destination must be an empty directory at
validation time, and step 3 creates `.jj` inside it. Removing write permission therefore fails in
step 3 with a plain `WorkspaceError` ("Cannot access <path>/.jj"), before any registration exists.

## Live acceptance evidence

The standalone verifier creates isolated home and configuration directories. It uses real on-disk
repositories and workspaces. It runs Pyjutsu against the built extension and uses `jj 0.42.0` as
the differential oracle.

The live run proved:

- default sibling placement and the two-operation lifecycle;
- stable change ID and typed `Revset` placement;
- multi-parent topology, identical merged tree IDs, conflict metadata, and checked-out files;
- copy, full, and empty sparse modes;
- pre-mutation rejection for invalid, empty, and multiple-result revsets;
- duplicate-name and non-empty-destination rejection without lost files;
- repository identity parity for primary, secondary, and CLI authoring;
- distinct workspace configuration, repository/workspace path conditions, and environment overrides;
- no secure configuration creation during a normal load; and
- secure configuration migration warnings delivered through Python.

The first complete live run passed. See
[`artifacts/20260819T224136Z-live-run/`](artifacts/20260819T224136Z-live-run/).
The generated repositories are local runtime artifacts. The committed evidence contains commands,
environment details, build output, and the complete structured live log.

The final verification repeated all canonical gates and the live run after the verifier and lint
scope changes. All gates passed. The fresh live run passed 43 assertions and five expected-error
checks. See [`artifacts/20260819T224733Z-final/`](artifacts/20260819T224733Z-final/).

A post-review run narrowed validation checks to `PyjutsuError`, reran the complete lint gate, and
repeated all 43 live assertions. It passed. See
[`artifacts/20260819T225032Z-post-review/`](artifacts/20260819T225032Z-post-review/).
