---
title: Make secondary workspaces first-class for orchestration consumers
type: issue
status: done
loci:
  schema: 1
  id: 01a01bd7-6b5b-7000-bf4b-cf03c0af3a5f
  projects: []
---

# ISSUE: Make secondary workspaces first-class for orchestration consumers

## Summary

Pyjutsu's workspace API needs two related fidelity/correctness improvements before it is a strong substrate for programs that create and manage many secondary Jujutsu workspaces:

1. **`Workspace.add_workspace()` must support creating the new workspace's working-copy commit on arbitrary parent revision(s)** instead of always placing it on `root()`.
2. **Workspace loading must resolve repository/workspace configuration correctly for secondary workspaces**, instead of deriving repository configuration from `<workspace>/.jj/repo/config.toml`.

These are general Pyjutsu improvements, not application-specific workflow policy. They are immediately needed by a higher-level orchestration/project-management consumer that will use Pyjutsu to create isolated candidate/agent workspaces.

The desired consumer flow is:

```python
candidate_info = repo.add_workspace(
    ".workspaces/wp-42-candidate-a",
    name="wp-42-candidate-a",
    revisions=[baseline_change_id],
)
```

The resulting workspace should have an empty `@` directly on the requested baseline and should use the same effective repository/user configuration semantics as the equivalent workspace created through the pinned JJ CLI.

No consumer-side "create on root, then repair it" choreography should be required.

---

## Why this matters

The intended consumer will use JJ workspaces as isolated engineering benches:

```text
accepted baseline
      │
      ├── candidate A workspace
      │       └── agent A edits
      │
      ├── candidate B workspace
      │       └── agent B edits
      │
      └── candidate C workspace
              └── agent C edits
```

Each workspace must begin from an explicitly selected baseline.

Typical examples:

```text
main
  ↓
WP-42 candidate A

main
  ↓
WP-42 candidate B

CR-17 candidate revision
  ↓
review-fix workspace
```

or stacked work:

```text
WP-10 accepted candidate
        ↓
WP-11 candidate
```

Creating a workspace on `root()` and then repositioning it is the wrong abstraction. The workspace should be born in the intended topology.

Secondary workspaces also need consistent configuration. In an agent-heavy consumer, secondary workspaces are not an edge case; they become the normal authoring environment.

The invariants should be:

```text
same repository
    ⇒ same repository-level configuration

same user environment
    ⇒ same user-level configuration

different workspace
    ⇒ only intentionally workspace-specific configuration may differ
```

---

# Current behavior

## 1. `add_workspace()` always starts on `root()`

The public Python surface currently exposes only path/name selection:

```python
def add_workspace(
    self,
    path: str | os.PathLike[str],
    *,
    name: str | None = None,
) -> WorkspaceInfo:
    ...
```

The native stub likewise has no revision/parent argument:

```python
class PyWorkspace:
    def add_workspace(
        self,
        path: str | os.PathLike[str],
        name: str | None = ...,
    ) -> dict[str, object]:
        ...
```

The Rust binding explicitly documents the current limitation: it matches `jj workspace add` except that the new `@` lands on `root()`, while the CLI supports `-r/--revision` placement and has different default-parent behavior.

`tests/test_workspace_mgmt.py` currently locks in the root-based behavior by asserting that a newly created workspace has an empty `@` whose parent is the root commit.

This is not merely a default around a hidden placement API. Pyjutsu currently has no public/native argument that lets a caller choose the new working-copy commit's parent revision(s).

### Upstream JJ 0.42 behavior

The pinned JJ version already supports exactly what is needed.

`jj workspace add` accepts zero or more `--revision/-r` values.

In upstream `cli/src/commands/workspace/add.rs` at tag `v0.42.0`:

- **No revisions supplied:** the new workspace working-copy commit is created on the parent(s) of the current workspace's working-copy commit.
- **One or more revisions supplied:** those revisions become the parents of the new workspace's working-copy commit, equivalent to `jj new r1 r2 ...`.

That is the primitive Pyjutsu should expose.

Primary source:

https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/commands/workspace/add.rs

---

## 2. Repository configuration loading is not correct for modern/secondary workspaces

`src/workspace.rs::load_user_settings()` currently constructs settings approximately as:

