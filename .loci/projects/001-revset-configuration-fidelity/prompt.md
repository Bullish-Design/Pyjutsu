# Implementation prompt

Work in the Pyjutsu repository. Implement the complete project in
`.loci/projects/001-revset-configuration-fidelity/project.md` and the gap recorded in
`.loci/issues/003-revset-ignores-configuration/issue.md`.

Use the `build-run-investigation-loop` skill for this task. Read its full instructions before you
change code. If that skill is not in your available-skills list, do not stall and do not invent it —
proceed with the evidence-first workflow this prompt already specifies, and say in your final report
that the skill was unavailable. Also read `AGENTS.md` and the full `.agents/skills/my-ai/SKILL.md`
file. Follow the repository's manager routing, devenv, verification, commit, and push rules.

Keep Pyjutsu on jj-lib 0.42.0 for this project. Do not combine this work with the 0.44 upgrade
recorded in `.loci/issues/002-jj-lib-0-44-upgrade-investigation/issue.md`.

## Objective

Make revset evaluation consume the configuration Pyjutsu already loads, then give Pyjutsu jj's
configurable immutability.

Deliver three strands, in this order:

1. **Plumbing.** Build the revset alias map and the glob-by-default flag from the resolved
   `UserSettings`. Pass both into `RevsetParseContext`.
2. **Default aliases.** Ship jj's own alias definitions as a Pyjutsu default configuration layer.
3. **Immutability.** Enforce the configurable `immutable_heads()` set on every rewrite verb.

Strand 1 blocks strands 2 and 3. Land each strand as its own verified commit.

## Decisions already made

Do not re-open these. Record them in the research report and implement them.

- **Ship jj's default aliases.** The alternative was to document them as unavailable. The user chose
  to ship them. Load them at `ConfigSource::Default`, which is the lowest precedence rank, so user,
  repository, and workspace configuration still override them exactly as in the pinned CLI.
- **Do not add `jj-cli` as a runtime dependency.** Every capability below composes from public
  jj-lib APIs. Verify that claim before you write code; if you find a case that genuinely cannot,
  stop and report it rather than adding the crate.
- **Enforce immutability.** Pyjutsu protects only the root commit today. That is a real gap against
  the pinned CLI, not a deliberate scope line, once the aliases resolve.
- **Release framing: minor bump to 0.16.0, both breaks on by default.** This project makes two
  silent behaviour changes for existing callers: the glob default flips (strand 1) and immutability
  enforcement turns on (strand 3). Neither lands behind a compatibility flag. Bump the version in
  `Cargo.toml` and `pyproject.toml` to `0.16.0`, and document both breaks together.
- **Escape hatch: `transaction(ignore_immutable=True)`.** Pyjutsu's equivalent of the CLI's
  `--ignore-immutable` is a keyword argument on `transaction()`, scoped to one unit of work. It
  defaults to `False`. It does **not** lift the root-commit guard.

## Start with evidence

Before implementation:

1. Read `.loci/issues/003-revset-ignores-configuration/issue.md` completely.
2. Read `.loci/issues/001-secondary-workspaces-first-class/issue.md`, section "Required change B",
   and its `RESEARCH_REPORT.md`. That work built `src/config_loader.rs`, which this project extends.
3. Inspect the current Rust: `src/revset.rs`, `src/config_loader.rs`, `src/transaction.rs`,
   `src/repo_view.rs`, `src/workspace.rs`.
4. Inspect the exact jj-lib 0.42.0 source in the Cargo registry.
5. Inspect the pinned `jj` 0.42.0 CLI source for the configuration and immutability behaviour.
6. Run the current focused revset and transaction tests. Preserve their output.
7. Write the dated research report the investigation skill requires.

Use upstream Jujutsu source and documentation as primary sources. Do not infer CLI behaviour from
memory. The pinned CLI is on `PATH` inside `devenv shell`; use it as the differential oracle.

### Verified anchors

These were confirmed against the working tree at commit `d7484a4` and against jj-lib 0.42.0 in the
Cargo registry. Re-verify each one; line numbers move.

Pyjutsu:

```text
src/revset.rs:33-74     evaluate_revset — builds RevsetParseContext
src/revset.rs:40        let aliases = RevsetAliasesMap::new();      # always empty
src/revset.rs:60        use_glob_by_default: false,                 # CLI default is true
src/revset.rs:79        evaluate      — collects Commits
src/revset.rs:100       evaluate_ids  — collects CommitIds
```

