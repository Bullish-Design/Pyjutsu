---
title: Revset evaluation ignores repository and workspace configuration
type: issue
status: active
loci:
  schema: 1
  id: 01a01fd4-c52a-7000-ba1a-72f77585ddf5
  projects:
    - 01a02558-2047-7000-9372-7f439920f294
---

# ISSUE: Revset evaluation ignores repository and workspace configuration

## Summary

Issue 001 fixed configuration loading. `src/config_loader.rs` now resolves user,
secure repository, and secure workspace configuration from the correct Jujutsu
identity. Primary and secondary workspaces both reach the same repository
configuration. Those settings build `UserSettings`, and `UserSettings` drives
commit authoring.

Revset evaluation never consumes that configuration.
`src/revset.rs::evaluate_revset` builds its `RevsetParseContext` from hardcoded
values. It uses an empty `RevsetAliasesMap` and sets `use_glob_by_default:
false`. Only one field of the resolved settings reaches the parse context: the
user email.

The result is a correctness and fidelity gap:

```text
config_loader.rs   →  UserSettings  →  commit authoring     ✅ consumed
config_loader.rs   →  UserSettings  →  revset resolution    ❌ ignored
```

This is not a crash, and it does not block the secondary-workspace work that
issue 001 delivered. It leaves the configuration work half-consumed.

---

## Why this matters

Issue 001 established this invariant:

```text
same repository
    ⇒ same repository-level configuration
```

The invariant now holds for authoring identity. It does not hold for revset
resolution. A repository owner can define `[revset-aliases]` for the repository.
Every Jujutsu command-line user in that repository gets those aliases. A Pyjutsu
program in the same repository gets none of them.

Pyjutsu states that it provides faithful Jujutsu primitives. A revset that works
in the pinned command-line interface (CLI) must work in Pyjutsu. Today three
classes of revset behave differently.

---

# Current behaviour

## 1. The alias map is always empty

`src/revset.rs:40`:

```rust
let aliases = RevsetAliasesMap::new();
```

`src/revset.rs:53-63` passes that empty map into the parse context:

```rust
let ctx = RevsetParseContext {
    aliases_map: &aliases,
    local_variables: HashMap::new(),
    user_email,
    date_pattern_context: chrono::Local::now().into(),
    default_ignored_remote: Some("git".as_ref()),
    fileset_aliases_map: &fileset_aliases,
    use_glob_by_default: false,
    extensions: &extensions,
    workspace: Some(ws_ctx),
};
```

The function signature at `src/revset.rs:33-39` accepts no settings:

```rust
fn evaluate_revset<'a>(
    repo: &'a dyn Repo,
    revset_str: &str,
    workspace_name: &WorkspaceName,
    workspace_root: &Path,
    user_email: &str,
) -> Result<Box<dyn Revset + 'a>, PyErr>
```

`user_email` is the only value that travels from the resolved settings into
revset parsing. `src/workspace.rs:512` extracts it:

```rust
let user_email = settings.user_email().to_owned();
```

A repository-level or user-level `[revset-aliases]` table therefore has no
effect in Pyjutsu.

### Every revset entry point is affected

All revset reads funnel through `evaluate_revset`:

```text
src/repo_view.rs:64    resolve_single()   → resolve(), diff_stat(), and every
                                            single-revision read
src/repo_view.rs:83    eval_to_data()     → log()
src/repo_view.rs:215   conflicts()
src/repo_view.rs:277   log_stream()
src/transaction.rs:121 resolve_single()   → every mutation that takes a revset
src/transaction.rs:144 branch_roots()
src/workspace.rs:1209  add_workspace(revisions=...)
src/workspace.rs:1852  create_tag(target=...)
```

`add_workspace(revisions=...)` is the newest entry point. Issue 001 added it.

---

## 2. The jj-cli default aliases are unavailable

Jujutsu ships its most common revset functions as default aliases in the
command-line crate, not in `jj-lib`. Pyjutsu does not depend on `jj-cli`, so
Pyjutsu ships none of them.

Confirmed missing at tag `v0.42.0`:

```text
trunk()
builtin_immutable_heads()
immutable_heads()
immutable()
mutable()
visible()
hidden()
```

Upstream defines them in `cli/src/config/revsets.toml`.

Verified at runtime:

```python
repo.add_workspace(path, revisions="trunk()")
# RevsetError: Function `trunk` doesn't exist
```

`trunk()` appears in the issue 001 API examples. Those examples do not run
today.

---

## 3. `use_glob_by_default` diverges from the pinned CLI

`src/revset.rs:60` sets:

```rust
use_glob_by_default: false,
```

The pinned CLI sets the opposite default. Upstream
`cli/src/config/misc.toml` at tag `v0.42.0` contains:

```toml
ui.revsets-use-glob-by-default = true
```

String patterns in revsets therefore match differently in Pyjutsu and in the
CLI. The setting is also user configurable, so a user who changes it sees no
effect in Pyjutsu.

---

# Required change

## Thread resolved settings into the parse context

Read the alias map and the glob-by-default setting from the resolved
`UserSettings`, then pass both into `RevsetParseContext`.

Conceptual flow:

```text
config_loader::resolved_workspace_settings()
        ↓
UserSettings
        ↓
revset alias map  +  ui.revsets-use-glob-by-default
        ↓
RevsetParseContext
        ↓
every revset entry point
```

Expected shape:

- extend `evaluate_revset` to accept one resolved revset configuration value
  **in place of** the bare `user_email` parameter;
- build the alias map once per loaded workspace, not once per call;
- cache that value in one place, and remove the now-redundant `user_email`
  field from `PyRepoView` and `PyWorkspace` rather than adding fields beside it;
- keep the construction in Rust, because the values come from jj-lib types.

