# Pyjutsu — Developer Guide

For people working on **Pyjutsu itself** — adding bindings, fixing bugs, porting to a new jj-lib.
If you only want to *use* the library, read [`USER_GUIDE.md`](USER_GUIDE.md). For the design
rationale and scope decisions, read [`PYJUTSU_CONCEPT.md`](PYJUTSU_CONCEPT.md) — it is the
canonical spec and this guide assumes it.

---

## 1. The one rule: a thin native layer, a rich Python layer

Pyjutsu is two layers with a hard boundary between them (concept §4):

```
┌─────────────────────────────────────────────────────────────┐
│  python/pyjutsu/   (pure Python)                             │
│  Pydantic models, the public API, defaults, docstrings,      │
│  ergonomics, the run_jj escape hatch, the revset builder.    │
│  Validates plain data at the FFI boundary.                   │
├─────────────────────────────────────────────────────────────┤
│  src/  →  _pyjutsu   (PyO3 native extension, Rust)           │
│  Opaque handles + PLAIN Python data (dict/list/str) only.    │
│  Never exposes a jj-lib type. Holds no business logic.       │
├─────────────────────────────────────────────────────────────┤
│  jj-lib 0.42.0   (hard-pinned in Cargo.toml)                 │
└─────────────────────────────────────────────────────────────┘
```

**Design rule (do not violate):** the Rust layer stays thin and dumb. It returns dicts/lists/
strings, never `jj-lib` types, and holds no policy. All modeling, defaults, coercion, and the
public contract live in Python, which validates the plain data as it crosses the boundary. When
in doubt about where logic goes: **if it's a decision or a default, it's Python; if it's calling
jj-lib, it's Rust.**

Why this split earns its keep: `jj-lib` is explicitly unstable, so we keep the surface touching it
as small as possible, pin it exactly, and turn any behavioral drift into a loud test failure
(§5).

---

## 2. Repository layout

### Rust (`src/`) → the `_pyjutsu` cdylib

| File | Responsibility |
|---|---|
| `lib.rs` | `#[pymodule]`: registers `version`/`pyjutsu_version` + the `PyWorkspace`, `PyRepoView`, `PyCommitStream`, `PyTransaction` classes. Build-derived version constants. |
| `workspace.rs` | `PyWorkspace` — load/init, git interop, remotes, snapshot, stale, undo/restore, transaction factory. The biggest module. |
| `repo_view.rs` | `PyRepoView` (all reads) + `PyCommitStream` (lazy `iter_log`). |
| `transaction.rs` | `PyTransaction` — the mutation verbs, bound to one thread. |
| `revset.rs` | Revset parsing/evaluation helpers and the cached resolved `RevsetConfig`. |
| `config/revsets.toml` | Vendored jj 0.42 default revset aliases; re-diff it on every jj upgrade. |
| `diff.rs`, `diff_stat.rs` | Diff + diff-stat computation, producing the plain hunk/stat dicts. |
| `convert.rs` | jj-lib value → plain-Python-dict converters (the shape the Pydantic models expect). |
| `errors.rs` | The `PyjutsuError` hierarchy + `jj-lib` error → exception mapping. |
| `build.rs` | Emits `PYJUTSU_JJ_LIB_VERSION` from the resolved `Cargo.lock` (see §7). |

### Python (`python/pyjutsu/`) → the public package

| File | Responsibility |
|---|---|
| `__init__.py` | Public exports, `__version__`, `JJ_VERSION`/`JJ_LIB_TARGET`, the stale-build tripwire. |
| `workspace.py` | The `Workspace` facade (main entry point). |
| `repo_view.py` | The `RepoView` read surface. |
| `transaction.py` | The `Transaction` token + hunk-selection/description normalizers. |
| `models.py` | All Pydantic v2 models (frozen, `extra="forbid"`). |
| `revset.py` | The `Revset`/`Pattern` builder (renders to strings; evaluates nothing). |
| `errors.py` | Re-exports the native hierarchy + the pure-Python `JjCliError`. |
| `_pyjutsu.pyi` | Type stub for the native extension — **keep in sync with `src/`**. |