```text
built-in defaults
→ user config
→ <workspace_root>/.jj/repo/config.toml
```

The implementation derives repo configuration from:

```rust
let repo_config = workspace_root
    .join(".jj")
    .join("repo")
    .join("config.toml");
```

and notes that secondary workspaces use a `.jj/repo` pointer file, so that repo layer is skipped for secondary workspaces.

That already creates a primary/secondary behavioral difference.

More importantly, this storage assumption is legacy relative to the pinned JJ release. JJ moved repository/workspace configuration to secure external storage before JJ 0.42. The 0.42 CLI configuration loader uses `SecureConfig` and config storage rooted under JJ's configuration directory rather than treating `.jj/repo/config.toml` as the modern canonical repository-config file.

Therefore the problem is broader than:

> secondary workspaces cannot find `.jj/repo/config.toml`

The correct problem statement is:

> Pyjutsu must resolve the modern JJ repository/workspace configuration identity and load configuration associated with the shared repository/workspace rather than deriving repo configuration from a legacy path under the current working-copy directory.

This matters because `UserSettings` affects commit authorship and other JJ behavior.

Current differential tests can hide this because their identity is supplied through `JJ_CONFIG` at the user layer. A globally pinned user identity does not prove that repo/workspace configuration is being resolved correctly.

Primary source for JJ 0.42 config loading:

https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/config.rs

---

# Required change A — revision-aware workspace creation

## Target public API

Recommended Python API:

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

Examples:

```python
# Explicit baseline
ws.add_workspace(
    ".workspaces/candidate-a",
    name="candidate-a",
    revisions="main",
)

# Stable JJ change ID
ws.add_workspace(
    ".workspaces/candidate-b",
    name="candidate-b",
    revisions=baseline.change_id,
)

# Multiple parents / integration probe
ws.add_workspace(
    ".workspaces/integration-probe",
    revisions=[candidate_a.change_id, candidate_b.change_id],
)
```

The Python facade should normalize all accepted forms into a plain list of rendered revset strings before crossing the FFI boundary.

Conceptually:

```text
None
    → None

"main"
    → ["main"]

R.bookmark("main")
    → ['bookmarks(exact:"main")']

["main", some_revset]
    → ["main", "<rendered revset>"]
```

The native API should stay mechanical:

```python
class PyWorkspace:
    def add_workspace(
        self,
        path: str | os.PathLike[str],
        name: str | None = ...,
        revisions: list[str] | None = ...,
    ) -> dict[str, object]:
        ...
```

Python owns coercion/ergonomics; Rust owns jj-lib calls.

---

## Default behavior

### Preferred: match JJ

When `revisions=None`, Pyjutsu should match `jj workspace add`:

```text
new workspace @
    ↓ parents
parents(current workspace @)
```

The new and current workspaces therefore have sibling working-copy commits by default.

The existing root-based behavior remains explicitly expressible:

```python
ws.add_workspace(path, revisions="root()")
```

This is preferable for a library whose stated goal is faithful JJ primitives.

### Compatibility consideration

Changing `revisions=None` from `root()` to JJ's real default changes documented behavior.

If compatibility with existing consumers is important, the project could temporarily preserve the old default while adding explicit `revisions=` support. However, that would intentionally retain a non-JJ default and require a later migration.

Preferred end state:

```text
None       => upstream JJ default
"root()"   => old Pyjutsu behavior explicitly
```

---

## Support multiple parents

Do not expose only a singular `revision=` argument.

JJ natively supports:

```text
workspace @
   /     \
parent A parent B
```

and upstream accepts multiple `-r` values.

Even if the initial orchestration consumer usually creates one-parent candidate workspaces, exposing the true JJ primitive now prevents an unnecessarily narrow API and naturally supports integration probes and merge-parent workspaces.

Recommended public name: `revisions`.

---

## Expected implementation shape

The upstream CLI performs the conceptual sequence:

1. create/register the workspace;
2. determine requested parents;
3. merge parent trees using JJ semantics;
4. create a new empty working-copy commit with those parents;
5. edit the new workspace onto that commit;
6. finish the working-copy mutation.

Pyjutsu should follow the same semantics rather than creating on root and requiring consumers to repair the topology afterward.