`user_email` is the only value that travels from the resolved settings into revset parsing.
`src/workspace.rs` extracts it with `settings.user_email().to_owned()` and carries it on
`PyWorkspace`; `PyRepoView::new` takes it as its fourth argument.

Eight call sites reach `evaluate` / `evaluate_ids`:

```text
src/repo_view.rs:65     resolve_single  -> resolve(), diff_stat(), every single-revision read
src/repo_view.rs:91     eval_to_data    -> log()
src/repo_view.rs:218    conflicts()
src/repo_view.rs:284    log_stream()
src/transaction.rs:122  resolve_single  -> every mutation that takes a revset
src/transaction.rs:156  branch_roots()
src/workspace.rs:1209   add_workspace(revisions=...)
src/workspace.rs:1852   create_tag(target=...)
```

jj-lib 0.42.0, all public:

```text
revset::RevsetAliasesMap             re-exported from revset_parser (revset.rs:70)
                                     = AliasesMap<RevsetAliasParser, String> (revset_parser.rs:678)
dsl_util::AliasesMap::insert(decl, defn, doc)   (dsl_util.rs:598)
```

`AliasesMap::insert` is the primitive you need. jj-lib ships **no** loader that reads a config table
into a `RevsetAliasesMap`; jj-cli has `revset_util::load_revset_aliases`. Reproduce that small policy
in Pyjutsu, the same way `src/config_loader.rs` already reproduces jj-cli's config-path policy.

jj 0.42.0 CLI, for reference only — do not link the crate:

```text
cli/src/config/revsets.toml          the seven default aliases
cli/src/config/misc.toml             ui.revsets-use-glob-by-default = true
cli/src/cli_util.rs:873              revsets_use_glob_by_default: settings.get("ui.revsets-use-glob-by-default")?
cli/src/revset_util.rs               load_revset_aliases, parse_immutable_heads_expression
cli/src/cli_util.rs:946-1090         immutable_expression, immutable_heads_expression, find_immutable_commit
cli/src/cli_util.rs:1935-1990        check_rewritable, check_rewritable_expr
```

Primary sources:

- https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/config/revsets.toml
- https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/config/misc.toml
- https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/revset_util.rs
- https://github.com/jj-vcs/jj/blob/v0.42.0/cli/src/cli_util.rs

---

## Strand 1 — plumbing

Thread the resolved configuration into the revset parse context.

Read from the resolved `UserSettings`:

- the `revset-aliases` table, built into a `RevsetAliasesMap` with `AliasesMap::insert`;
- `ui.revsets-use-glob-by-default`, replacing the hardcoded `false`.

### The threading design — replace `user_email`, do not sit beside it

Introduce one struct — call it `RevsetConfig` — that owns the alias map, the glob flag, and the
user email. `evaluate_revset` takes that single value **in place of** the bare `user_email`
parameter. Do not add the alias map and the glob flag as two more parameters alongside
`user_email`. That would grow a 4-argument constructor to 6 across 8 call sites and create a second
source of truth that can drift.

Net parameter count must go **down**, not up.

The resolved `UserSettings` is already reachable from `repo`. Verified against jj-lib 0.42.0:

```text
Repo::base_repo() -> &ReadonlyRepo        on the trait evaluate_revset already takes
ReadonlyRepo::settings() -> &UserSettings  repo.rs:329
impl Repo for MutableRepo                  repo.rs:2056-2059, returns the base repo
```

`PyWorkspace::load` passes the resolved settings into `resolved.loader.load(&settings, ...)`
(`src/workspace.rs:515-518`), and every `PyRepoView` is built from `ws.repo_loader()`
(`src/workspace.rs:545`, `src/workspace.rs:572`). So `repo.base_repo().settings()` inside
`evaluate_revset` yields exactly the issue-001 resolved settings — in head views, in historical
views, and inside a transaction. Re-verify this chain yourself before you rely on it.

Two consequences:

1. `user_email: String` on `PyWorkspace` (`src/workspace.rs:407`) and `PyRepoView`
   (`src/repo_view.rs:42`) is already a redundant cached copy. Removing it is in scope for this
   strand.
2. Strand 3 needs the immutable expression at every rewrite verb. Put it on the same `RevsetConfig`
   so strand 3 adds no new plumbing.

Build the alias map **once per loaded workspace**, not once per call; a revset read must not
re-parse every alias. Cache it in one place — an `Arc<RevsetConfig>` built at `Workspace::load`, or
a memo derived from `repo` — not as scattered fields on two Python-facing structs.

