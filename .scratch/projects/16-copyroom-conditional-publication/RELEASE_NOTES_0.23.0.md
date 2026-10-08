# pyjutsu 0.23.0 release notes

Guarded publication for CopyRoom. Nothing existing changes behaviour. jj-lib stays at 0.44.0.

## New

- `Workspace.publish_if(expected_wc_commit, onto, description) -> PublishResult`. It publishes a
  new empty working-copy commit on `onto` only if `@` is still `expected_wc_commit` and no other
  writer moved the operation heads. A stale call raises `StalePublishError` before it moves `@`
  and before it writes any file.
- `Workspace.recover()` finishes a publication that stopped after its operation landed.
- `PublishError`, `StalePublishError`, `PublishIncompleteError`, `PublishResult`.
- `pyjutsu publish-if --repo PATH --expect-wc FULL_COMMIT_ID --onto FULL_COMMIT_ID -m DESCRIPTION`
  and `pyjutsu recover --repo PATH`. `python -m pyjutsu` runs the same commands. The wheel installs
  a `pyjutsu` executable.
- Output: `key=value` lines, first line `result=published|stale|incomplete|error|recovered`.
  Exit codes: 0 ok, 1 stale, 2 infrastructure failure or landed-but-incomplete, 3 usage.
- `LICENSE` (MIT).

## Contract

Read `docs/PUBLISH_IF.md`. In short: under writers that use jj's locks, a stale call is rejected
before `@` moves and before a writer's bytes are overwritten. The call cannot stop:

1. a direct write to a path the checkout rewrites, during the checkout;
2. a writer that loaded the old head and publishes later (the log forks; the next jj command
   merges it; no commit or byte is lost, the late writer's `@` move is);
3. power loss (unproved);
4. raw `git` commands (they bypass jj's Git lock).

`result=incomplete` (exit 2) means the operation landed or may have landed. Run
`pyjutsu recover --repo PATH`.

In a colocated repository the usual result is two operations: the publication and
`sync colocated git`.

## Internal

- Cargo feature `test-hooks` (dev build only) adds test barriers. The wheel has none and the wheel
  smoke check asserts it.
- `SnapshotInputs` is shared by `snapshot()` and `publish_if`. `Workspace.load` errors now carry
  their cause chain (for example, the unsupported store type).

## Step 4 inputs (Vendomat and nix-meta)

- Tag: `v0.23.0`, to be created by `gitman release` after the lane lands. Not created yet.
- Wheel: `pyjutsu-0.23.0-cp313-abi3-manylinux_2_39_x86_64.whl`, sha256
  `7bac5f7dee9d7cef16b52014eb96b7e6a9d117d459a77a6bff6771404ee3b085` (local build from this lane;
  Vendomat builds the shipping wheel with `nix build .#pyjutsu-wheel`, so its hash will differ).
- Entry point to resolve by absolute path: `<env>/bin/pyjutsu` (console script
  `pyjutsu = pyjutsu.cli:main`).
- Capability check for CopyRoom: `pyjutsu --help` lists `publish-if`; `pyjutsu.__version__ >= 0.23.0`.
- Rollback: keep the 0.22.0 tag; 0.22.0 has no `publish-if`.
