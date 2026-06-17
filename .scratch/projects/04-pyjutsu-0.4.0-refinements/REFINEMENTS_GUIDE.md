# Pyjutsu 0.4.0 — Deferred-refinement Implementation Guide (4 slices)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec; §5 surface, §12 scope) →
> `.scratch/projects/03-pyjutsu-m2-mutations/` slice guides (where each of these was *flagged, not
> faked*) → **this document** (the verified plan for the four 0.4.0 refinements) → the code it
> produces. The deferred-scope review session that selected these four items is the immediate parent.
>
> **Pins unchanged:** `jj-lib = "=0.38.0"` (default features include `git`); `gix = "=0.78.0"`.
> `JJ_LIB_TARGET` stays `"0.38.0"`. Pyjutsu uses **independent semver**: this milestone bumps
> `0.3.0 → 0.4.0` (see §8). **Start from a clean `main`** (M2 complete @ `0.3.0`, 152 tests green).
> Every jj-lib API ref below is `file:line` into
> `~/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/jj-lib-0.38.0/src/`, **verified against the
> pinned source while writing this guide**.

---

## 0. What this milestone is, and why these four

The M2 write layer shipped its verbs in *simplest faithful form* and **flagged** the refinements it
deliberately skipped. The deferred-scope review inventoried all of them; the user selected four —
the highest value-to-effort, jj-lib-clean, naturally differential-testable ones:

| Slice | Item | Source flag | jj-lib API status |
|---|---|---|---|
| **1** | `tx.rebase` gains `-r` (single-commit reattach) and `-b` (whole-branch) | `src/transaction.rs:256`; `transaction.py:163` | clean — `MoveCommitsTarget::Commits` (rewrite.rs:535) |
| **2** | `git_push` gains bookmark **deletion** + **multi-bookmark** | `src/workspace.rs:955`; slice-11 guide §4b | clean — `BookmarkPushUpdate.new_target: Option` already nullable |
| **3** | `git_fetch` gains **glob / negative** bookmark patterns | `src/workspace.rs:851`; slice-11 guide §4a | clean — `StringPattern::glob` (str_util.rs:141) + `StringExpression::negated` (str_util.rs:390) |
| **4** | **Snapshot fidelity**: real `.gitignore` chain + `snapshot.max-new-file-size` from settings | `src/workspace.rs:358`; `[[m2-slice5-snapshot]]` | clean primitives — `GitIgnoreFile::chain_with_file` (gitignore.rs:108) + `LocalSettings::get_value_with` (settings.rs:267); the unit-byte parse is hand-rolled |

**Sequencing is deliberate and load-bearing.** Slices 1–3 are *additive* and never touch the
snapshot → tree → commit-id path, so the existing 152-test differential net validates them unchanged.
**Slice 4 is fenced last** because it is the only one that can move a commit id; it ships with its own
fixture so any parity drift is contained. **Do them in order. Each slice is independently
committable and must leave the suite green before the next begins.**