Put the construction in Rust. The values come from jj-lib types, and Python owns no policy here.

Handle a malformed alias the way the CLI does: report it and continue rather than failing every
revset in the repository. A bad `revset-aliases` entry must not make the workspace unloadable.
Decide where that diagnostic surfaces — a Python warning matches how `Workspace.load` already
delivers secure-config warnings — and document the choice.

### The glob default is a breaking change

Note the divergence risk in `use_glob_by_default`: a missing alias raises a visible error, but a
wrong glob default silently changes which commits a pattern **matches**. Cover it with a test that
would fail under the old hardcoded value.

This strand is a correctness fix in intent, but flipping the glob default from `false` to `true` is
a behaviour break for every existing caller whose revset contains a string pattern. There is no
error and no warning — only a different result set. Treat it as a release event, not a detail:
record it in the same release note as strand 3's enforcement break, under the 0.16.0 bump.

---

## Strand 2 — default aliases

Vendor jj's alias definitions as a Pyjutsu default configuration layer.

Copy the `[revset-aliases]` table from the pinned `cli/src/config/revsets.toml` into Pyjutsu's own
source tree. Load it at `ConfigSource::Default` in `src/config_loader.rs::base_config`, beside the
existing `StackedConfig::with_defaults()`.

The seven aliases are `trunk()`, `builtin_immutable_heads()`, `immutable_heads()`, `immutable()`,
`mutable()`, `visible()`, and `hidden()`.

`trunk()` is **not** a one-line definition. It is a multi-line `'''` block holding a fallback chain
across the `main`, `master`, and `trunk` bookmarks on the `origin` and `upstream` remotes. Copy it
literally. Do not flatten or re-format it.

Copy only `[revset-aliases]`. Do **not** copy the `[revsets]` table from the same file. That table
sets which revset each CLI command defaults to, and Pyjutsu has no default-command concept.

Every function these aliases compose is already a jj-lib builtin — `visible_heads`, `tags`,
`untracked_remote_bookmarks`, `remote_bookmarks`, `latest`, `reachable`, `present`. Verify that
before you vendor. Vendoring must add names, not capability.

Because this is a copy and not a dependency, it goes stale. Add a re-sync step to
`.loci/issues/002-jj-lib-0-44-upgrade-investigation/issue.md` through the loci CLI: every jj upgrade
must re-diff the vendored file against upstream. Record the upstream tag the copy came from, in the
file itself.

### The staleness oracle — use the pinned CLI, not the network

Do not write a test that fetches upstream `revsets.toml`. The test environment has no guaranteed
network, and a vendored jj source tree is not available.

Use the pinned CLI already on `PATH` inside `devenv shell`. It prints its own effective defaults:

```bash
jj config list --include-defaults
```

At jj 0.42.0 that emits `ui.revsets-use-glob-by-default = true` and all seven
`revset-aliases."..."` entries. This oracle is offline, version-pinned, and compares the
**effective** value rather than file text — so it also catches a precedence mistake in the vendored
layer, not only a stale copy. Use it for the strand 2 staleness test and for the strand 1 glob
default test.

Confirm precedence with a test: a repository-level redefinition of `trunk()` must beat the vendored
default, exactly as in the CLI.

---

## Strand 3 — immutability

Enforce jj's configurable immutable set on the rewrite verbs.

Current state: `src/transaction.rs` guards **only** the root commit, in `abandon`, `rebase`,
`squash`, `restore`, and `split`. The comment above `abandon` states that `immutable_heads()` is CLI
workflow policy the thin layer does not replicate. That comment becomes wrong with this strand;
update it.

Follow the CLI's shape:

```text
immutable_heads_expression   parsed from revset-aliases.immutable_heads()
immutable_expression         = immutable_heads_expression.ancestors()
check before rewriting       find the first commit in the rewrite set that is immutable
error                        name the commit, and say how to proceed
```

### Build the expression once per transaction

Parse and build the immutable expression **once per transaction**, not once per verb call. The CLI
builds it once per command; a transaction is Pyjutsu's analogue of a command. A loop of 500
`squash` calls inside one transaction must not re-parse the expression 500 times.

Put it on the `RevsetConfig` value that strand 1 introduces, so this strand adds no new plumbing.
Build it lazily: a transaction that rewrites nothing must not pay for it.