### Operation-count decision

The current Pyjutsu implementation intentionally advertises one operation for workspace creation.

Upstream `jj workspace add` has richer lifecycle behavior and may publish separate workspace-registration / initial-working-copy operations.

For this change, **semantic fidelity should take priority over preserving the current one-operation simplification**.

The acceptance criterion should be:

> resulting topology and working-copy state match the pinned JJ CLI for the same requested parents

not:

> `add_workspace()` must always publish exactly one operation

If one-operation behavior is naturally achievable without custom complexity, it is fine. It should not drive the design.

---

## Working-copy tree semantics

The new `@` should use the merged tree of its requested parent(s), exactly as JJ does.

Single parent:

```text
baseline tree
    ↓
new empty @
```

The new working directory should contain baseline files immediately.

Multiple parents should use JJ's own merge behavior. Do not manually copy files or approximate a Git merge. Conflicts must remain first-class JJ conflicts.

---

## Sparse patterns

Full sparse-workspace parity is not required for the initial orchestration bootstrap, but upstream `workspace add` also handles sparse-pattern inheritance.

Two reasonable scopes:

### Minimum scope

Add revision placement only and explicitly document sparse behavior as unchanged/out of scope.

### Better-fidelity scope

Expose something like:

```python
sparse_patterns: Literal["copy", "full", "empty"] = "copy"
```

matching JJ's conceptual choices.

A higher-level agent orchestrator may choose `full`, but that is consumer policy and should not be hard-coded in Pyjutsu.

---

# Required change B — correct modern configuration resolution

## Goal

Loading either the primary workspace or any secondary workspace should resolve configuration from the same canonical shared repository identity, with intentional workspace-specific configuration layered appropriately.

This must hold regardless of whether:

```text
<workspace>/.jj/repo
```

is:

- a directory in the primary workspace, or
- a pointer file in a secondary workspace.

Filesystem layout of the working-copy metadata must not decide whether repo settings exist.

---

## Do not solve this by merely following the pointer to `config.toml`

A tempting narrow patch is:

```text
if .jj/repo is a file:
    resolve pointer
    append config.toml
```

That should **not** be the final implementation.

It would repair the old path assumption while remaining inconsistent with JJ 0.42's secure external repository/workspace config model.

The target is modern JJ configuration fidelity.

---

## Configuration layers

At minimum, Pyjutsu should correctly account for:

```text
built-in defaults
user configuration
repository configuration
workspace configuration
```

with precedence matching the pinned JJ behavior.

Conditional configuration should also be considered where it affects final `UserSettings`, particularly conditions involving:

```text
repository path
workspace path
hostname/environment
```

The exact supported surface should be verified against JJ 0.42's `ConfigEnv` and `jj_lib::secure_config` behavior rather than reconstructed from old assumptions.

Useful upstream jj-lib primitive:

```rust
jj_lib::secure_config::SecureConfig
```

Pyjutsu should not depend on the full JJ CLI crate just to obtain configuration behavior if the required semantics can be composed from `jj-lib`.

---

## Refactor `load_user_settings`

The current signature:

```rust
fn load_user_settings(workspace_root: &Path) -> Result<UserSettings, PyErr>
```

encourages the wrong model because it assumes all configuration can be derived directly from the current working-copy path before canonical repo/workspace identity has been resolved.

Refactor toward an API/lifecycle where repository and workspace identity are explicit.

Conceptually:

```rust
fn load_user_settings(
    workspace_root: &Path,
    repo_path: Option<&Path>,
    workspace_name: Option<&WorkspaceName>,
    ...
) -> Result<UserSettings, PyErr>
```

or split loading into bootstrap and resolved phases.

Likely conceptual flow:

```text
1. load built-ins + user config
2. load enough workspace metadata to identify the shared repo
3. resolve secure repo/workspace config
4. resolve conditional configuration context
5. build final UserSettings
6. use those settings consistently for authoring and revset context
```

The exact implementation should follow the pinned jj-lib lifecycle.

Do not invent a second notion of repository root if `jj_lib::Workspace`, `RepoLoader`, or related jj-lib objects can provide the canonical repository path.

---

## Secondary workspace authoring invariant

