# Research report — revset configuration fidelity

Date: 2026-08-24

## Target

Make Pyjutsu consume resolved revset settings and match Jujutsu 0.42.0.
Keep `jj-lib` and the differential CLI at 0.42.0.
Do not add `jj-cli` as a dependency.

## Environment and baseline

- Pyjutsu commit: `d7484a44a471181eb495874a7364e321a1787ce0`
- Jujutsu: `jj 0.42.0`
- Rust: `rustc 1.94.1`
- Focused command: `.devenv/state/venv/bin/python -m pytest -q tests/test_revset_builder.py tests/test_transaction.py`
- Result: 13 tests passed.
- Evidence: [`artifacts/20260824T000000Z-revset-config-baseline/`](artifacts/20260824T000000Z-revset-config-baseline/)

## Local evidence

`src/revset.rs` created an empty `RevsetAliasesMap` for every evaluation.
It also set `use_glob_by_default` to `false`.
The only settings value in the parse context was a cached user email string.

`Repo::base_repo().settings()` returns the resolved `UserSettings` for both
`ReadonlyRepo` and `MutableRepo` in jj-lib 0.42.0.
The settings therefore match the identity resolved by `src/config_loader.rs`
for primary workspaces, secondary workspaces, historical views, and transactions.

`RevsetAliasesMap` is `AliasesMap<RevsetAliasParser, String>`.
Its public `insert()` method validates alias declarations and stores definitions
without reimplementing jj's parser.
jj-lib has no public loader from a configuration table to that map.

## Upstream evidence

- [jj 0.42 revset defaults](https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/config/revsets.toml)
  defines the seven CLI alias defaults, including the multiline `trunk()` fallback.
- [jj 0.42 miscellaneous defaults](https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/config/misc.toml)
  sets `ui.revsets-use-glob-by-default = true`.
- [jj 0.42 revset utility](https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/revset_util.rs)
  loads aliases from configuration and builds immutable expressions from `immutable_heads()`.
- [jj 0.42 CLI utility](https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/cli_util.rs)
  checks immutable commits before rewrite commands.

The pinned CLI confirms the effective glob default and all seven aliases with
`jj config list --include-defaults`.

## Chosen implementation

Create one Rust `RevsetConfig` from resolved settings during `Workspace.load()`.
It owns the alias map, glob flag, and user email.
Store it once in `Arc<RevsetConfig>` on `PyWorkspace`.
Views and transactions clone that `Arc` instead of carrying user email fields.

Walk `revset-aliases` with `StackedConfig::table_keys()` and read each merged
value through `StackedConfig::get()`.
Insert valid entries with jj-lib's `AliasesMap::insert()`.
Emit a Python warning for a malformed declaration or non-string value and
continue loading the workspace.

Missing glob configuration falls back to jj 0.42's `true` default.
An explicit resolved setting overrides that default.

The vendored `src/config/revsets.toml` is loaded at `ConfigSource::Default`, below every resolved
user, repository, and workspace layer. It contains only jj 0.42's `[revset-aliases]` table, not
CLI command defaults. `tests/test_revset_config.py` uses `jj config list --include-defaults` from
the pinned binary as the offline staleness oracle.

For rewrite safety, Pyjutsu evaluates `immutable_heads().ancestors()` before `describe`, `edit`,
`abandon`, `rebase`, `squash`, `restore`, and `split`. The parsed expression is lazy and cached on
the workspace's shared `RevsetConfig`; a transaction that only moves refs or does no rewrite pays
nothing. The root commit is checked first and remains immutable even when
`transaction(ignore_immutable=True)` bypasses configured immutability. Bookmark and tag operations
are deliberately out of scope because, like their pinned CLI counterparts, they move refs rather
than rewrite commits.

This project releases as Pyjutsu 0.16.0. The documented behavior changes are jj's `true` glob
default and configured immutability enforcement; both are enabled by default, with the scoped
transaction escape hatch for deliberate administrative rewrites.

## Rejected approaches

Adding `jj-cli` would add a runtime dependency for a small public jj-lib
adapter. It is not needed.

Rebuilding the alias declaration parser would duplicate jj-lib syntax. The
public `insert()` method already owns that syntax.

Caching individual values beside user email would create parallel state and
would leave the eight revset entry points vulnerable to drift.

## Evidence limits

The focused baseline proves only the old test surface before the change.
It does not prove configuration precedence, default aliases, or immutability.
The subsequent strand tests and full gates must provide that evidence.

## Final verification

- `devenv tasks run pyjutsu:build` rebuilt and installed Pyjutsu 0.16.0.
- `devenv tasks run pyjutsu:test` passed; its visible underlying run also passed the complete
  pytest suite and all 7 Rust unit tests.
- `devenv tasks run pyjutsu:lint` passed (`ruff` and `cargo clippy -D warnings`).
- `scripts/verify_secondary_workspaces.py /tmp/pyjutsu-live` passed against `jj 0.42.0` after
  creating a clean live workspace root.