Apply the check to every verb that rewrites or abandons an existing commit. At minimum: `describe`,
`abandon`, `rebase`, `squash`, `restore`, `split`.

`edit` is also in scope. It does not rewrite a commit by itself, but the pinned CLI still guards it —
`cli/src/commands/edit.rs:63` calls `check_rewritable` — because making a commit the working copy
means the next snapshot rewrites it. Match that.

Enumerate the current surface yourself rather than trusting the list above. `set_bookmark` and
`create_tag` move refs and do not rewrite commits; decide explicitly whether they are in or out, and
record the reason. Check each against the pinned CLI's own command source before you decide.

Keep the existing root-commit guard. `record_abandoned_commit` asserts on the root and would panic
through PyO3, so the explicit guard must stay even once the general check exists.

### Design decisions this strand must make and record

The release framing and the escape hatch are already settled above, under "Decisions already made".
Implement them; do not re-open them.

1. **Default protected set.** Vendoring gives `immutable_heads()` a real definition, so enforcement
   turns on by default. State that plainly.
2. **Error type.** `ImmutableCommitError` already exists and subclasses `PyjutsuError`. Reuse it. The
   message must name the commit and point at the configuration, as the CLI's does. Where the CLI
   suggests `--ignore-immutable`, Pyjutsu's message names `transaction(ignore_immutable=True)`.
3. **Cost.** The check evaluates a revset before each rewrite. Measure it against the per-transaction
   caching above. If a hot path regresses, say so with numbers rather than removing the check.

---

## Required tests

Place configuration at the **repository layer** through the pinned CLI. Do not place the value under
test in `JJ_CONFIG`. `tests/test_workspace_config.py` shows the established pattern, including the
`XDG_CONFIG_HOME` monkeypatch and `jj config set --repo`.

Strand 1:

- a repository-level `[revset-aliases]` entry changes Pyjutsu revset resolution;
- a user-level entry does the same;
- repository beats user, matching pinned CLI precedence;
- the same alias resolves from a secondary workspace, proving the issue-001 configuration path and
  the revset path share one settings object;
- `ui.revsets-use-glob-by-default` is honoured, and its default matches the CLI — use a pattern that
  resolves differently under glob and exact matching, and assert against the CLI both ways;
- a malformed alias produces the documented diagnostic and does not break unrelated revsets;
- every entry point uses the configured context: `resolve`, `log`, `iter_log`, `conflicts`,
  `diff_stat`, `create_tag`, the transaction mutations, and `add_workspace(revisions=...)`.

Strand 2:

- `trunk()`, `immutable_heads()`, `immutable()`, `mutable()`, `visible()`, and `hidden()` all
  resolve, and each matches `jj log -r` against the same repository;
- a repository redefinition of `trunk()` overrides the vendored default;
- the vendored table and upstream `revsets.toml` agree — a test that reads both, so a stale copy
  fails the suite rather than drifting silently.

Strand 3:

- rewriting a commit inside `immutable()` raises `ImmutableCommitError`, and the pinned CLI refuses
  the equivalent command;
- rewriting a mutable commit still succeeds;
- a repository-level redefinition of `immutable_heads()` widens and narrows the protected set, and
  Pyjutsu tracks it;
- the root commit stays protected regardless of configuration;
- `transaction(ignore_immutable=True)` permits the rewrite, and still refuses the root commit;
- `transaction()` without the argument refuses it, proving the default is `False`;
- every guarded verb is covered.

Use differential tests against the pinned CLI as the main correctness contract. Control identity,
timestamps, and randomness where exact commit ids are part of an assertion.

Extend `scripts/verify_secondary_workspaces.py`, or add a sibling live script, if a contract is
easier to prove against real on-disk repositories. Tree-id and topology parity belong in the pytest
gate, not only in a manual script — `.loci/issues/001-.../RESEARCH_REPORT.md` records why.

---

## Documentation and cleanup

Update `README.md`, `docs/USER_GUIDE.md`, `docs/DEV_GUIDE.md`, and `docs/PYJUTSU_CONCEPT.md`.

Remove or rewrite the "No CLI revset aliases" note in `docs/USER_GUIDE.md` §1 and its shorter
counterpart at the end of the README "Revset builder" section. Both state that `trunk()`,
`immutable_heads()`, `mutable()`, `visible()`, and `hidden()` do not exist, and that repository
`[revset-aliases]` configuration has no effect. Strands 1 and 2 make both statements false.