Revset aliases are loaded from resolved `UserSettings` once per workspace, then shared by views
and transactions. Keep the vendored table at `ConfigSource::Default`: `tests/test_revset_config.py`
compares its effective values to the pinned CLI, and user/repository/workspace configuration must
continue to override it. Configured immutability is checked in `transaction.rs` before every
rewrite verb; `transaction(ignore_immutable=True)` is intentionally narrow and cannot lift the
root guard.

`tests/` holds the Python test suite (differential + unit); `tests/golden/` holds golden model
fields; `nix/pyjutsu.nix` defines the devenv tasks.

---

## 3. Environment & the build/test/lint loop

**Everything runs inside the devenv shell** — it pins the Rust toolchain, `maturin`, and the
matching `jj` 0.42.0 CLI used for differential tests. Never invoke bare `cargo`/`maturin`/`pytest`/
`ruff`; the ambient shell has none of the pinned toolchain.

```sh
devenv shell -- devenv tasks run pyjutsu:build   # maturin develop --uv (rebuild the extension)
devenv shell -- devenv tasks run pyjutsu:test    # pytest -q  &&  cargo test
devenv shell -- devenv tasks run pyjutsu:lint    # ruff check python tests  &&  cargo clippy -D warnings
```

The task definitions live in `nix/pyjutsu.nix` (`enterTest` runs the same build→pytest→cargo test
sequence). A one-off command inside the shell:

```sh
devenv shell -- pytest -q tests/test_split.py
```

> **Stale-build tripwire.** `python/pyjutsu/__init__.py` hard-fails import if its `__version__`
> ≠ the compiled extension's `pyjutsu_version()`. During editable dev you can bump the Python
> version before `maturin develop` rebuilds the `.so`; that skew raises a clear "stale build:
> rebuild the extension" error instead of shipping a mismatch. If you see it, just re-run
> `pyjutsu:build`.

> **Note on `cargo fmt`.** This repo is *not* clean under a bare `cargo fmt`; run formatting/lint
> through the devenv tasks, not ad-hoc.

---

## 4. Adding a new binding — the standard pattern

Say you want to bind a new jj verb. The change almost always touches five places, in this order:

1. **Rust method** on `PyWorkspace`/`PyRepoView`/`PyTransaction` (in the matching `src/*.rs`).
   Call jj-lib, convert the result to a **plain dict/list/str** via `convert.rs` helpers, and map
   any `jj-lib` error to the right `PyjutsuError` subclass (`errors.rs`). Release the GIL
   (`Python::allow_threads`) around I/O-heavy work (snapshots, fetch/push, big revset eval), and
   drive async jj-lib APIs to completion synchronously with `pollster`/`block_on` (see the
   existing streams for the idiom).
2. **Type stub** entry in `python/pyjutsu/_pyjutsu.pyi` — the native signature returning
   `dict[str, object]` / `list[...]` / `None`.
3. **Pydantic model** in `models.py` if the verb returns a new shape (frozen, `extra="forbid"`).
4. **Python facade** method on `Workspace`/`RepoView`/`Transaction`: coerce arguments (revset
   strings, list-or-scalar), call the native handle, `Model.model_validate(row)` the result, and
   write the **user-facing docstring** (the native layer carries none). This is where defaults and
   ergonomics live.
5. **Tests** — a differential test vs the pinned CLI (§6) is the primary correctness proof; add
   unit tests for the Python coercion/validation.

Keep the Rust side mechanical. If you're tempted to make a *decision* in Rust (a default, a
fallback, a shape choice), that belongs in Python.

### Handling jj-lib's `!Send` transaction

`Transaction`/`MutableRepo` are **`!Send`** in jj-lib 0.42. `PyTransaction` is therefore
thread-affine and unsendable: the native transaction lives inside the `PyWorkspace` handle (behind
its `Mutex`) and the Python `Transaction` is just a token that drives it between `__enter__` and
`__exit__`. This is *why* there's no native async facade — see the concept doc.

---

## 5. Adding a hook event

Hooks are a **pure-Python feature** (concept §4: the Rust layer stays thin and dumb), so a new
event touches no Rust. Adding one for an operation that already publishes a jj op is a three-step
change:

1. **Wire the verb** in `python/pyjutsu/workspace.py` (or `transaction.py`): call
   `self._fire_pre("<event>", *payload)` before the native call and
   `self._fire_post("<event>", op_id, *payload)` after it, exactly like `git_push` does for
   `pre-push`/`post-push`. Pre payloads are the call's arguments; post payloads lead with the
   published operation (or its id). `Transaction.__exit__` is the model for
   `pre-commit`/`post-commit`, where the pre-hook must run while the transaction is still **open**
   (so hooks can mutate it) and the pending path list is computed lazily — only when a pre-hook is
   actually registered, keeping the zero-cost promise:

   ```python
   if self._hooks.count("pre-commit"):
       self._hooks.fire("pre-commit", self, paths=self.changed_paths("@"))
   else:
       self._hooks.fire("pre-commit", self)
   ```

2. **Declare the event** in `python/pyjutsu/hooks.py`: add it to `CONFIG_EVENTS` (the declarative
   config rejects events that aren't wired — a config may never declare a hook that won't fire) and
   to the per-event signature table in the module docstring.

3. **Test** in `tests/test_hooks.py`: add a parametrized veto case + a post-fire case to the
   `test_pre_hooks_veto_every_wired_event` / `test_post_hooks_fire_after_every_wired_event` tables
   (your event + a lambda), plus any payload-shape assertions.

The `_fire_pre`/`_fire_post` helpers already wrap failures into `HookAbort` (pre) / `PostHookError`
(post, carrying the published op id), honor `on_post_failure = "warn"`, and let `fire`'s
`fail_fast` policy apply — a new event gets the full semantics for free.

---

## 6. Testing strategy — differential tests are the safety net

The core defense against jj-lib instability is **differential testing against the pinned `jj`
CLI**: run an operation through both Pyjutsu and `jj`, then assert the repo state is equivalent
(same change graph, bookmarks, op-log effect). This validates correctness *and* turns any behavior
change in a jj upgrade into a loud failure.

The shared fixtures live in `tests/conftest.py`:

- `jj` — a `JjCli` driver bound to a scratch config with a **pinned identity + timestamp**,
  exported as `JJ_CONFIG` **in this process too** so the in-process binding and the CLI subprocess
  build byte-identical commits (matching commit ids).
- `scratch_repo` / `linear_repo` / `bookmarked_repo` / `diffstat_repo` / `conflict_repo` — repos
  built by the pinned CLI for tests to read/mutate.

Getting **commit-id parity** with the CLI requires care (documented in memory + the M2 notes):
pinned commit timestamp, `JJ_CONFIG` loaded in-process, and jj's trailing-newline description
convention (`transaction.py::_complete_newline`). Differential tests can author from primary or
secondary workspaces. Both resolve the same secure repository configuration identity.

`src/config_loader.rs` is the small Jujutsu 0.42 policy adapter over jj-lib.
It loads defaults, environment base values, user paths, secure repository configuration, secure
workspace configuration, and environment overrides. It then resolves conditional scopes.
The workspace loader resolves the canonical repository path before final `UserSettings` exist.
Normal loads use `SecureConfig::maybe_load_config()` and do not create empty configuration files.
Initialization uses the bootstrap path because repository configuration does not exist yet.

Workspace creation follows the pinned CLI lifecycle. The first operation registers the workspace.
The second operation creates and edits the requested working-copy commit. Tests must compare both
the topology and the checked-out files. Resolve all explicit revisions before filesystem mutation.
Treat a post-registration error as partial state and include a recovery action.

Run the live secondary-workspace acceptance contract after the build gate:

```bash
devenv shell -- python scripts/verify_secondary_workspaces.py /tmp/pyjutsu-secondary-live
```

Use a path that does not exist. The verifier creates isolated user configuration and real on-disk
workspaces. It compares Pyjutsu with the pinned `jj` CLI for topology, trees, conflicts, sparse
patterns, operation history, validation, and authoring configuration.

Other layers: Rust unit tests (`cargo test`, e.g. the diff line-counting cases in `diff_stat.rs`),
Python unit tests for model validation/coercion, and **golden fixtures** (`tests/golden/`,
checked by `test_golden.py`) that pin the model field shapes — regenerate them against the pin
when a model's shape legitimately changes.

---

## 7. Versioning & the pins