**Explicitly still out of scope (do NOT implement; keep the flags):** `rebase` interactive selection;
`squash` partial/interactive (slice 3 of M2's flag — *not* in this milestone); `git_fetch` **tag**
fetching (upstream-blocked, jj TODO #7528) and `--all-remotes`; `git_push` `--all`/`--tracked`/`-r
<rev>` selection and force-with-lease beyond jj-lib's built-in negotiation; `snapshot.auto-track`
fileset (slice 4 ships file-size + gitignore only unless auto-track falls out cheaply — see §4.4);
`add_workspace -r`/sparse; revset builder; diffs/hunks; async. These remain *flagged, not faked*.

---

## 1. Carried structural facts (true for every slice; re-verify, don't assume)

- **Thin Rust, rich Python.** `_pyjutsu` returns opaque dicts / plain scalars / `None` only. **No
  jj-lib or gix type crosses the FFI.** Models, ergonomics, and policy live in pure-Python `pyjutsu`.
  Every new method here returns the same shapes the M2 verbs already do (a `Commit`/`Operation` dict,
  or `None`).
- **`!Send` discipline.** `Transaction`, `MutableRepo`, `GitFetch` are `!Send` (`[[m2-transaction-not-send]]`).
  Anything off-GIL (`py.allow_threads`) must **create and drop them inside one synchronous closure on
  one thread** — the slice-9/10/11 pattern. In-`Transaction` graph work (slice 1) stays **on** the GIL.
- **Fresh loader per git verb** (`[[m2-slice10-git-interop]]`). `git_fetch`/`git_push` re-open the
  store via `PyWorkspace::fresh_loader` so a remote added in-process is visible (git config-snapshot
  staleness). Slices 2–3 inherit this from the existing methods — keep it.
- **`rebase_descendants()` after any commit-rewriting step** (landmine #1). Slice 1's `move_commits`
  and slice 3's `import_refs` both record rewrites/abandons → call it before reading back / committing.
  Push (slice 2) authors no commit rewrite — `rebase_descendants` is unnecessary there.
- **`has_changes()` is the no-op signal** (`[[m2-slice10-git-interop]]`): a git verb that changed
  nothing drops the tx uncommitted and returns `None`.
- **Differential oracle = the pinned `jj` 0.38.0 CLI**, driven via `tests/diff/jj_cli.py::JjCli`,
  against a `_copy_repo` (shutil.copytree) sibling. The binding and the CLI share identity + pinned
  `debug.commit-timestamp` via `JJ_CONFIG` (set in `tests/conftest.py::jj`), which is why commit ids
  match. **Everything runs through devenv** — never bare `cargo`/`maturin`/`pytest`/`jj`.

---

## 2. Slice 1 — `tx.rebase` gains `-r` and `-b`

### 2.1 What it is
Today `tx.rebase(commit, onto=…)` is `jj rebase -s`: it always carries `commit` **and its
descendants** (`MoveCommitsTarget::Roots`, `src/transaction.rs:284`). Add the two missing modes:
- **`-r` (revision):** move *only* `commit`, reattaching its children onto `commit`'s old parents.
- **`-b` (branch):** move the whole branch containing `commit` — i.e. the roots of
  `connected(onto..commit)`, everything not already an ancestor of the destinations.

### 2.2 jj-lib APIs (verified)
| What | Signature / fact | Ref |
|---|---|---|
| Target enum | `MoveCommitsTarget::{Commits(Vec<CommitId>), Roots(Vec<CommitId>)}` | rewrite.rs:532-537 |
| `-r` semantics | `Commits([id])` moves exactly those commits; children reattach to their old parents | rewrite.rs:603 |
| `-s` semantics (today) | `Roots([id])` moves the roots **and all descendants** | rewrite.rs:629 |
| Mover | `move_commits(&mut MutableRepo, &MoveCommitsLocation, &RebaseOptions) -> Result<MoveCommitsStats, …>` | rewrite.rs (already used at transaction.rs:286) |
| Location | `MoveCommitsLocation { new_parent_ids, new_child_ids: Vec<CommitId>, target }` | rewrite.rs (used at transaction.rs:281) |

**`-b` (whole-branch).** jj-cli computes the branch roots as
`roots( connected(targets..commit) )` and feeds them as `MoveCommitsTarget::Roots`. The clean,
in-`Transaction` way to get those ids is to evaluate that revset through the binding's existing
`resolve`/revset machinery against `repo` (the same `RevsetExpression` path `resolve_single` uses).
**Verify before coding:** find how `src/transaction.rs` already evaluates revsets (`resolve_single`,
~transaction.rs:273) and reuse it to evaluate a *multi*-commit expression for `-b`. If multi-commit
revset evaluation inside the tx proves more than a couple of lines, **ship `-r` in this slice and
flag `-b` as the one remaining mode** — `-r` is the high-value half and is trivially clean.

### 2.3 Rust sketch (`src/transaction.rs`, modify `rebase`)
Add a `mode: &str` (or a small enum mapped from Python) param. Keep the root-panic guard and the
`rebase_descendants()` + re-read tail exactly as-is.

```rust
#[pyo3(signature = (commit, onto, mode="source"))]
fn rebase<'py>(&self, py: Python<'py>, commit: &str, onto: Vec<String>, mode: &str)
    -> PyResult<Bound<'py, PyDict>>
{
    // … existing: borrow tx, repo = tx.repo_mut(), resolve `target`, root guard, new_parent_ids …
    let target = match mode {
        "source"   => MoveCommitsTarget::Roots(vec![target.id().clone()]),        // -s (today)
        "revision" => MoveCommitsTarget::Commits(vec![target.id().clone()]),      // -r
        "branch"   => MoveCommitsTarget::Roots(branch_roots),                     // -b (see 2.2)
        _ => return Err(PyjutsuError::new_err("rebase mode must be source|revision|branch")),
    };
    let loc = MoveCommitsLocation { new_parent_ids, new_child_ids: vec![], target };
    move_commits(repo, &loc, &RebaseOptions::default()).map_err(map_backend_err)?;
    repo.rebase_descendants().map_err(map_backend_err)?;
    let rebased = self.resolve_single(&*repo, commit)?;   // id changed — re-read
    CommitData::build(&*repo, &rebased)?.to_dict(py)
}
```

**Verify while implementing:**
- For `-r`, after `move_commits` the *children* were reattached but `commit` itself keeps its change
  id with a new commit id — the existing re-read by `commit` revset still resolves it. Confirm against
  `jj rebase -r` that `commit`'s new parents are `onto` and its old children now point at `commit`'s
  old parents.
- `RebaseOptions::default()` keeps `EmptyBehavior::Keep` (M2's documented choice). Don't change it.

### 2.4 Python facade (`python/pyjutsu/transaction.py`, modify `rebase`)
```python
def rebase(self, commit: str, *, onto: str | list[str], mode: str = "source") -> Commit:
    """… mode ∈ {"source","revision","branch"} → jj rebase -s/-r/-b …"""
    targets = [onto] if isinstance(onto, str) else list(onto)
    return Commit.model_validate(self._require_open().rebase(commit, targets, mode))
```
Replace the "out of scope" sentence in the current docstring (transaction.py:163) with the new
mode description. Update `python/pyjutsu/_pyjutsu.pyi`:
`def rebase(self, commit: str, onto: list[str], mode: str = ...) -> dict[str, object]: ...`

### 2.5 Differential tests (extend `tests/test_rewrite.py`)
Mirror the existing `test_rebase_subtree_matches_cli` / `test_rebase_carries_descendants` pattern
(`_copy_repo` + `jj.change_ids`/`commit_id`/`parent_commit_ids`). Add:
- **`test_rebase_revision_matches_cli`** *(headline)*: on `linear_repo` (A→B→C→@), `mode="revision"`
  reattaching `B` onto `A`'s parent (or `C` onto `A`) vs `jj rebase -r <B> -d <dest>` on a copy.
  Assert: `B`'s change id preserved, `B`'s new parent is the dest, **`C`'s parent is now `B`'s old
  parent** (the -r distinction), and `commit_id(self, X) == commit_id(other, X)` for every surviving
  change. This is the test that proves `-r ≠ -s`.
- **`test_rebase_branch_matches_cli`** *(only if `-b` shipped)*: vs `jj rebase -b`. If `-b` is
  deferred, add `test_rebase_branch_mode_raises`? No — simply omit the mode from the surface and the
  docstring until shipped.
- **`test_rebase_bad_mode_raises`**: `mode="nonsense"` ⇒ `PyjutsuError`.
- Keep `test_rebase_subtree_matches_cli` (now `mode="source"`, the default) green unchanged.

---

## 3. Slice 2 — `git_push` deletion + multi-bookmark

### 3.1 What it is
Today `git_push(remote, bookmark, *, allow_new=False)` pushes exactly one existing local bookmark,
create-or-fast-forward (`src/workspace.rs:906`). Add:
- **Deletion:** remove a bookmark on the remote — `BookmarkPushUpdate { old_target: <remote tip>,
  new_target: None }`. The local bookmark may be absent (you're deleting the *remote* ref).
- **Multi-bookmark:** push several bookmarks in one operation — extend `branch_updates` to N entries.

### 3.2 jj-lib APIs (verified — all already in use by the current `git_push`)
| What | Fact | Ref |
|---|---|---|
| Update record | `BookmarkPushUpdate { old_target: Option<CommitId>, new_target: Option<CommitId> }` — **`new_target: None` is a delete** | git.rs:2920 (in use at workspace.rs:958) |
| Targets | `GitBranchPushTargets { branch_updates: Vec<(RefNameBuf, BookmarkPushUpdate)> }` — already a Vec | git.rs:2930 |
| Push | `git::push_branches(&mut MutableRepo, opts, &RemoteName, &GitBranchPushTargets, &mut cb) -> GitPushStats` | git.rs:2945 |
| Stats gate | `GitPushStats::all_ok()`; `rejected` / `remote_rejected` carry `(RefNameBuf, Option<reason>)` | git.rs:170 |
| Local / remote read | `view.get_local_bookmark(ref).as_normal()`; `view.get_remote_bookmark(ref.to_remote_symbol(remote)).target.as_normal()` | view.rs:168/234 |

**No new jj-lib surface.** This slice is entirely about composing `branch_updates` differently.

### 3.3 Rust sketch (`src/workspace.rs`, generalize `git_push`)
Change the signature to take a list and a `delete` flag; build one `BookmarkPushUpdate` per bookmark.

```rust
#[pyo3(signature = (remote, bookmarks, allow_new=false, delete=false))]
fn git_push<'py>(&self, py: Python<'py>, remote: &str, bookmarks: Vec<String>,
                 allow_new: bool, delete: bool) -> PyResult<Option<Bound<'py, PyDict>>>
{
    // … fresh_loader, settings, off-GIL closure as today …
    let view = repo.view();
    let mut branch_updates = Vec::with_capacity(bookmarks.len());
    for bm in &bookmarks {
        let bref: &RefName = bm.as_str().as_ref();
        let remote_ref = view.get_remote_bookmark(bref.to_remote_symbol(remote_name));
        let old_target = match &remote_ref.target {
            t if t.is_absent() => None,
            t => Some(t.as_normal().ok_or_else(|| map_git_err(
                format!("remote bookmark '{bm}@{remote}' is conflicted")))?.clone()),
        };
        let new_target = if delete {
            if old_target.is_none() {                    // nothing to delete
                return Err(map_git_err(format!("bookmark '{bm}' doesn't exist on remote '{remote}'")));
            }
            None
        } else {
            let local = view.get_local_bookmark(bref);
            if local.is_absent() { return Err(map_git_err(format!("no local bookmark '{bm}'"))); }
            let id = local.as_normal().ok_or_else(|| map_git_err(
                format!("refusing to push conflicted bookmark '{bm}'")))?.clone();
            if old_target.is_none() && !allow_new {
                return Err(map_git_err(format!(
                    "bookmark '{bm}' doesn't exist on remote '{remote}'; pass allow_new=True")));
            }
            Some(id)
        };
        branch_updates.push((RefNameBuf::from(bm.as_str()),
                             BookmarkPushUpdate { old_target, new_target }));
    }
    drop(view);                                          // release read borrow before tx
    let targets = GitBranchPushTargets { branch_updates };
    // … start_transaction, push_branches, all_ok() gate, has_changes(), commit, finish_op …
}
```

**Verify while implementing:**
- The `view` read borrow must end (`drop(view)` or scope) **before** `repo.start_transaction()`.
- `delete=True` must **not** require a local bookmark — only a remote-tracking `old_target`.
- Keep the `all_ok()` rejection → `GitError` (listing `rejected`/`remote_rejected`) exactly as today.
- Reject `delete=True` combined with an empty `bookmarks` list early (`GitError`).

### 3.4 Python facade (`python/pyjutsu/workspace.py`, modify `git_push`)
Accept `str | list[str]`, add `delete`:
```python
def git_push(self, bookmark: str | list[str], *, remote: str = "origin",
             allow_new: bool = False, delete: bool = False) -> Operation | None:
    names = [bookmark] if isinstance(bookmark, str) else list(bookmark)
    row = self._handle.git_push(remote, names, allow_new, delete)
    return Operation.model_validate(row) if row is not None else None
