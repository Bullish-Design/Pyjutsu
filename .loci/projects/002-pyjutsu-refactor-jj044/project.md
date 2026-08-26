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

### 2026-08-25 — Phase 2 operation-log extraction

The stacked `jj044-refactor/operations` lane moved `undo` and
`restore_operation` implementations into `src/workspace/operations.rs`.
Python-facing wrappers remain in the single `#[pymethods]` block.

`src/workspace.rs` decreased from 2,176 to 2,107 lines. The new operation-log
module is 87 lines.

Validation:

```text
cargo check                              PASS
cargo fmt --check                        PASS
pytest -q -n0 tests/test_undo.py          PASS: 7 tests
devenv tasks run pyjutsu:verify           PASS
```

The full gate includes warning-free Clippy, all Python tests, and all Rust
tests. No test expectation or public API changed.

### 2026-08-26 — Phase 2.5 jj-lib delegation audit

#### Principle

Pyjutsu is a binding. jj-lib owns every behavior that jj-lib provides. Code
that reaches past jj-lib — to `gix`, to the filesystem, or to a subprocess —
must name the missing jj-lib API and must carry a `// jj-lib gap:` comment.

This audit runs before the dependency bump. It fixes the scope of the port and
prevents the upgrade from preserving a workaround that jj-lib 0.44 has closed.

#### Method

Evidence comes from the local crate sources, not from documentation:

```text
~/.cargo/registry/src/index.crates.io-*/jj-lib-0.42.0
~/.cargo/registry/src/index.crates.io-*/jj-lib-0.44.0
~/.cargo/registry/src/index.crates.io-*/gix-0.84.0
~/.cargo/registry/src/index.crates.io-*/gix-0.85.0
```

Measured surface, Rust source, commit `dabe76a`:

```text
jj_lib::* module imports        30
`use jj_lib` statements         92
direct `gix::` call sites       33   (workspace.rs 25, tags.rs 7, repo_view.rs 1)
direct std::fs call sites        9   (all in workspace.rs)
subprocess call sites            1   (run_jj, Python)
total Rust lines             5,539
```

#### Inventory

Class: **GAP** — jj-lib provides nothing. **DUPLICATE** — jj-lib provides an
equivalent and Pyjutsu reimplements it. **CONTRACT** — the bypass is the
documented feature. **ORIGINAL** — a Pyjutsu concept with no jj analogue.

| # | Site | Class | Evidence |
|---|---|---|---|
| 1 | `src/workspace/tags.rs` — annotated tag write via `gix` | GAP | jj-lib 0.44 only *copies* existing annotated tag refs (`git.rs:1410`). `MutableRepo::set_local_tag_target` (`repo.rs:1850`) writes lightweight tags. No annotated-tag writer exists. |
| 2 | `ensure_jj_git_excluded` — `workspace.rs:200` | GAP | The string `info/exclude` does not appear in jj-lib 0.44. jj-cli owns this policy. |
| 3 | trunk `HEAD` write — `workspace.rs:1059` | GAP | jj-lib 0.44 `git.rs` reads `HEAD` only. No `set_head`, `reset_head`, or `export_head`. |
| 4 | `apply_head_ref_packed` — `workspace.rs:288` | GAP | Packed-refs workaround for directory/file conflicts in fractal ref names. jj-lib exposes no ref-repair API. |
| 5 | `write_git_ref` / `delete_git_ref` — `workspace.rs:1928` | CONTRACT | Bypassing jj's view is the documented purpose: heal colocated drift when `git_export` is itself broken. |
| 6 | `prune_orphaned_keep_refs` — `workspace.rs:234` | **DUPLICATE** + GAP | jj-lib owns `refs/jj/keep/`. It enforces the policy in `recreate_no_gc_refs` (`git_backend.rs:851`) and exposes `Store::gc` publicly (`store.rs:254`, present since 0.42). But its `NO_GC_REF_NAMESPACE` constant is **private** in both 0.42 (`git_backend.rs:100`) and 0.44 (`git_backend.rs:99`). Pyjutsu must restate the literal. |
| 7 | `patch_id_hex` — `repo_view.rs:418` | ORIGINAL | A Pyjutsu digest, not a Git object id. See finding F2. |
| 8 | `src/config/revsets.toml` | GAP | The alias table lives in `jj-cli`, not jj-lib. Vendoring is the only option. |
| 9 | `python/pyjutsu/revset.py::_quote` | **DUPLICATE** | Hand-mirrors jj-lib `dsl_util.rs::escape_string`. Verified by hand against 0.42, with no compile-time link. |
| 10 | `python/pyjutsu/hooks.py` | ORIGINAL | Pyjutsu feature. jj has no in-process hook model. |
| 11 | `run_jj` | ORIGINAL | Documented subprocess escape hatch. |

