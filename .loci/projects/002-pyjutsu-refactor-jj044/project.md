---
title: Pyjutsu refactor and jj-lib 0.44 upgrade
type: project
status: active
loci:
  schema: 1
  id: 73e04d8e-9b53-47a1-ba77-0bd81b54d558
  projects: []
---

# PROJECT: Pyjutsu refactor and jj-lib 0.44 upgrade

Refactor the native boundary, upgrade jj-lib to 0.44.0, add SHA-256 and native
tag support, and produce Pyjutsu 0.17.0 binary wheels.

Follow the uploaded `Pyjutsu Refactor + jj-lib 0.44 Upgrade Implementation
Guide` in phase order. Keep each checkpoint green. Related issue:
[[.loci/issues/002-jj-lib-0-44-upgrade-investigation/issue.md]].

## Implementation log

### 2026-08-25 — Phase 0 baseline

The implementation lane is `jj044-refactor`. A remote refresh confirmed that
local `main` and `origin/main` both point to
`0aa6dfc10cbc2afdbb4d4a0cfdcdc942fcc74ba0`.

The only other Git worktree is Paseo's `hesitant-anaconda` worktree. It is
clean and points to `7570030cebb35ba0b759ec6f9757d374bfba7f24`.
That revision is an ancestor merged by the baseline commit. No unmerged jj-lib
0.44 implementation exists in any branch or Paseo worktree.

Baseline versions:

```text
Pyjutsu          0.16.0
JJ_VERSION       0.42.0
JJ_LIB_TARGET    0.42.0
external jj      0.42.0
rustc            1.94.1 (e408947bf 2026-03-25)
cargo            1.94.0 (29ea6fb6a 2026-03-24)
jj-lib           0.42.0
gix              0.84.0 (one resolved version)
baseline commit  0aa6dfc10cbc2afdbb4d4a0cfdcdc942fcc74ba0
```

Baseline validation:

```text
devenv tasks run pyjutsu:build  FAIL
devenv tasks run pyjutsu:lint   PASS
devenv tasks run pyjutsu:test   FAIL: pyjutsu:build dependency
devenv tasks run base:check     PASS
devenv tasks run base:test      FAIL: pyjutsu:build dependency
devenv test                     PASS
pytest -q                       PASS
cargo test                      PASS: 7 tests
```

The known pre-existing failure is exact and reproducible. Noninteractive
`maturin develop --uv` cannot find a virtual environment because the build
task does not set `VIRTUAL_ENV` or `UV_PROJECT_ENVIRONMENT`. No fix was present
when this baseline was recorded.

### 2026-08-25 — Phase 1 build and test reliability

`nix/pyjutsu.nix` now defines the devenv virtual environment root once. The
build task and `enterTest` pass it to Maturin through `VIRTUAL_ENV` and
`UV_PROJECT_ENVIRONMENT`. Test and lint tasks use explicit sequential command
lines. The new `pyjutsu:verify` task depends on lint and test without copying
their command bodies.

Phase 1 acceptance:

```text
devenv tasks run pyjutsu:build   PASS
devenv tasks run pyjutsu:test    PASS
devenv tasks run pyjutsu:lint    PASS
devenv tasks run pyjutsu:verify  PASS
devenv tasks run base:check      PASS
devenv tasks run base:test       PASS
devenv test                      PASS
```

Each command ran through a fresh noninteractive devenv invocation. No command
depended on an activated interactive virtual environment.

### 2026-08-25 — Phase 2 annotated-tag extraction

The first behavior-preserving Workspace extraction moved annotated Git tag
creation and push logic into `src/workspace/tags.rs`. The single
`#[pymethods]` block remains in `src/workspace.rs`. Its Python-facing methods
are thin delegates with unchanged signatures and documentation.

`src/workspace.rs` decreased from 2,311 to 2,176 lines. The new tag module is
209 lines. This staged layout lets later extractions use
`src/workspace/<responsibility>.rs` before the final `mod.rs` conversion.

Validation:

```text
cargo check                              PASS
cargo fmt --check                        PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                               PASS: 7 tests
pytest -q -n0 tests/test_tags.py          PASS: 6 tests
devenv tasks run pyjutsu:verify           PASS
```

No test expectation or public API changed.