jj-lib ships no loader that reads a configuration table into a
`RevsetAliasesMap`; jj-cli owns that policy. Use jj-lib's
`AliasesMap::insert(decl, defn, doc)` primitive so the declaration and
definition syntax stays jj-lib's, and reproduce only the small table-walking
step, as `src/config_loader.rs` already reproduces jj-cli's config-path policy.

---

## Open design decision — the jj-cli default aliases

Pyjutsu must decide explicitly what happens to `trunk()` and its siblings. This
issue does not settle the decision. It requires that the decision is made and
recorded.

### Option A — ship the defaults as Pyjutsu defaults

Copy the alias definitions from upstream `cli/src/config/revsets.toml` into a
Pyjutsu default configuration layer. User and repository configuration override
them, exactly as in the CLI.

```text
+ `trunk()` works, so CLI knowledge transfers directly
+ differential tests can compare CLI revsets one to one
− Pyjutsu carries a copy of CLI policy that must track each jj release
− the copy is not `jj-lib` behaviour, so the "thin binding" line moves
```

### Option B — document the aliases as unavailable

Keep only what `jj-lib` defines. State clearly in the documentation that
CLI-only aliases do not exist in Pyjutsu. Tell consumers to define the aliases
they need in repository configuration.

```text
+ Pyjutsu stays a strict jj-lib binding
+ no per-release copy to maintain
− `trunk()` fails, and that surprises every CLI user
− the issue 001 examples must be rewritten
```

Whichever option wins, the choice must be visible in the documentation and in a
test.

---

# Non-goals

This issue must **not**:

- add `jj-cli` as a runtime dependency;
- move revset policy or coercion from Python into Rust;
- change what a revset means, beyond matching the pinned Jujutsu 0.42
  behaviour;
- add a template or configuration language beyond revset aliases;
- rework `config_loader.rs`, which issue 001 already delivered;
- change the public `Revset` builder surface.

Rust stays a thin `jj-lib` binding layer.

---

# Test strategy

## 1. Repository alias differential

Place a `[revset-aliases]` entry in repository configuration, not in
`JJ_CONFIG`. Then compare:

```text
jj log -r <alias>          (pinned CLI)
repo.log("<alias>")        (Pyjutsu)
```

Assert the same commit set.

## 2. Alias precedence

Define the same alias name at the user layer and at the repository layer. Assert
that Pyjutsu applies the same precedence as the pinned CLI.

## 3. Secondary workspace parity

Run the alias differential from a secondary workspace. This proves that the
issue 001 configuration path and the revset path use the same settings.

## 4. Glob default

Use a string pattern that resolves differently under glob and under exact
matching. Compare Pyjutsu against the pinned CLI. Then flip
`ui.revsets-use-glob-by-default` in configuration and assert both follow.

## 5. Default alias decision

Add a test that locks in the chosen option. Under Option A, assert that
`trunk()` resolves. Under Option B, assert that `trunk()` raises `RevsetError`
with a message that names the missing function.

---

# Acceptance criteria

## Configuration consumption

- [ ] `evaluate_revset` builds its alias map from the resolved `UserSettings`.
- [ ] A repository-level `[revset-aliases]` table changes Pyjutsu revset
      resolution.
- [ ] A user-level `[revset-aliases]` table changes Pyjutsu revset resolution.
- [ ] Alias precedence matches the pinned Jujutsu 0.42 behaviour.
- [ ] `use_glob_by_default` reads `ui.revsets-use-glob-by-default` from the
      resolved settings.
- [ ] The default value matches the pinned CLI.
- [ ] Alias construction uses jj-lib's `AliasesMap::insert` primitive, not a
      hand-written parser for the declaration or definition syntax.

## Coverage

- [ ] Every revset entry point uses the configured context: `resolve`, `log`,
      `conflicts`, `diff_stat`, `log_stream`, `create_tag`, the transaction
      mutations, and `add_workspace(revisions=...)`.
- [ ] A secondary workspace resolves the same aliases as the primary workspace.
- [ ] Differential tests place the configuration at the repository layer, not in
      `JJ_CONFIG`.

## Default aliases

- [ ] The project records an explicit decision for the jj-cli default aliases.
- [ ] The documentation states which CLI aliases work and which do not.
- [ ] A test locks in the decision.
- [ ] The issue 001 API examples that use `trunk()` match the decision.

## Architecture

- [ ] `jj-cli` is not a runtime dependency.
- [ ] Rust remains a thin jj-lib binding layer.
- [ ] `_pyjutsu.pyi` stays synchronized with any native surface change.
- [ ] `run_jj()` remains only the generic escape hatch.

---

# Primary source references

## Pyjutsu

```text
src/revset.rs          evaluate_revset, lines 33-74
src/config_loader.rs   resolved_workspace_settings (issue 001)
src/workspace.rs:512   the only settings value that reaches revsets
src/repo_view.rs       resolve, log, conflicts, diff_stat, log_stream
src/transaction.rs     resolve_single, branch_roots
```

## Jujutsu 0.42.0

Default revset aliases:

https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/config/revsets.toml

Default `ui.revsets-use-glob-by-default`:

https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/config/misc.toml

Configuration loading:

https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/config.rs

Pyjutsu binds jj-lib 0.42.0, so implementation and tests must follow the 0.42
behaviour.

---

# Definition of done

A repository owner writes an alias in repository configuration:

```toml
[revset-aliases]
"candidates()" = "descendants(trunk()) & mine()"
```

Both of these then resolve the same commit set:

```bash
jj log -r 'candidates()'
```

```python
repo.log("candidates()")
```

The same holds from a secondary workspace. String patterns match the CLI
default. The project has recorded, documented, and tested its decision about the
jj-cli default aliases.