Filesystem calls not listed above are ordinary path handling:
`workspace.rs:380` canonicalizes a stored path, and `workspace.rs:1149`/`1199`
check and create a secondary-workspace directory.

#### Findings

**F1 — Pyjutsu depends on a `gix` feature it does not declare.**

`Cargo.toml:45` declares `gix = { version = "=0.84.0", default-features = false }`
with no features. `repo_view.rs:421` calls `gix::hash::hasher(Kind::Sha1)`
directly. That compiles only because jj-lib 0.42 enables `sha1` on its own
`gix` edge and Cargo unifies features. Pyjutsu's direct call therefore relies
on a transitive dependency's choice.

This corrects the upgrade issue. The issue states that gix feature `sha1` must
be enabled "otherwise `gix-hash` 0.25 does not compile". `gix-hash` is already
0.25.1 under gix 0.84.0, and the current build already compiles. The real
requirement is different and permanent: declare `sha1` on Pyjutsu's own `gix`
edge, because Pyjutsu calls the hashing API directly.

jj-lib 0.44 adds `sha256` to its `gix` features. SHA-256 support therefore
arrives through jj-lib. Pyjutsu must still declare what it uses itself.

**F2 — `patch_id_hex` hardcodes SHA-1.**

`repo_view.rs:421` fixes the digest at `gix::hash::Kind::Sha1`. The upgrade
issue lists "hidden 20-byte object-ID assumptions" as a Medium risk but names
no site. This is one. A decision is required before SHA-256 initialization
lands:

- Keep SHA-1 always. The patch id is a Pyjutsu content digest, not a Git object
  id, so a stable width is defensible. Document it.
- Or follow the repository object hash. Then a patch id changes meaning between
  repositories.

Recommendation: keep SHA-1 and document it. Record the decision in the public
docstring so the value never reads as a Git object id.

**F3 — keep-ref pruning duplicates a jj-lib invariant.**

`prune_orphaned_keep_refs` deletes orphaned `refs/jj/keep/` entries with a raw
`gix` ref transaction, and restates jj-lib's `NO_GC_REF_NAMESPACE` literal.

Two facts limit the fix, and both were checked in the crate sources:

- `Store::gc` is public but is not a drop-in replacement. It runs a full
  backend collection. Pyjutsu runs a narrow purge on load. Do not swap it.
- `NO_GC_REF_NAMESPACE` is **private** in jj-lib 0.42 and 0.44. Pyjutsu cannot
  import it. The duplicated literal is unavoidable today.

Required action: keep the local constant, and treat it as vendored data. Add it
to the per-upgrade re-verification list with `src/config/revsets.toml` and
`_quote`. Record in the doc comment why the narrow purge exists rather than
`Store::gc`. Open an upstream request to export the constant.

**F4 — the gix 0.84 → 0.85 bump is under-scoped in the issue.**

The issue's port list mentions one gix change: remove the
`gix::remote::fetch::Tags` import. There are 33 direct gix call sites. The
highest-risk one is `apply_head_ref_packed` (`workspace.rs:349`), which drives
the low-level file-store transaction API:

```rust
ref_store.transaction()
    .packed_refs(PackedRefs::DeletionsAndNonSymbolicUpdatesRemoveLooseSourceReference(odb))
    .prepare(edits, Fail::Immediately, Fail::Immediately)
    .commit(None)
```

Treat this call site as its own port task with its own compile check. Do not
assume the mechanical port covers it.