After this fix, the following should hold unless workspace-specific configuration intentionally differs:

```python
primary = Workspace.load(primary_path)
secondary = Workspace.load(secondary_path)

# conceptually
primary_effective_repo_settings == secondary_effective_repo_settings
```

Equivalent operations should use the same:

```text
user.name
user.email
relevant authoring settings
repository-level settings
```

and should match the pinned JJ CLI when timestamps/randomness/test fixtures are controlled.

The current documentation warning that secondary workspaces may legitimately produce different commit IDs because repository config is skipped should be removed once this is fixed.

---

# Why these changes belong together

They meet in the same lifecycle:

```text
create secondary workspace
        ↓
position on selected baseline
        ↓
load/use as a normal authoring workspace
        ↓
agent edits
        ↓
snapshot / mutate
        ↓
produce candidate changes
```

Revision placement without configuration fidelity creates correctly positioned but potentially inconsistently authored workspaces.

Configuration fidelity without revision placement still forces every consumer to perform unnecessary topology-repair steps.

Together they make secondary workspaces genuinely first-class Pyjutsu objects.

---

# Higher-level consumer boundary

The future orchestration consumer may model:

```text
Issue
  ↓
Work Package
  ↓
Candidate
  ↓
JJ workspace + JJ change(s)
  ↓
Change Request
  ↓
verification / review
  ↓
integration
```

**None of those concepts should be added to Pyjutsu.**

Consumer code should be able to compose the primitive:

```python
class CandidateWorkspaceService:
    def create_candidate(self, *, name: str, baseline: str):
        return self.repo.add_workspace(
            self.workspace_dir / name,
            name=name,
            revisions=baseline,
        )
```

Pyjutsu's responsibility ends at:

> create a correct JJ workspace on the requested revisions and load/use it with correct JJ configuration semantics.

This preserves the architectural boundary:

```text
Pyjutsu = faithful JJ primitives
consumer = workflow policy
```

---

# API examples after the change

## Candidate from trunk

```python
from pyjutsu import Workspace

repo = Workspace.load("repo")

info = repo.add_workspace(
    ".workspaces/wp-42-a",
    name="wp-42-a",
    revisions="trunk()",
)

candidate = Workspace.load(info.path)
```

Expected graph:

```text
trunk()
   │
   └── @  wp-42-a
```

---

## Candidate from stable change ID

```python
baseline = repo.resolve("main")

info = repo.add_workspace(
    ".workspaces/wp-42-b",
    name="wp-42-b",
    revisions=baseline.change_id,
)
```

This is especially useful to higher-level systems because logical work references can store stable JJ change IDs while exact revisions continue to rewrite normally.

---

## Sibling workspace using JJ default

```python
info = repo.add_workspace(
    ".workspaces/experiment",
    name="experiment",
)
```

Expected semantics should match:

```bash
jj workspace add .workspaces/experiment --name experiment
```

The new `@` should use the current workspace `@`'s parent(s), not `root()` unconditionally.

---

## Multi-parent integration probe

```python
repo.add_workspace(
    ".workspaces/integration-probe",
    revisions=[
        candidate_a.change_id,
        candidate_b.change_id,
    ],
)
```

This gives consumers a natural way to inspect JJ's merged working-copy state and conflicts without inventing a separate merge approximation.

---

# Required code areas

## Rust — `src/workspace.rs`

Likely changes:

- `PyWorkspace::add_workspace`
- workspace registration/initial working-copy placement logic
- revset resolution for requested parents
- merged-parent tree creation using jj-lib semantics
- working-copy mutation/finalization
- `load_user_settings`
- secure repository/workspace configuration resolution
- workspace load/init lifecycle if necessary to support resolved settings

Keep policy/default coercion out of Rust.

---

## Native type stub — `python/pyjutsu/_pyjutsu.pyi`

Update `PyWorkspace.add_workspace(...)` to accept normalized revision strings:

```python
def add_workspace(
    self,
    path: str | os.PathLike[str],
    name: str | None = ...,
    revisions: list[str] | None = ...,
) -> dict[str, object]: ...
```

Keep the stub synchronized with the native extension.

---

## Python facade — `python/pyjutsu/workspace.py`

