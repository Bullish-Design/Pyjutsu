# pyjutsu 0.24.0 release notes

Port to jj-lib 0.46.0, a fix for the per-workspace Git HEAD, and Rust 1.97.1 as the minimum
compiler.

## Changed

- **jj-lib 0.46.0** (was 0.44.0). `JJ_VERSION` and `JJ_LIB_TARGET` read `0.46.0`.
- **Rust 1.97.1** is the minimum compiler (`rust-version` in `Cargo.toml`). jj-lib 0.46.0 needs it.
  The wheel and the Nix package are unaffected. A source build (the sdist) needs the new compiler.
- The dependencies that mirror jj-cli's lock move with it: `gix` 0.88.0, `rand` 0.10.3,
  `whoami` 2.1.3. `dunce`, `etcetera`, and `rand_chacha` are unchanged.
- The vendored default `immutable_heads()` follows jj 0.46: it adds `untracked_remote_tags()`. A
  commit that an untracked remote tag names is now immutable. Configuration that overrides
  `immutable_heads()` is not affected.

## Fixed

- **Per-workspace Git HEAD.** jj 0.45 and later stores one Git `HEAD` per workspace (`git_heads` in
  the operation-store view). A colocated child workspace is a Git worktree with its own `HEAD`.
  `publish_if` decided "colocated" by comparing the main repository's work directory with the
  workspace root, so it skipped the Git sync in a child workspace. The next jj command there
  printed "Reset the working copy parent to the new Git HEAD" and moved `@`.
  Now `publish_if`, `recover`, `sync_colocated`, `git_export`, and `git_import` use the jj CLI's
  own test (`GitBackend::open_git_repo_at_workdir`) and pass the workspace name and root to
  jj-lib. A publication in a child workspace resets that worktree's `HEAD` and index, and that
  workspace's `git_heads` entry. Other workspaces keep their `HEAD`.
- `sync_colocated` raises `GitError` in a workspace that has no Git work directory. Before, it ran
  against the repository's inner Git store. `git_import` and `git_export` skip the `HEAD` step in
  such a workspace, as the jj CLI does.
- `write_ref` and `delete_ref` (`ws.git`) work again with gix 0.88. Ref names that nest, such as
  `T` and `T/api`, are packed in separate transactions.

## New

- `pyjutsu --version` prints `pyjutsu 0.24.0 (jj-lib 0.46.0)` and exits 0. CopyRoom can read the
  linked jj-lib from it.
- Tests: `tests/test_publish_workspace_git_head.py` covers `publish_if`, `recover` (three crash
  points and a failed Git sync), and `sync_colocated` in a colocated child workspace and in the
  default workspace. Each test runs a plain jj 0.46 command afterward. The command must print no
  "Reset the working copy parent" message and must not move `@`.

## Internal

- The development shell pins the jj 0.46.0 release binary (`nix/jj.nix`) for the differential
  tests. The `nixpkgs-jj` input is gone.
- The development shell takes its Rust toolchain (1.98.1) from a dedicated `nixpkgs-rust` input.
  It is the nixpkgs rev that `flake.nix` pins, so the shell and the Nix package use one compiler.
  The rest of the shell stays on the rolling nixpkgs.
- API changes absorbed (see `docs/DEV_GUIDE.md` §8 for the procedure): `MergedTreeValue` moved to
  `jj_lib::backend`; `RepoPathUiConverter` moved to `jj_lib::ui_path`; `Merge::added_ids` is
  `present_adds`; `resolve_change_id` and the short-prefix lookups are async;
  `ResolvedRevset::evaluate` takes no repo; `UserSettings::from_config_and_home_dir`;
  `remove_wc_commit` is `remove_workspace`; `Store::gc` is `GitBackend::gc`; the workspace store
  comes from `RepoLoader::workspace_store`; `SimpleOpHeadsStore::NAME` is a constant.

## Release inputs (Vendomat and nix-meta)

- Version: 0.24.0 in `Cargo.toml`, `pyproject.toml`, `python/pyjutsu/__init__.py`,
  `tests/test_build.py`, `uv.lock`, and `Cargo.lock`.
- Tag `v0.24.0`: not created. The user decides when to publish.
- Wheel: `pyjutsu-0.24.0-cp313-abi3-manylinux_2_39_x86_64.whl`. Vendomat builds the shipping
  wheel (`nix build .#pyjutsu-wheel`, `vendomat publish pyjutsu`).
- Capability check for CopyRoom: `pyjutsu --version` prints `pyjutsu 0.24.0 (jj-lib 0.46.0)`.
- Rollback: keep the 0.23.0 tag. 0.23.0 binds jj-lib 0.44.0 and has the child-workspace fault.
- CopyRoom follow-up: move the wheel URL in `pyproject.toml` and `uv.lock`, and the pyjutsu ref in
  `flake.nix` and `flake.lock`, to v0.24.0.