**F5 — `_quote` needs a per-upgrade re-verification step.**

`python/pyjutsu/revset.py::_quote` reimplements jj-lib's `escape_string` in
Python. Nothing detects divergence at build time. Every jj-lib upgrade must
re-diff it against `dsl_util.rs::escape_string` in the target release, exactly
as `src/config/revsets.toml` is re-diffed.

#### Rules going forward

1. Prefer jj-lib. Reach past it only when this inventory records a GAP.
2. Mark every such site with a `// jj-lib gap:` comment that names the missing
   API and the release checked.
3. Declare every direct dependency feature that Pyjutsu itself calls. Never
   rely on a transitive crate's feature choice.
4. Import shared constants from jj-lib where jj-lib exports them. When a needed
   constant is private, restate it, mark it as vendored, and add it to the
   re-verification list.
5. Re-verify every vendored copy at each upgrade. The list is
   `src/config/revsets.toml`, `python/pyjutsu/revset.py::_quote`, and
   `NO_GC_REF_NAMESPACE` in `src/workspace.rs`.
6. Re-run this audit at every jj-lib upgrade, before the pin moves.

#### Effect on the upgrade order

Three issue-002 steps change:

- Add `features = ["sha1"]` to Pyjutsu's direct `gix` edge when the pin moves
  (F1). This is a correctness fix, not the compile fix the issue describes.
- Add the `patch_id_hex` hash decision to the object-hash step (F2).
- Budget the gix port by call site, not as one mechanical edit (F4).

Phase 2.5 changes no behavior. It adds the inventory and the source comments.
No production statement changes.

F1's one-line manifest fix (`features = ["sha1"]`) is **not** applied here. It
lands with the pin move, so the manifest and the lock change together.

#### Follow-up: non-jujutsu surface review

[[NATIVE_SURFACE_REPORT.md]] re-reads this inventory under a stricter rule:
not "does jj-lib provide this?" but "does **jujutsu** provide this?" It
recommends removing annotated tags in favour of jj-lib lightweight tags,
replacing the `gix` hasher and the hand-ported `_quote`, and deprecating the
git ref-repair escape hatch. The report retires findings F1, F3, and F5.

[[LIBRARY_DESIGN_REVIEW.md]] re-reads the surviving `gix` code with no
carry-over, and inventories what jj offers that Pyjutsu does not bind. It
concludes that `git_refs` and the ref writers are one feature that belongs in
gitman, that `remotes` is the single justified `gix` caller, and that Pyjutsu's
**read** surface — conflict content, file bytes, short ids — is where the real
gaps are. Feature work belongs in a project 003, not here.

[[COLOCATED_GIT_SURFACE.md]] supersedes the "minimise gix" premise of both
reports above. `gix` already ships in every wheel through jj-lib, so the real
cost is API **depth**, not call count. It proposes a `ws.git` namespace for the
git half of a colocated repo — annotated tag read/write, git config, `HEAD`,
worktrees, objects, submodules, reflog — and reverses two earlier calls: the
ref-repair trio stays, and the annotated tag code moves rather than dies.

[[IMPLEMENTATION_PLAN.md]] turns all three reports into ordered lanes: Phase A
(four pre-bump removals), Phase B (the 0.44 pin move, with three amendments to
the issue's order), Phase C (project 003 — the jj read surface), and Phase D
(project 004 — the `ws.git` colocated namespace).

#### Validation

```text
cargo fmt --check                         PASS
cargo clippy --all-targets -- -D warnings PASS
cargo test                                PASS: 7 passed, 0 failed
ruff check python tests scripts           PASS
pytest -q                                 PASS: exit 0
devenv tasks run pyjutsu:verify           PASS: exit 0
```

The parallel test reporter suppresses pytest's summary line. Exit code 0 is the
recorded evidence.

Comment-only changes landed at these sites: `src/workspace/tags.rs`,
`src/workspace.rs` (`ensure_jj_git_excluded`, `prune_orphaned_keep_refs`,
`apply_head_ref_packed`, the trunk HEAD write), `src/repo_view.rs`
(`patch_id_hex`), and `python/pyjutsu/revset.py` (`_quote`).