Update `Workspace.add_workspace(...)` to:

- accept `str | Revset | Sequence[str | Revset] | None`;
- normalize to `list[str] | None`;
- document default-parent behavior precisely;
- document multi-parent semantics;
- remove the warning that secondary workspaces inherently skip repo settings after configuration fidelity is fixed.

Consider factoring a small internal multi-revision normalizer if the same shape is likely to be reused elsewhere.

---

## Tests — `tests/test_workspace_mgmt.py`

Replace the root-only contract with coverage for:

- no explicit revision;
- one explicit revision;
- a stable change ID;
- bookmark/revset input;
- multiple parents;
- invalid revision;
- zero-match revision;
- ambiguous/multi-match revision where a single endpoint is required;
- resulting working-copy tree;
- workspace registration;
- operation-log behavior as appropriate.

Use differential tests against the pinned CLI as the main correctness contract.

---

## New configuration tests

Add focused tests such as:

```text
test_secondary_workspace_loads_repo_config
test_primary_and_secondary_workspace_authoring_identity_match
test_secondary_workspace_matches_cli_commit_metadata
test_workspace_specific_config_is_respected
test_repo_config_precedence_over_user_config
test_secondary_workspace_conditions_resolve_against_correct_paths
```

The exact set depends on how fully Pyjutsu wants to mirror JJ's current configuration stack, but at least one test must place relevant configuration at the **repo layer rather than `JJ_CONFIG`** so the existing masking behavior cannot pass accidentally.

---

## Differential CLI helper

Extend the test helper so equivalent CLI workspaces can be created with:

```bash
jj workspace add -r <REV> ...
```

and with multiple `-r` values.

This should be the source of truth for workspace topology against the exact pinned CLI version.

---

## Documentation

Update:

```text
README.md
docs/USER_GUIDE.md
docs/DEV_GUIDE.md
docs/PYJUTSU_CONCEPT.md
```

Remove/update statements that:

- `add_workspace()` intentionally only places `@` on `root()`;
- arbitrary revision placement is out of scope;
- secondary-workspace repo config is expected to be skipped;
- commit-ID parity should only be expected from the default workspace for that reason.

Document the modern configuration strategy and explicitly avoid teaching `.jj/repo/config.toml` as the canonical current config location.

---

# Test strategy

## 1. Explicit revision differential

Create equivalent repos.

Pyjutsu:

```python
info = ws.add_workspace(
    py_path,
    name="candidate",
    revisions="main",
)
```

CLI:

```bash
jj workspace add \
    --name candidate \
    -r main \
    cli_path
```

Assert:

```text
workspace exists
same parent commit(s)
same working-copy tree
working-copy commit is empty
same conflict state
same logical graph shape
```

Exact commit-ID parity may be asserted when authoring metadata/timestamps/randomness are controlled.

---

## 2. Default behavior differential

Compare:

```python
ws.add_workspace(path)
```

with:

```bash
jj workspace add path
```

Verify that the new working-copy commits have the same parents.

This specifically prevents reintroducing the old root-only default.

---

## 3. Multiple-parent differential

Construct:

```text
A    B
 \  /
 new workspace @
```

Compare Pyjutsu against:

```bash
jj workspace add -r A -r B ...
```

Verify:

- parent IDs;
- merged tree;
- conflict state/content for conflicting parents.

---

## 4. Repository-config parity

Use a repository-level setting that affects authored commit metadata or another directly observable `UserSettings` behavior.

Do **not** place the setting in `JJ_CONFIG`.

Create:

```text
primary workspace
secondary workspace
```

Then author equivalent commits through:

```text
pinned jj CLI
Pyjutsu primary workspace
Pyjutsu secondary workspace
```

Assert effective configuration/metadata parity.

This test must prove that repo config is loaded via JJ's modern mechanism rather than accidentally inherited from the global test user layer.

---

## 5. Workspace-specific config

If Pyjutsu supports JJ workspace config fully, verify an intentional difference:

```text
primary workspace     → primary expected value
secondary workspace   → secondary expected value
```

This proves that fixing shared repo config does not incorrectly collapse intentionally workspace-specific settings.

---

# Acceptance criteria

## Revision-aware workspace creation