```
> **Signature note:** the existing facade is `git_push(self, remote, bookmark, …)` (positional
> `remote` first) per concept §5 example `ws.git_push(bookmark="feature", remote="origin", …)`.
> **Keep backward compatibility:** preserve the existing parameter order/names that
> `tests/test_git_net.py` already calls, and only *add* `delete` + widen `bookmark` to accept a list.
> Check the current call sites in `tests/test_git_net.py` first and match them.

Update `_pyjutsu.pyi`:
`def git_push(self, remote: str, bookmarks: list[str], allow_new: bool = ..., delete: bool = ...) -> dict[str, object] | None: ...`

### 3.5 Differential tests (extend `tests/test_git_net.py`)
Reuse the `bookmarked_repo` fixture (bare `origin`, pushed `feature`) and the show-ref oracle from
the existing push tests. Add:
- **`test_push_delete_matches_cli`** *(headline)*: `ws.git_push("feature", delete=True)` vs
  `jj git push --bookmark feature --deleted` (verify the exact CLI delete flag — jj 0.38 uses
  `--deleted` for tracked-delete or `bookmark delete` + push; confirm with `jj git push --help` in
  devenv). Assert `refs/heads/feature` **absent** from the bare `origin` (`git -C origin.git
  show-ref`) on both sides; one op each.
- **`test_push_multiple_bookmarks`**: create `feat-a`, `feat-b`, push both in one call; assert both
  refs present in `origin`, one op published by the binding.
- **`test_push_delete_nonexistent_raises`** ⇒ `GitError`.
- Keep all existing single-bookmark push tests green (they now pass a 1-element list internally).

---

## 4. Slice 3 — `git_fetch` glob / negative bookmark patterns

### 4.1 What it is
Today `git_fetch(remote, bookmarks=None)` maps a non-empty list to a union of **exact** names only
(`src/workspace.rs:853-861`). Add jj's pattern vocabulary so `bookmarks=["glob:feature/*"]` and
negative patterns work, matching `jj git fetch --bookmark 'glob:…'`.

### 4.2 jj-lib APIs (verified)
| What | Signature | Ref |
|---|---|---|
| Expr is a tree | `enum StringExpression { Pattern, NotIn, Union, Intersection }` | str_util.rs:351 |
| From a pattern | `StringExpression::pattern(StringPattern) -> Self` | str_util.rs:375 |
| Exact (today) | `StringExpression::exact(impl Into<String>)` | str_util.rs:380 |
| Negate | `StringExpression::negated(self)` → `NotIn` | str_util.rs:390 |
| Union all | `StringExpression::union_all(Vec<Self>)` | str_util.rs:400 |
| Glob pattern | `StringPattern::glob(&str) -> Result<Self, StringPatternParseError>` | str_util.rs:141 |
| Exact pattern | `StringPattern::exact(impl Into<String>)` | str_util.rs:121 |

So a glob becomes `StringExpression::pattern(StringPattern::glob(src).map_err(map_git_err)?)` and a
negative becomes `.negated()`.

### 4.3 The mapping (define it precisely, then test it)
Adopt jj's CLI string-pattern prefixes so power users transfer knowledge directly (concept §5
"the revset string *is* jj's"):
- bare name or `exact:<name>` → `StringPattern::exact`
- `glob:<pat>` → `StringPattern::glob`
- a leading `~` (or jj's documented negation form — **verify against jj 0.38's `--bookmark`
  help**) → wrap the parsed pattern in `.negated()`

**Combining semantics (match jj-cli):** positives are **unioned**; a fetch with only negatives is
ill-defined (nothing to start from) → treat a negatives-only list as `all().intersection(~neg…)`,
i.e. start from `all()` and subtract. Concretely: `union_all(positives)` (or `all()` if none) then
`.intersection(neg.negated())` for each negative. **Verify this against jj-cli's actual expression
construction** (grep jj-cli's `git fetch` bookmark parsing if available, else differential-test the
exact behavior) before trusting it — the union/intersection algebra is the one subtle part.

```rust
let bookmark = match &bookmarks {
    None => StringExpression::all(),
    Some(specs) => {
        let mut positives = Vec::new();
        let mut negatives = Vec::new();
        for s in specs {
            let (neg, body) = match s.strip_prefix('~') { Some(r) => (true, r), None => (false, s.as_str()) };
            let pat = if let Some(g) = body.strip_prefix("glob:") {
                StringPattern::glob(g).map_err(map_git_err)?
            } else if let Some(e) = body.strip_prefix("exact:") {
                StringPattern::exact(e)
            } else {
                StringPattern::exact(body)
            };
            if neg { negatives.push(StringExpression::pattern(pat)); }
            else   { positives.push(StringExpression::pattern(pat)); }
        }
        let mut expr = if positives.is_empty() { StringExpression::all() }
                       else { StringExpression::union_all(positives) };
        for n in negatives { expr = expr.intersection(n.negated()); }
        expr
    }
};
```

### 4.4 Python facade / stubs
Signature is unchanged (`bookmarks: list[str] | None`). Update the `git_fetch` docstring
(`workspace.py`) to document the `glob:` / `exact:` / `~` vocabulary and that **tags are still not
fetched** (jj #7528) and `--all-remotes` is still out of scope. No `.pyi` change.

### 4.5 Differential tests (extend `tests/test_git_net.py`)
Build a remote with several bookmarks (`feature/a`, `feature/b`, `main`) so a glob discriminates.
- **`test_fetch_glob_matches_cli`** *(headline)*: `ws.git_fetch("origin", ["glob:feature/*"])` vs
  `jj git fetch --bookmark 'glob:feature/*'` on a copy. Assert the binding imported exactly the
  `feature/*` remote-tracking rows (and **not** `main@origin`) on both sides; one op.
- **`test_fetch_negative_pattern`**: `["glob:feature/*", "~feature/b"]` fetches `feature/a` but not
  `feature/b` — assert against the CLI equivalent.
- **`test_fetch_exact_still_matches_cli`**: keep the existing exact-name behavior green.
- **`test_fetch_bad_glob_raises`**: a malformed glob ⇒ `GitError`.

---

## 5. Slice 4 — Snapshot fidelity (the careful one; do LAST)

### 5.1 What it is
`PyWorkspace::snapshot` (`src/workspace.rs:363-371`) hardcodes
`base_ignores = GitIgnoreFile::empty()` and `max_new_file_size = 1 << 20`. That reproduces the CLI
**only** for repos with no `.gitignore` and no oversized files (every current fixture, which is why
parity holds). Make it faithful:
1. **`base_ignores`** = the user/repo `.gitignore` chain jj-cli composes, so ignored files don't get
   auto-tracked into `@` on snapshot.
2. **`max_new_file_size`** = read `snapshot.max-new-file-size` from settings (default 1 MiB), so the
   configured cap is honored.

### 5.2 Why it's last / the risk
This is the **only** slice that can change a commit id: which untracked files enter `@`'s tree
depends on `base_ignores`, and the tree determines the commit id. The differential net currently
passes *because* the hardcode matches the CLI on gitignore-free fixtures. Changing this without a
gitignore/large-file fixture would either regress silently or look green for the wrong reason. **Ship
it behind its own fixture and verify tree/commit-id parity explicitly.**

### 5.3 jj-lib APIs (verified)
| What | Signature | Ref |
|---|---|---|
| Snapshot opts | `SnapshotOptions { base_ignores: Arc<GitIgnoreFile>, progress, start_tracking_matcher, force_tracking_matcher, max_new_file_size: u64 }` | working_copy.rs:212 |
| Empty (today) | `GitIgnoreFile::empty() -> Arc<Self>` | gitignore.rs:53 |
| Chain a file | `GitIgnoreFile::chain_with_file(&self, prefix: &str, path: PathBuf) -> Result<Arc<Self>, …>` | gitignore.rs:108 |
| Chain bytes | `GitIgnoreFile::chain(&self, prefix: &str, dir: &Path, contents: &[u8]) -> Result<Arc<Self>, …>` | gitignore.rs:64 |
| Read a setting | `LocalSettings::get_value_with(name, convert)` / `get_value(name)` (`ConfigValue`) | settings.rs:262/267 |

> **No `SnapshotOptions::from_settings` exists in jj-lib 0.38** — that composition lives in jj-cli.
> So the binding replicates it. The byte-size parse (`"1MiB"` → `u64`) is **hand-rolled**; jj-cli uses
> a `HumanByteSize`-style parser. Read the raw value via `get_value` and parse the unit suffix, or read
> it as the typed form jj-cli uses if exposed — **verify how `snapshot.max-new-file-size` is typed in
> settings** (grep the jj-cli source under the same registry if present, else accept a plain integer
> bytes value and a `<N>(KiB|MiB|GiB)` suffix). If the parse is more than a few lines, read the
> setting but **keep the 1 MiB default** when absent/unparseable, and flag full unit parsing.

**`base_ignores` composition.** jj-cli builds the chain from the user's global gitignore (config
`snapshot`/core.excludesfile equivalent) plus the repo's `.gitignore` files. **Match jj-cli's order
exactly** — grep its working-copy snapshot setup for the precise chain (global → repo-root
`.gitignore`). The realistic, parity-critical case is the **repo-root `.gitignore`**; chain that via
`chain_with_file("", workspace_root.join(".gitignore"))`. Confirm whether jj also reads nested
`.gitignore`s during snapshot (the snapshotter may handle per-directory ignores itself — in which
case `base_ignores` is only the *base* global/root layer). **This is the fact to verify before
coding** — getting the layering wrong is exactly what breaks commit-id parity.

### 5.4 Rust sketch (`src/workspace.rs`, in `snapshot`)
```rust
let base_ignores = {
    let root_ignore = ws.workspace_root().join(".gitignore");
    if root_ignore.exists() {
        GitIgnoreFile::empty().chain_with_file("", root_ignore).map_err(map_workingcopy_err)?
    } else {
        GitIgnoreFile::empty()
    }
    // …plus the global/user layer if jj-cli includes it — verify §5.3.
};
let max_new_file_size = read_max_new_file_size(&settings).unwrap_or(1 << 20);  // honor setting, default 1 MiB
let options = SnapshotOptions {
    base_ignores,
    progress: None,
    start_tracking_matcher: &everything,
    force_tracking_matcher: &nothing,
    max_new_file_size,
};
```
Keep the rest of `snapshot` (freshness check, off-GIL `locked_wc().snapshot`, clean-tree no-op)
untouched. `auto-track` stays the `EverythingMatcher` default **unless** verifying §5.3 shows it's
cheap to wire `snapshot.auto-track` into `start_tracking_matcher` — if not, flag it (the M2 slice-5
flag already names `snapshot.auto-track` as future work; leaving it is in-scope).

### 5.5 Differential tests (`tests/test_snapshot.py`, add cases + a fixture)
Add a fixture (or build inline) — a colocated repo with a `.gitignore` and a large file:
- **`test_snapshot_respects_gitignore_matches_cli`** *(headline)*: repo with `.gitignore`
  containing `ignored.txt`, then create both `ignored.txt` and `tracked.txt` on disk. Auto-snapshot
  via the binding vs `jj` snapshot on a copy. Assert `@`'s **tree id / commit id are identical** on
  both sides, and that `ignored.txt` is absent from `@`'s tree while `tracked.txt` is present. This
  is the test the whole slice exists for.
- **`test_snapshot_max_file_size_matches_cli`**: with `snapshot.max-new-file-size = "10KiB"` in the
  test config and a >10 KiB new file, assert the binding skips it exactly as the CLI does (same tree
  id). Drive the config through the same `JJ_CONFIG`/`write_config` mechanism the `jj` fixture uses
  so both sides read the same cap. **You will likely need to extend `tests/diff/jj_cli.py::write_config`
  to set `snapshot.max-new-file-size`** for this case.
- **Re-run the full suite**: this touches the shared snapshot path, so every M2 test that snapshots a
  dirty `@` must stay green. If anything regresses, the gitignore layering (§5.3) is wrong.

---

## 6. Build / verify / report (every slice)

```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- devenv tasks run pyjutsu:test
devenv shell -- devenv tasks run pyjutsu:lint     # ruff check + clippy -D warnings (NOT ruff format)
```
(Confirm the exact task names against `devenv.nix` first; the slice-11 guide used these.) Per slice:
build → full suite green → lint clean → commit on `main`. **No AI attribution** anywhere (commits,
PRs, comments, docs). Commit messages, one per slice, e.g.:
`Implement 0.4.0 slice 1: rebase -r/-b`, `… slice 2: git_push delete + multi`,
`… slice 3: git_fetch glob/negative patterns`, `… slice 4: snapshot fidelity (gitignore + max-file-size)`.

---

## 7. Version bump to 0.4.0 (after slice 4 lands, or stage minors)

These four are one milestone. Recommended: keep `0.3.0` through slices 1–3 (they're additive,
unreleased), then **bump `0.3.0 → 0.4.0` with slice 4** as the milestone close. (If you prefer to mark
progress, you may bump a patch per slice — but the milestone target is `0.4.0`.)
- `python/pyjutsu/__init__.py`: `__version__ = "0.4.0"` (leave `JJ_LIB_TARGET = "0.38.0"`).
- `Cargo.toml`: `version = "0.4.0"`; `pyproject.toml` if it carries a version.
- **Rebuild** so `Cargo.lock` / `uv.lock` refresh; **commit the lockfiles**.
- The `JJ_VERSION == JJ_LIB_TARGET` tripwire (`__init__.py:53`) stays green (pyjutsu's own version is
  independent of the jj-lib pin).
- Concept §12 scope: none of these were §12 "Later" items, so the §12 list is unchanged; if a doc
  tracks milestone state, note "0.4.0: rebase -r/-b, push delete/multi, fetch patterns, snapshot
  fidelity".
- **Update memory:** a note per slice (or one `0.4.0-refinements` note) recording what landed and any
  verification surprises (gitignore layering, the CLI delete-flag name, the fetch union/intersection
  algebra); flip `[[m2-slice5-snapshot]]`/`[[m2-slice8-rebase-squash-restore]]`/`[[m2-slice11-git-net]]`
  cross-links to "refined in 0.4.0".

---

## 8. Guardrails (carried; non-negotiable)

- **Thin Rust, rich Python.** Only dicts / scalars / `None` cross FFI. No jj-lib or gix type leaks.
- **GIL discipline.** Slice 1 is in-`Transaction` graph work → **on** the GIL. Slices 2–3 are
  subprocess + network → **off** the GIL, `!Send` types created and dropped in one closure on one
  thread. Slice 4's `snapshot` already runs off-GIL — keep it.
- **Differential, against the pinned `jj` 0.38.0 CLI only.** Reuse `JjCli` + `_copy_repo`; assert
  **commit/tree ids** where parity is exact (slices 1, 4) and **state** (refs present/absent, rows
  imported) where the CLI wraps extra ops (slices 2, 3). Assert the **binding** publishes exactly one
  op per verb; keep no-op detection tolerant if op-count exactness is fiddly.
- **Pins frozen:** `jj-lib =0.38.0`, `gix =0.78.0`, `JJ_LIB_TARGET =0.38.0`. `Cargo.lock` committed.
- **Everything through devenv** — never bare `cargo`/`maturin`/`python`/`pytest`/`jj`.
- **Faithful primitive, simplest form.** Implement exactly the four selected refinements; keep every
  other flag (interactive rebase/squash, tag fetch, `--all-remotes`, force-push, `auto-track`,
  workspace `-r`/sparse, revset builder, diffs, async) **flagged, not faked**.

> **Top traps (grepped against the pinned source while writing this guide; re-grep if doubted):**
> (1) **slice 1** — `MoveCommitsTarget::Commits` is `-r`, `Roots` is `-s` (rewrite.rs:535/537); `-b`
> needs the `roots(connected(onto..commit))` revset — ship `-r` first if `-b` is non-trivial.
> (2) **slice 2** — `BookmarkPushUpdate.new_target = None` is the delete; drop the `view` read borrow
> before `start_transaction`; delete doesn't require a local bookmark; **verify jj 0.38's delete CLI
> flag** for the oracle. (3) **slice 3** — `StringExpression` is a tree (`Pattern`/`NotIn`/`Union`/
> `Intersection`, str_util.rs:351); globs via `StringPattern::glob` (str_util.rs:141); **verify the
> union/intersection algebra against jj-cli** before trusting negatives. (4) **slice 4** — **no
> `SnapshotOptions::from_settings` in jj-lib**; replicate jj-cli's gitignore chain order (gitignore.rs:108)
> and hand-roll the byte-size parse; **this is the only slice that can move a commit id — fence it last,
> fixture it, verify tree-id parity**. See [[m2-slice5-snapshot]], [[m2-slice8-rebase-squash-restore]],
> [[m2-slice10-git-interop]], [[m2-slice11-git-net]], [[m2-transaction-not-send]].