Restore `trunk()` to the examples that previously used it, where it reads better than the bookmark
name that replaced it. Commit `445f107` swapped roughly twelve examples across the three files from
`trunk()` to `"main"` precisely because `trunk()` did not resolve.

Document the immutable set: what is protected by default, how to redefine it, what the error
means, and `transaction(ignore_immutable=True)`. Document the vendored alias layer and its
precedence.

Bump the version to `0.16.0` in both `Cargo.toml` and `pyproject.toml`. Write one release note that
covers **both** behaviour breaks together — the glob default flip from strand 1 and the immutability
enforcement from strand 3 — and says what an existing caller must check. The repository has no
`CHANGELOG.md`; put the note where this project's documentation set already keeps user-facing
change information, and say where you put it.

Keep the note that each explicit `add_workspace` revset must resolve to exactly one commit. That is a
deliberate divergence from `jj workspace add -r 'A|B'` and is unrelated to this project.

Do not add candidate, work-package, agent, scheduling, cleanup, or integration policy to Pyjutsu.

---

## Non-goals

- Do not add `jj-cli` as a runtime dependency.
- Do not copy the `[revsets]` table, template defaults, colors, hints, or merge-tool defaults.
- Do not move revset coercion from Python into Rust.
- Do not change what a revset means beyond matching pinned Jujutsu 0.42 behaviour.
- Do not rework `src/config_loader.rs`; issue 001 delivered it.
- Do not change the public `Revset` builder surface.
- Do not begin the jj-lib 0.44 upgrade.

Rust stays a thin jj-lib binding layer. Python owns public coercion and default ergonomics.
`_pyjutsu.pyi` stays synchronized with any native surface change. `run_jj()` remains only the generic
escape hatch.

---

## Verification and delivery

Run focused tests after each slice. Then run the repository's complete gates inside devenv:

```bash
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint
devenv shell -- bash -c '.devenv/state/venv/bin/python scripts/verify_secondary_workspaces.py /tmp/pyjutsu-live'
```

`devenv tasks run` suppresses inner stdout. Run each task's exec line directly inside `devenv shell`
when you need the pass or fail detail.

Baseline at the start of this project, measured at commit `d7484a4`: 364 pytest tests pass, 7 cargo
tests pass, ruff is clean, clippy is clean, and the live script passes 43 assertions. Report the real
numbers you observe. If a gate fails, say so with its output. Never describe a gate as passing when
you did not run it.

### Commit and push cadence

Do not save the whole project for one commit at the end.

- Run the full gate at the end of **each strand**. When it is green, commit that strand and push to
  `origin` on the current branch immediately.
- Land at least one commit per strand. Split further when a strand has a natural internal boundary —
  for example, strand 1's `RevsetConfig` introduction and the `user_email` removal may be two
  commits.
- Never commit on a red gate. If a gate fails, fix it or report the blocker; do not commit around it.
- Inspect the diff and `git status` before each commit. Include all relevant active changes.

### Merge to main when complete

After the final strand is committed, pushed, and green:

1. Confirm the full gate passes on the final state of the branch.
2. Merge the branch into `main`.
3. Push `main` to `origin`.
4. Report the merge commit and the pushed state.

Do not merge a branch whose gate is red, and do not merge with strands left unfinished. If a strand
is blocked, stop before the merge, push what is green, and report the blocker.

Close `.loci/issues/003-revset-ignores-configuration/issue.md` through the loci CLI when strands 1
and 2 are delivered. Use `documents/set_status` with `status=done`; the vault defines no status
vocabulary, and `done` is this user's convention for a delivered record. Do not hand-edit
loci-owned frontmatter.

If strand 3 grows beyond this project, record it as its own issue through the loci CLI rather than
letting it expand silently.

In the final report, include:

- what each strand changed, by file;
- the `RevsetConfig` shape, its lifecycle, and where it is cached;
- confirmation that `user_email` threading was removed, not widened;
- the precedence you verified against the pinned CLI;
- the immutability design decisions you made, with the reasoning;
- both behaviour breaks, and where the 0.16.0 release note lives;
- tests added and gate results;
- every commit and its push, plus the merge commit on `main`;
- any follow-up issue created.

Do not stop after analysis. Continue until the complete project is implemented, verified, committed,
pushed, and merged to `main`, or until a concrete external blocker requires user action.

The `.loci/projects/001-revset-configuration-fidelity/` directory is untracked at the start of this
project. Commit it with the first strand.