Two version numbers, kept deliberately separate:

- **`pyjutsu.__version__`** (e.g. `0.10.0`) — Pyjutsu's own semver, moving on its own cadence.
  Hand-maintained in `Cargo.toml` **and** `python/pyjutsu/__init__.py` (both must match; the
  stale-build guard enforces the Python↔native side).
- **`JJ_VERSION` / `JJ_LIB_TARGET`** (e.g. `0.42.0`) — the linked jj-lib. **Build-derived**:
  `build.rs` reads the resolved `Cargo.lock` and emits `PYJUTSU_JJ_LIB_VERSION`, so it *cannot*
  drift from the actual dependency (project 10 §P3 killed the second hand-maintained copy —
  `JJ_LIB_TARGET` is now just an alias of `JJ_VERSION`).

The jj-lib pin itself is `jj-lib = "=0.42.0"` in `Cargo.toml` (the real API contract, with
`Cargo.lock` committed), and the **matching `jj` CLI** is pinned in `devenv.nix` so differential
tests compare against the exact CLI of the bound library. `gix` is pinned to jj-lib 0.42.0's own
locked `=0.84.0` so the types unify (`cargo tree -i gix` must show a single version).

A **normal feature/fix** is an ordinary minor/patch bump of `__version__` — nothing jj-related
moves. Bumping jj-lib is a separate, deliberate act (§8).

---

## 8. Porting to a new jj-lib version

jj-lib is explicitly unstable; a bump is a Rust-side port, not a routine dependency update. The
0.38→0.42 port (pyjutsu 0.8.0) is the worked example (see memory
`pyjutsu-0-8-0-jj-lib-0-42-port`). The shape of the work:

1. Move the pins together: `jj-lib = "=X.Y.Z"` in `Cargo.toml`, the matching `jj` CLI rev in
   `devenv.nix`, and jj-lib's locked `gix` in the `gix` pin. Refresh `Cargo.lock`.
2. Rebuild and chase compile errors — API renames, async-ification (0.42 pushed many APIs to
   streams/futures; the `block_on`/`pollster` idiom is how we drive them synchronously),
   restructured git push/import options, etc.
3. Fix the CLI **differential-test harness** for CLI flag/behavior changes (e.g. `--allow-new`
   removed, `jj undo` → `jj op revert`).
4. Re-run the full differential suite — it is the acceptance test for the port. Update behavioral
   notes/citations that reference a specific jj version.

Because `JJ_VERSION` is build-derived, the version strings update themselves once the pin moves —
no hand-editing of version constants scattered through the code.

---

## 9. Gotchas worth knowing up front

- **Panics across FFI abort the process** — but PyO3 wraps `#[pymethods]` bodies in
  `catch_unwind`, so a panic surfaces as a Python exception. Still, map fallible jj-lib paths to
  `PyjutsuError` rather than relying on panic-catching.
- **`extra="forbid"` is a feature, not friction.** A model rejecting an unexpected key is the
  drift tripwire firing — it means the native layer's dict shape and the model disagree. Fix the
  mismatch; don't loosen the model.
- **Reads must never snapshot.** `RepoView` is side-effect-free by contract. If a "read" would
  snapshot `@`, it's modeled wrong.
- **Colocated git needs HEAD/index care.** After mutations, the on-disk git `HEAD`/index can go
  stale and mislead raw-git tooling; `sync_colocated` (and `git_export`) rebuild them. gix holds a
  config snapshot, so some verbs take a fresh `RepoLoader` to avoid staleness.
- **Keep the `.pyi` in sync.** It's the only type contract for the native surface; a new native
  method with no stub entry is a silent gap.

---

## 10. Where to look next

- [`PYJUTSU_CONCEPT.md`](PYJUTSU_CONCEPT.md) — the canonical design spec (architecture §4,
  testing §7, risks §8, workspaces §11, scope §12).
- [`USER_GUIDE.md`](USER_GUIDE.md) — the consumer-facing API, useful for checking a binding reads
  the way a user expects.
- `.scratch/projects/` — the per-milestone design docs and kickoff prompts; the running history of
  how each slice was built.
- The docstrings in `python/pyjutsu/*.py` — the most precise, up-to-date description of each
  verb's contract and edge cases.
