# Implementation prompt

Work in the Pyjutsu repository. Implement the complete issue in
`.loci/issues/001-secondary-workspaces-first-class/issue.md`.

Use the `build-run-investigation-loop` skill for this task. Read its full
instructions before you change code. Also read `AGENTS.md` and the full
`.agents/skills/my-ai/SKILL.md` file. Follow the repository's manager routing,
devenv, verification, commit, and push rules.

## Objective

Make secondary Jujutsu workspaces first-class Pyjutsu authoring workspaces.

Deliver both required capabilities:

1. `Workspace.add_workspace()` accepts zero, one, or multiple parent
   revisions.
2. Every workspace loads modern repository and workspace configuration from
   the correct Jujutsu identity.

Keep Pyjutsu on jj-lib 0.42.0 for this issue. Do not combine this work with the
0.44 upgrade.

## Start with evidence

Before implementation:

1. Read the complete issue file.
2. Inspect the current Python facade, native stub, Rust workspace loader,
   revset helpers, transaction code, and workspace tests.
3. Inspect the exact jj-lib 0.42.0 source in the Cargo registry.
4. Inspect the pinned `jj` 0.42.0 workspace-add and configuration source.
5. Run the current focused workspace tests and preserve their output.
6. Write the dated research report required by the investigation skill.

Use upstream Jujutsu source and documentation as primary sources. Do not infer
CLI behavior from memory.

## Configuration implementation

Fix configuration loading first.

Create a focused Rust configuration module instead of expanding
`src/workspace.rs` with more policy.

Use jj-lib's workspace-loader abstraction to resolve the canonical repository
path before constructing final `UserSettings`. Do not parse a secondary
workspace's `.jj/repo` pointer in Pyjutsu.

Use `SecureConfig` for repository and workspace configuration. Load existing
secure configuration without creating empty configuration files during a
normal workspace load.

Match the supported Jujutsu 0.42 precedence:

```text
required defaults
environment base values
user configuration
repository configuration
workspace configuration
environment overrides
conditional resolution
```

Reproduce the relevant Jujutsu user paths, including the legacy home file,
the platform config file, and `conf.d`. Preserve `JJ_CONFIG` semantics,
including an explicitly empty value.

Resolve conditional configuration with the canonical repository path and the
current workspace path. Include the environment and hostname context that can
affect authoring settings.

Keep initialization as a bootstrap path. A repository does not yet have
repository configuration during its initial creation.

Do not add `jj-cli` as a runtime dependency. Use a small, parity-tested adapter
over jj-lib unless direct evidence proves that approach cannot meet the
required authoring contract.

Surface secure-config warnings through an appropriate Python warning or
documented diagnostic. Do not discard warnings silently.

## Workspace creation implementation

Expose this Python API:

```python
def add_workspace(
    self,
    path: str | os.PathLike[str],
    *,
    name: str | None = None,
    revisions: str | Revset | Sequence[str | Revset] | None = None,
) -> WorkspaceInfo:
    ...
```

Normalize all Python values into `list[str] | None` before the native call.
Treat a string as one revision, not as a sequence of characters.

Use a plural native `revisions` argument. Keep Rust responsible for revset
resolution and jj-lib mutation semantics.

Resolve explicit parent revisions before creating filesystem or workspace
state. Each supplied expression must resolve to exactly one commit.

For `revisions=None`, use the current workspace commit's parents. Fall back to
the root commit only when the current workspace has no working-copy commit.

Use Jujutsu's merged-parent-tree semantics. Preserve conflicts as Jujutsu
conflicts. Do not copy files or approximate a Git merge.

Use jj-lib's workspace-registration primitive. Then create and edit the new
working-copy commit on the requested parents. Finish the new workspace's
working-copy mutation so its files match its new `@` before returning.

Accept the faithful two-operation lifecycle. Do not copy private jj-lib code
to preserve Pyjutsu's old one-operation promise.

Validate the destination and workspace name before registration. Reject a
non-empty destination and duplicate workspace name. If failure occurs after
registration, return an explicit partial-state error with a recovery action.
Do not delete user files automatically.

Make the default match `jj workspace add` now:

```text
revisions=None      -> parents of the source workspace @
revisions="root()"  -> the old Pyjutsu behavior
```

## Sparse patterns

Do not claim complete CLI parity while sparse behavior differs.

If the jj-lib 0.42 APIs support it cleanly, expose:

```python
sparse_patterns: Literal["copy", "full", "empty"] = "copy"
```

If this would delay the two required fixes, preserve the current sparse
behavior and document the exact difference. Create a focused follow-up issue
under `.loci/issues/` through the loci CLI.

## Required tests

Add differential tests against the pinned `jj` 0.42.0 CLI for:

- default sibling placement;
- one explicit parent;
- a stable change ID;
- a typed `Revset` value;
- multiple parents;
- conflicting parent trees;
- resulting files and working-copy tree;
- invalid, empty, and multi-result revsets;
- duplicate names and non-empty destinations;
- workspace registration and operation descriptions;
- partial-state error behavior where it can be induced safely.

Add configuration tests that create repository configuration through the
pinned CLI. Do not place the value under test in `JJ_CONFIG`.

Prove authoring parity from:

- the primary Pyjutsu workspace;
- a secondary Pyjutsu workspace;
- an equivalent pinned-CLI workspace.

Also test intentional workspace-specific configuration and conditional
configuration based on repository and workspace paths.

Control identity, timestamps, and randomness where exact commit IDs are part
of the assertion.

## Documentation and cleanup

Update the public facade documentation, native stub, README, user guide,
developer guide, and concept document.

Remove the warning that secondary workspaces inherently skip repository
configuration.

Correct the stale concept example that uses `at=` and claims
`add_workspace()` returns a `Workspace` handle.

Do not add candidate, work-package, agent, scheduling, cleanup, or integration
policy to Pyjutsu.

Do not use a subprocess `jj workspace add` implementation. Keep `run_jj()` as
the explicit escape hatch.

## Verification and delivery

Run focused tests after each slice. Then run the repository's complete build,
test, and lint gates inside devenv.

Inspect the final diff and repository status. Include all relevant active
changes. Commit only after the full gate is green. Push the completed commit
to the current branch.

In the final report, include:

- the configuration lifecycle implemented;
- the exact workspace-parent behavior;
- sparse-pattern scope;
- operation behavior;
- tests and gate results;
- commit and pushed branch;
- any follow-up issue created.

Do not stop after analysis. Continue until the complete issue is implemented,
verified, committed, and pushed, or until a concrete external blocker requires
user action.