- [ ] `Workspace.add_workspace()` accepts zero, one, or multiple parent revisions.
- [ ] Public arguments may be normal revset strings or Pyjutsu `Revset` objects.
- [ ] Explicit revisions produce the same parent topology as JJ 0.42 `workspace add -r`.
- [ ] Multiple revisions use JJ's merged-tree semantics.
- [ ] `revisions=None` matches JJ's default placement.
- [ ] `revisions="root()"` explicitly reproduces the old Pyjutsu placement when desired.
- [ ] Invalid/ambiguous revisions fail with an appropriate Pyjutsu exception.
- [ ] Failure does not silently leave a semantically wrong workspace without clear error handling.
- [ ] The on-disk working copy matches the new working-copy commit.
- [ ] Differential tests cover default, one-parent, and multi-parent creation.

## Configuration fidelity

- [ ] Pyjutsu no longer treats `<workspace>/.jj/repo/config.toml` as the canonical modern repository-config path.
- [ ] Repository configuration is identified from the shared JJ repository, independent of primary/secondary `.jj/repo` layout.
- [ ] Secondary workspaces receive the same repository-level settings as the primary workspace.
- [ ] Workspace-specific settings remain distinct when JJ defines them to be distinct.
- [ ] Supported configuration precedence matches pinned JJ behavior.
- [ ] Differential tests prove authoring/config parity from primary and secondary workspaces.
- [ ] Tests exercise repo-level config rather than relying exclusively on `JJ_CONFIG`.
- [ ] Documentation no longer warns that secondary-workspace config divergence is expected for this implementation reason.

## Architecture

- [ ] No Work Package/Candidate/agent/orchestration concepts are added to Pyjutsu.
- [ ] Rust remains a thin jj-lib binding layer.
- [ ] Python owns public coercion/default ergonomics.
- [ ] `_pyjutsu.pyi` stays synchronized with the native surface.
- [ ] No subprocess `jj workspace add` path becomes the primary implementation.
- [ ] `run_jj()` remains only the generic escape hatch.

---

# Recommended implementation order

## Slice 1 — lock down upstream contracts

Before implementation:

1. extend the CLI differential helper for:
   - default workspace add;
   - `-r` one-parent;
   - multi-parent `-r A -r B`;
2. add a regression test that demonstrates current modern repo-config mismatch, especially from a secondary workspace.

This turns the desired behavior into executable contracts before refactoring.

---

## Slice 2 — fix configuration resolution

Do this before relying on secondary workspaces for authoring.

Target:

```text
primary load
secondary load
    ↓
correct shared repo config
    +
correct workspace-specific config
```

Re-run representative mutation/commit-parity tests from secondary workspaces.

This may expose previously hidden metadata differences that the existing `JJ_CONFIG` fixture masked.

---

## Slice 3 — add `revisions` to workspace creation

Extend in order:

```text
Rust PyWorkspace
↓
.pyi stub
↓
Python Workspace facade
```

Keep Python coercion on the Python side and revset/JJ mutation semantics in Rust.

---

## Slice 4 — align default placement with JJ

Once explicit revision placement works:

```text
revisions=None
```

should follow the CLI default.

Update/remove tests that assert unconditional root placement.

If compatibility requires a staged migration, make that an explicit release decision rather than leaving the divergence accidental.

---

## Slice 5 — optional sparse-pattern parity

Add sparse inheritance if it fits cleanly without delaying the two required fixes.

It is useful but not a blocker for the first orchestration consumer.

---

## Slice 6 — docs and full validation

Update documentation and run the repository's canonical devenv tasks:

```bash
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint
```

During development, run focused workspace/config tests serially when operation-log output needs inspection.

---

# Non-goals

This issue should **not** add:

- Work Packages;
- Candidates;
- Change Requests;
- agent scheduling;
- workspace pools;
- automatic candidate naming;
- project-management metadata;
- orchestration domain events;
- integration policy;
- automatic workspace cleanup policy;
- opinionated bookmark creation;
- a custom branch model.

Those belong above Pyjutsu.

The Pyjutsu primitive should simply become powerful enough that a consumer can say:

```python
workspace = repo.add_workspace(
    path,
    name=name,
    revisions=parents,
)
```

and trust the result.

---

# Why not use a consumer-side workaround?

A consumer can technically do:

```text
add workspace on root
        ↓
load new workspace
        ↓
edit/rewrite @
        ↓
create desired child
```

That should not be canonical.

Problems:

1. **Wrong transient state** — the workspace exists in a topology the caller never requested.
2. **Additional operations** — repo history contains setup/repair operations that are implementation artifacts.
3. **More failure points** — a crash between creation and repositioning can leave a valid but semantically wrong workspace.
4. **Repeated choreography** — every Pyjutsu consumer needing normal JJ workspace placement must reinvent the same sequence.
5. **Incomplete fidelity** — it does not solve configuration-loading differences.
6. **Harder orchestration** — systems creating many workspaces concurrently need a clean, reliable lifecycle primitive.

The missing behavior belongs in Pyjutsu because it is a JJ capability, not workflow policy.

---

# Architectural outcome

After this issue, Pyjutsu's workspace model should support:

```text
                shared JJ repository
                       │
       ┌───────────────┼───────────────┐
       │               │               │
   primary WS      candidate WS    candidate WS
       │               │               │
       @               @               @
       │               │               │
   selected         selected         selected
   parent(s)        parent(s)        parent(s)

       └──── same repo-level configuration ────┘
              workspace-specific config
              differs only intentionally
```

That provides a clean substrate for:

```text
parallel candidate development
isolated agent workspaces
integration probes
review/fix workspaces
stacked work packages
temporary experiments
```

without Pyjutsu needing to know anything about those workflows.

---

# Primary source references

## Pyjutsu

Relevant current files:

```text
src/workspace.rs
python/pyjutsu/workspace.py
python/pyjutsu/_pyjutsu.pyi
tests/test_workspace_mgmt.py
docs/USER_GUIDE.md
docs/DEV_GUIDE.md
docs/PYJUTSU_CONCEPT.md
```

Current behaviors to replace:

```text
Workspace.add_workspace()
    → new @ always on root()

load_user_settings(workspace_root)
    → built-ins + user config + legacy workspace-root/.jj/repo/config.toml
```

## JJ 0.42.0

Workspace-add source:

https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/commands/workspace/add.rs

Configuration source:

https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/config.rs

Pyjutsu currently binds JJ 0.42.0, so implementation and tests should follow the 0.42 behavior rather than legacy `.jj/repo/config.toml` assumptions.

---

# Definition of done

This should be a normal supported Pyjutsu workflow:

```python
from pyjutsu import Workspace

repo = Workspace.load("/project")
baseline = repo.resolve("main")

info = repo.add_workspace(
    "/project-workspaces/candidate-a",
    name="candidate-a",
    revisions=baseline.change_id,
)

candidate = Workspace.load(info.path)

assert candidate.working_copy().parent_ids == [baseline.commit_id]
```

and `candidate` should author subsequent changes with the same effective repository/user configuration semantics as an equivalent workspace created and used through the pinned JJ 0.42 CLI.

At that point a higher-level orchestrator can safely treat secondary Pyjutsu workspaces as first-class, correctly positioned and correctly configured JJ authoring environments rather than partially supported special cases.

---

# Resolution

Delivered:

- `Workspace.add_workspace()` accepts zero, one, or many parent revisions. It resolves them before any filesystem or repository mutation.
- `src/config_loader.rs` resolves the canonical repository path with jj-lib's workspace loader, then reads secure repository and workspace configuration. Primary and secondary workspaces share repository configuration.
- Sparse patterns support `copy`, `full`, and `empty`.
- Commit 445f107 added two corrections. Explicit parent revisions deduplicate to match the CLI. `Workspace.init` re-resolves settings, so conditional scopes apply.

Gates at close: 364 pytest tests pass, 7 cargo tests pass, ruff is clean, clippy is clean, and the live acceptance script passes 43 assertions.

Open gaps:

- Revset evaluation still ignores repository and workspace configuration. [[.loci/issues/003-revset-ignores-configuration/issue.md]] records it. That gap also blocks configurable immutability enforcement.
- `add_workspace` requires each explicit revset to resolve to exactly one commit. `jj workspace add -r 'A|B'` is less strict. This is deliberate and documented.
