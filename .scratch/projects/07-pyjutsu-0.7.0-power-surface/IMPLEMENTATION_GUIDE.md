# Pyjutsu 0.7.0 — Power-user surface Implementation Guide (3 slices)

> **Authority:** `docs/PYJUTSU_CONCEPT.md` (canonical spec; §5 lists a *revset builder* and §12
> "Later" lists **revset builder, streaming/iterator log, CLI fallback backend, async facade**) →
> `.scratch/projects/06-pyjutsu-0.6.0-diff-surface/IMPLEMENTATION_GUIDE.md` (the parent milestone
> whose slice/verify discipline this mirrors) → **this document** (the verified plan for 0.7.0) →
> the code it produces.
>
> **Pins unchanged:** `jj-lib = "=0.38.0"` (default features include `git`); `gix = "=0.78.0"`.
> `JJ_LIB_TARGET` stays `"0.38.0"`. Pyjutsu uses **independent semver**: this milestone bumps
> `0.6.0 → 0.7.0` (see §6). **Start from a clean `main` with 0.6.0 landed @ `v0.6.0`.** Every jj-lib
> API ref below is `file:line` into
> `~/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/jj-lib-0.38.0/src/`, **verified against the
> pinned source while writing this guide** (2026-06-17). Facts still needing runtime confirmation
> against the CLI are marked **VERIFY**.

---

## 0. What this milestone is, and why now

Through 0.6.0 the *entire concept §5 v1 surface* is implemented and green (reads incl. `diff`,
transactions, op log, git interop, workspaces). 0.7.0 pulls three items out of the concept's
**"Later"** bucket that make the library pleasant and complete to **dogfood as a daily driver**:

| Slice | Item | Nature | jj-lib API status |
|---|---|---|---|
| **1** | **Revset builder** — a typed `Revset`/`Pattern` API that renders to jj revset strings | **pure Python**, no FFI | n/a — renders strings the existing `revset::evaluate` (src/revset.rs:29) parses; escaping mirrors jj's `escape_string` (dsl_util.rs:440) |
| **2** | **Streaming log** — a lazy `Iterator[Commit]` for huge histories | **thin FFI** (one new `#[pyclass]` iterator) | clean — `Revset::iter()` yields `Result<CommitId, _>` (revset.rs:3375); reuse `CommitData::build` (repo_view.rs:77) |
| **3** | **`run_jj(...)` escape hatch** — run the pinned `jj` binary for ops not yet bound | **pure Python** (subprocess) | n/a — shells out; **explicitly does not parse output into models** |

**Two decisions already taken (do not relitigate):**
- **Async facade is DEFERRED, not built.** Every binding method already releases the GIL
  (`py.allow_threads`), so callers get non-blocking behavior + concurrency today via
  `await asyncio.to_thread(ws.git_fetch, "origin")`. A true async facade would have to duplicate
  every method *and* solve the `!Send` `Transaction`/`MutableRepo` problem ([[m2-transaction-not-send]])
  with a pinned single-thread executor — high effort, low marginal value given `to_thread`. **This
  milestone instead documents the `asyncio.to_thread` pattern** (README + a short note in the
  `Transaction` / `Workspace` docstrings). No async code.
- **"CLI fallback" = the minimal `run_jj` escape hatch, NOT a CLI app and NOT a second backend.**
  Pyjutsu ships no CLI (`import`-only; no `[project.scripts]`, no `argparse`). Slice 3 is a deliberate,
  clearly-labeled *exit* from the typed in-process surface for things not yet bound — it returns raw
  text/exit code and parses nothing. **Out of scope (keep flagged):** a transparent auto-fallback
  behind typed methods (would hide subprocess + text-parsing, contradicting the thesis), a whole
  subprocess-backed reimplementation of the surface, and shipping a standalone `pyjutsu` CLI app.

**Sequencing.** Slice 1 first (pure Python, lowest risk, establishes `Revset`/`Pattern` and the
`str | Revset` coercion every read accepts); slice 2 next (the one bit of new FFI); slice 3 last
(self-contained subprocess helper). Each slice is independently committable and must leave the full
suite green before the next.

**Explicitly still out of scope (do NOT implement; keep flagged):** two-revset `diff(from, to)`,
word/inline diff, `--git` rendering in Rust, context-line options, async (see above); and the whole
carried backlog — force-push, `--change` push, tag fetch, `--all-remotes`, interactive squash,
sparse/`-r` workspace, native-backend polish, Windows.

---

## 1. Carried structural facts (true for every slice; re-verify, don't assume)

- **Thin Rust, rich Python.** `_pyjutsu` returns opaque dicts / scalars / `None` only. **No jj-lib
  type crosses the FFI.** Models and ergonomics live in pure-Python `pyjutsu` (this milestone adds
  the `Revset`/`Pattern` builder and a `JjResult` model, all pure Python; only slice 2 touches Rust).
- **Revsets funnel through one evaluator.** `src/revset.rs::evaluate` (revset.rs:29) is the single
  parse→resolve→evaluate path for `resolve`/`log`/`conflicts`/`diff_stat`/`diff`. It iterates
  `revset.iter().commits(repo.store())` (revset.rs:70). The builder produces the **string** this
  parses; streaming reuses the **iter** behind it. **The builder changes no Rust.**
- **The model-build pattern.** `CommitData::build(repo, &commit)` (used at repo_view.rs:77,102) turns
  one `jj_lib::commit::Commit` into the plain `CommitData`; `repo.store().get_commit(&id)`
  (repo_view.rs:101) fetches a commit by id. `eval_to_data` (repo_view.rs:57) is the off-GIL
  evaluate-then-build pattern; `limit` truncates before the build (repo_view.rs:73).
- **Off the GIL.** Reads hold `Arc<ReadonlyRepo>` (`Send + Sync`); wrap backend-touching work in
  `py.allow_threads(...)` exactly as the existing reads do. (No `pollster`/async needed for slices 1–3;
  the revset iter and `get_commit` are sync.)
- **Differential oracle = the pinned `jj` 0.38.0 CLI**, via `tests/diff/jj_cli.py::JjCli` against the
  fixtures in `tests/conftest.py` (`linear_repo`, `diffstat_repo`, `scratch_repo`, …). Reuse
  `JjCli.change_ids` (jj_cli.py:80) for log-order parity. **Everything runs through devenv** — never
  bare `cargo`/`maturin`/`pytest`/`jj`.
- **Build/verify per slice:** `devenv shell -- devenv tasks run pyjutsu:{build,test,lint}` (`build` =
  `maturin develop --uv`, `test` = `pytest -q && cargo test`, `lint` = `ruff check python tests &&
  cargo clippy --all-targets -- -D warnings`). The devenv-task stdout is swallowed; to see results run
  the underlying command inside the shell, e.g. `devenv shell -- bash -c 'python -m pytest -q'`.
  **No AI attribution** anywhere.

---

## 2. Slice 1 — Revset builder (pure Python)

### 2.1 What it is
A typed, composable Python API that **renders to a jj revset string** — never evaluates anything
itself. It is sugar over the existing string path: `ws.log(R.author("alice") & R.description("fix"))`
renders `(author(substring:"alice") & description(substring:"fix"))` and feeds the same
`revset::evaluate`. Power users keep full revset power; the builder just removes f-string quoting
hazards and adds discoverability (concept §5: "a `Revset` builder is a later nicety").

New module `python/pyjutsu/revset.py` exporting **`Revset`** and **`Pattern`**.

### 2.2 The `Revset` type
Immutable wrapper around a rendered, **already-parenthesis-safe** fragment:

```python
class Revset:
    __slots__ = ("_expr",)
    def __init__(self, expr: str) -> None: self._expr = expr
    @property
    def expr(self) -> str: return self._expr
    def __str__(self) -> str: return self._expr
    def __repr__(self) -> str: return f"Revset({self._expr!r})"
```

**Combinators (operators)** — jj operator set + precedence (revset language). Always wrap each
operand in parens on combination so precedence is never wrong (over-parenthesizing is harmless and
keeps the renderer trivial):

| Python | Renders | jj meaning |
|---|---|---|
| `a & b` | `(a & b)` | intersection |
| `a \| b` | `(a \| b)` | union |
| `a - b` | `(a ~ b)` | difference (jj spells it `~`, infix) |
| `~a` | `(~a)` | negation (complement) |
| `a.range(b)` | `(a..b)` | `a..b` (ancestors of b not of a) |
| `a.dag_range(b)` | `(a::b)` | `a::b` (DAG range) |
| `a.ancestors()` | `(::a)` | `::a` |
| `a.descendants()` | `(a::)` | `a::` |

**Constructors** — module-level functions / `Revset` classmethods. Functions taking text accept
`str | Pattern` (a bare `str` → `substring` to match jj's default for `author`/`description`):

```python
def raw(expr: str) -> Revset            # escape hatch: caller-supplied literal revset fragment
def all_() -> Revset                    # all()
def root() -> Revset                    # root()
def working_copy() -> Revset            # @
def commit(id: str) -> Revset           # a commit/change id or symbol, used verbatim (validate non-empty)
def bookmark(name: str) -> Revset       # bookmarks(exact:"<name>")  — precise, escaped
def author(text: str | Pattern) -> Revset        # author(<pattern>)
def description(text: str | Pattern) -> Revset    # description(<pattern>)
def committer(text: str | Pattern) -> Revset
def bookmarks(text: str | Pattern | None = None) -> Revset   # bookmarks() / bookmarks(<pattern>)
def tags() -> Revset
def heads(x: Revset) -> Revset          # heads(<x>)
def roots(x: Revset) -> Revset
def parents(x: Revset) -> Revset        # x-  (or parents(x)); prefer parents(<x>) function form
def children(x: Revset) -> Revset
def latest(x: Revset, count: int | None = None) -> Revset
```

Keep the constructor set **small but representative** — these cover the common dogfood queries; more
can be added later, and `raw(...)` is always available for anything unbound. Each function that names
a symbol or pattern **escapes** its argument (§2.4).

### 2.3 The `Pattern` type (jj string patterns)
jj filters take a *string pattern* `kind:"value"` (str_util.rs:172). Mirror the kinds exactly:

```python
class Pattern:
    # kinds verified at str_util.rs:172 — exact, exact-i, substring, substring-i, glob, glob-i, regex, regex-i
    @classmethod
    def exact(cls, value: str) -> "Pattern": ...
    @classmethod
    def substring(cls, value: str) -> "Pattern": ...
    @classmethod
    def glob(cls, value: str) -> "Pattern": ...
    @classmethod
    def regex(cls, value: str) -> "Pattern": ...
    # + *_i case-insensitive variants (exact_i/substring_i/glob_i/regex_i)
    def render(self) -> str:  # -> 'kind:"<escaped value>"'
        return f'{self._kind}:{_quote(self._value)}'
```

A bare `str` passed to a filter constructor is coerced to `Pattern.substring(s)` (jj's default in our
`use_glob_by_default=false` context, revset.rs:55). Document this so `author("alice")` ≡
`author(substring:"alice")`, and tell users to pass `Pattern.exact(...)` for exactness.

### 2.4 String quoting — mirror jj's `escape_string` EXACTLY (dsl_util.rs:440)
The renderer must quote values so the rendered string parses to the *same* value jj would. jj's rule
(verified dsl_util.rs:440–458):

```python
def _quote(s: str) -> str:
    out = ['"']
    for c in s:
        if c == '"':   out.append('\\"')
        elif c == '\\': out.append('\\\\')
        elif c == '\t': out.append('\\t')
        elif c == '\r': out.append('\\r')
        elif c == '\n': out.append('\\n')
        elif c == '\0': out.append('\\0')
        elif c.isascii() and (ord(c) < 0x20 or ord(c) == 0x7f):
            out.append(f'\\x{ord(c):02x}')      # mirrors Rust ascii::escape_default for controls
        else:           out.append(c)            # printable + all non-ASCII pass through verbatim
    out.append('"')
    return "".join(out)
```

> **VERIFY** the control-char branch against jj once at runtime: `ws.log(R.description("a\tb"))`
> should equal `ws.log("description(substring:\"a\\tb\")")` (both resolve identically). The named
> escapes (`\t\r\n\0`) and `"`/`\\` are the cases that actually matter for dogfooding; the generic
> `\xNN` control path is rarely hit — if jj's exact byte form ever differs for an odd control char,
> that's the line to re-check (it does not affect correctness of parsing, only string equality of the
> rendered form).

### 2.5 Wiring `str | Revset` into every read
Add a tiny coercion at the facade boundary (in `repo_view.py` and `workspace.py`); the native layer
still receives a `str`:

```python
from .revset import Revset
def _revset_str(revset: str | Revset) -> str:
    return revset.expr if isinstance(revset, Revset) else revset
```

Widen the type of `revset` to `str | Revset` on **`RepoView.resolve/log/diff_stat/diff/conflicts`**
and their `Workspace` delegators, calling `_revset_str(...)` before handing to `self._handle.*`. **No
`.pyi` change** (the native methods still take `str`). Export `Revset` and `Pattern` from
`__init__.py`.

### 2.6 Differential tests (`tests/test_revset_builder.py`, new)
The builder is correct iff *rendered string ≡ hand-written string* and *the binding result ≡ the CLI*.
- **`test_render_matches_handwritten`** *(pure, no repo)*: assert `.expr` for a spread of builder
  expressions equals the literal jj strings (e.g. `(R.author("a") & ~R.description(Pattern.glob("x*"))).expr
  == '(author(substring:"a") & (~description(glob:"x*")))'`).
- **`test_quote_escapes`** *(pure)*: `_quote('he said "hi"\n\\')` → `'"he said \\"hi\\"\\n\\\\"'`.
- **`test_builder_equals_string_query`** *(differential vs binding)*: on `linear_repo`
  (conftest.py:40 — commits A/B/C under empty `@`), assert
  `ws.log(R.description(Pattern.glob("commit *"))) == ws.log('description(glob:"commit *")')` and a few
  more, comparing the returned `Commit` lists.
- **`test_builder_equals_cli`** *(differential vs `jj`)*: assert
  `[c.change_id for c in ws.log(R.range(R.root(), R.working_copy()))]` equals
  `jj.change_ids(repo, "root()..@")`.
- **`test_raw_escape_hatch`**: `R.raw("trunk()..@").expr == "trunk()..@"` and evaluates equal to the
  string form.

---

## 3. Slice 2 — Streaming log (lazy `Iterator[Commit]`)

### 3.1 What it is
A lazy iterator over a revset's commits for huge histories, so you don't materialize the whole
`list[Commit]` at once. **Design that avoids self-referential lifetimes** (the revset borrows the
repo, its iter borrows the revset — un-storable in a `#[pyclass]`): **evaluate the revset to a
`Vec<CommitId>` eagerly** (cheap, bounded — ids only, off the GIL) and **build the expensive `Commit`
model one at a time per `__next__`**. The scale win is real: the costly per-commit backend reads
(commit object, signatures, bookmarks via `CommitData::build`) are deferred and streamed, and the
caller can process-and-discard rather than holding N models.

> This is *iterator* streaming, not lazy *evaluation* of the revset; the id list is fully realized
> first. That is the right trade for jj (ids are small; `CommitData::build` is the cost) and sidesteps
> `ouroboros`/self-ref entirely. True lazy revset evaluation stays flagged.

### 3.2 jj-lib APIs (verified)
| What | Signature / fact | Ref |
|---|---|---|
| Revset id iterator | `Revset::iter<'a>(&self) -> Box<dyn Iterator<Item = Result<CommitId, RevsetEvaluationError>> + 'a>` | revset.rs:3375 |
| Commit by id | `Store::get_commit(&CommitId) -> BackendResult<Commit>` (already used) | repo_view.rs:101 |
| Build the model | `CommitData::build(repo, &Commit) -> Result<CommitData, PyErr>` | repo_view.rs:77 |
| Map errors | `map_revset_err` / `map_backend_err` | src/errors.rs |

### 3.3 Rust: `evaluate_ids` + `PyCommitStream`
**`src/revset.rs`** — add a sibling to `evaluate` that stops at ids (don't build commits):
```rust
pub(crate) fn evaluate_ids(
    repo: &dyn Repo, revset_str: &str, workspace_name: &WorkspaceName,
    workspace_root: &Path, user_email: &str,
) -> Result<Vec<CommitId>, PyErr> {
    // …identical parse/resolve/evaluate as `evaluate` up to `let revset = resolved.evaluate(repo)?;`…
    let mut ids = Vec::new();
    for id in revset.iter() { ids.push(id.map_err(map_revset_err)?); }
    Ok(ids)
}
```
Factor the shared parse/resolve/evaluate prefix so `evaluate` and `evaluate_ids` don't drift (e.g. a
private `fn evaluate_revset(...) -> Result<Box<dyn Revset>, PyErr>` both call, then one does
`.iter().commits(store)` and the other `.iter()`).

**`src/repo_view.rs`** (or a small new `src/commit_stream.rs`) — a `Send` iterator pyclass holding the
`Arc<ReadonlyRepo>` + the id vec + a cursor:
```rust
#[pyclass(module = "pyjutsu._pyjutsu")]
pub(crate) struct PyCommitStream { repo: Arc<ReadonlyRepo>, ids: Vec<CommitId>, pos: usize }

#[pymethods]
impl PyCommitStream {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> { slf }
    fn __next__<'py>(&mut self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
        if self.pos >= self.ids.len() { return Ok(None); }     // Ok(None) ⇒ StopIteration
        let id = self.ids[self.pos].clone();
        self.pos += 1;
        let data = py.allow_threads(|| {
            let repo = self.repo.as_ref();
            let commit = repo.store().get_commit(&id).map_err(map_backend_err)?;
            CommitData::build(repo, &commit)
        })?;
        Ok(Some(data.to_dict(py)?))
    }
}
```
`PyRepoView::log_stream` mirrors `log` but stops at ids and hands them to the stream:
```rust
#[pyo3(signature = (revset_str, limit=None))]
fn log_stream(&self, py: Python<'_>, revset_str: &str, limit: Option<usize>) -> PyResult<PyCommitStream> {
    let ids = py.allow_threads(|| -> PyResult<Vec<CommitId>> {
        let mut ids = revset::evaluate_ids(self.repo.as_ref(), revset_str,
            &self.workspace_name, &self.workspace_root, &self.user_email)?;
        if let Some(n) = limit { ids.truncate(n); }
        Ok(ids)
    })?;
    Ok(PyCommitStream { repo: self.repo.clone(), ids, pos: 0 })
}
```
Register `PyCommitStream` in `lib.rs` (`m.add_class::<PyCommitStream>()?;`). Imports: `CommitId`
(`jj_lib::backend::CommitId`), `Arc<ReadonlyRepo>` (already in scope).

### 3.4 `.pyi` + Python facade
- **`.pyi`**: add `class PyCommitStream:` with `def __iter__(self) -> PyCommitStream: ...` and
  `def __next__(self) -> dict[str, object]: ...`; add `def log_stream(self, revset_str: str, limit:
  int | None = ...) -> PyCommitStream: ...` to `PyRepoView`.
- **`RepoView.iter_log`** (`python/pyjutsu/repo_view.py`):
  ```python
  def iter_log(self, revset: str | Revset, limit: int | None = None) -> Iterator[Commit]:
      """Lazily yield the revset's commits as validated models (for huge histories).

      Same commits/order as :meth:`log`, but builds one model at a time instead of a whole list.
      """
      for row in self._handle.log_stream(_revset_str(revset), limit):
          yield Commit.model_validate(row)
  ```
  Add `Workspace.iter_log` delegating to `self.head().iter_log(...)`.

### 3.5 Differential tests (`tests/test_stream_log.py`, new)
- **`test_iter_log_matches_log`** *(headline)*: on `linear_repo`, for several revsets
  (`"::@"`, `"all()"`, `"root()..@"`), assert `list(ws.iter_log(r)) == ws.log(r)` (same `Commit`
  models, same order).
- **`test_iter_log_matches_cli_order`**: `[c.change_id for c in ws.iter_log("::@")] ==
  jj.change_ids(repo, "::@")`.
- **`test_iter_log_limit`**: `list(ws.iter_log("all()", limit=2))` has length 2 and equals
  `ws.log("all()", limit=2)`.
- **`test_iter_log_is_lazy_iterator`**: `it = ws.iter_log("all()"); next(it)` returns a `Commit`;
  assert `iter(it) is it` (it's a one-shot iterator) and that exhausting it raises `StopIteration`.
- **`test_iter_log_accepts_builder`**: `list(ws.iter_log(R.all_())) == ws.log("all()")` (slice-1 glue).

---

## 4. Slice 3 — `run_jj(...)` escape hatch (pure Python subprocess)

### 4.1 What it is
A deliberate, clearly-labeled exit from the typed in-process surface: run the **pinned `jj` binary**
against this workspace for operations pyjutsu doesn't (yet) bind, returning **raw** stdout/stderr/exit.
It parses nothing into models — that is the whole point (the typed surface stays pure; this is the
"you are leaving it" valve). Concept §12 "CLI fallback backend", scoped to its minimal honest form.

```python
class JjResult(BaseModel):                 # python/pyjutsu/models.py
    model_config = ConfigDict(frozen=True, extra="forbid")
    args: list[str]                        # the jj args run (without the leading "jj")
    returncode: int
    stdout: str
    stderr: str

# python/pyjutsu/workspace.py
def run_jj(self, args: Sequence[str], *, check: bool = True,
           input: str | None = None, jj_binary: str | None = None) -> JjResult:
    ...
```

### 4.2 Design (pure Python — no Rust)
- **cwd** = `self.root()` (workspace.py:260); **env** = inherited `os.environ` (so `JJ_CONFIG` and the
  rest flow through exactly as the test harness relies on, conftest.py:26).
- **Binary resolution**, in order: explicit `jj_binary` arg → `PYJUTSU_JJ` env var →
  `shutil.which("jj")`. If none found, raise `JjCliError` with a clear "jj not on PATH" message.
- **`check=True`** (default) raises **`JjCliError`** (new `PyjutsuError` subclass in `errors.py`,
  pure Python — carries `args`, `returncode`, `stdout`, `stderr`) on non-zero exit; `check=False`
  returns the `JjResult` regardless.
- `subprocess.run([binary, *args], cwd=root, env=os.environ, capture_output=True, text=True,
  input=input)`.
- **No shell.** Never `shell=True`; args are a list. Document that values are passed verbatim (no shell
  interpolation), which is the safe default.

### 4.3 The version caveat (document prominently — docstring + README)
Unlike the rest of pyjutsu, `run_jj` depends on an **external `jj` binary on PATH**, which the library
cannot guarantee is the version it links (`pyjutsu.JJ_LIB_TARGET == "0.38.0"`). State in the docstring:
"requires a `jj` binary on PATH; for fidelity it should match `pyjutsu.JJ_LIB_TARGET`. This is an
escape hatch, not part of the in-process guarantee." **Do not** auto-run `jj --version` on every call
(an extra subprocess per call); optionally offer a one-off `Workspace.jj_version() -> str` helper that
runs `jj --version` for callers who want to assert the match themselves. (Optional; ship only if
trivial.)

### 4.4 Differential tests (`tests/test_run_jj.py`, new)
- **`test_run_jj_reads_back`** *(headline)*: on `scratch_repo` (conftest.py:31), 
  `ws.run_jj(["log", "-r", "@", "--no-graph", "-T", "change_id"]).stdout.strip()` equals
  `ws.working_copy().change_id` (escape hatch agrees with the typed read).
- **`test_run_jj_check_raises`**: `ws.run_jj(["definitely-not-a-jj-command"])` raises `JjCliError`;
  the raised error carries a non-zero `returncode` and the stderr.
- **`test_run_jj_no_check_returns`**: same bad command with `check=False` returns a `JjResult` with
  `returncode != 0` and **does not** raise.
- **`test_run_jj_mutation_visible`**: `ws.run_jj(["describe", "-m", "via cli"])` then a fresh
  `Workspace.load(repo).working_copy().description == "via cli"` — proves the subprocess mutated the
  same repo and the typed surface sees it. (The pinned `jj` is already on PATH inside devenv; `JJ_CONFIG`
  is set by the `jj` fixture, conftest.py:26, so identities/timestamps match.)
- **`test_run_jj_binary_missing`**: with `jj_binary="/nonexistent/jj"`, raises `JjCliError`.

---

## 5. Async note (no code — just docs)
Add a short **"Async usage"** subsection to the README and a one-line pointer in the `Workspace`
docstring: all methods release the GIL, so in an asyncio app wrap calls in `asyncio.to_thread(...)`
(e.g. `await asyncio.to_thread(ws.git_fetch, "origin")`); a native async facade is intentionally not
provided (the `!Send` transaction model makes it costly for little gain over `to_thread`). This is the
milestone's entire async deliverable.

---

## 6. Build / verify / report (every slice)
```
devenv shell -- devenv tasks run pyjutsu:build
devenv shell -- bash -c 'python -m pytest -q'        # task stdout is swallowed; run pytest directly to see it
devenv shell -- bash -c 'cargo test'
devenv shell -- bash -c 'ruff check python tests && cargo clippy --all-targets -- -D warnings'
```
Per slice: build → full suite green → lint clean → commit on `main`. **No AI attribution.** Commit
messages, one per slice:
`Implement 0.7.0 slice 1: revset builder`,
`Implement 0.7.0 slice 2: streaming (iterator) log`,
`Implement 0.7.0 slice 3: run_jj escape hatch + async docs`.

---

## 7. Version bump to 0.7.0 (after the last slice lands)
- `python/pyjutsu/__init__.py`: `__version__ = "0.7.0"` (leave `JJ_LIB_TARGET = "0.38.0"`); export the
  new public names (`Revset`, `Pattern`, `JjResult`, `JjCliError`).
- `Cargo.toml` + `pyproject.toml`: `version = "0.7.0"`.
- **Rebuild** so `Cargo.lock` / `uv.lock` refresh; **commit the lockfiles** (diff should be version-only).
- Tag `v0.7.0` (annotated, message `pyjutsu 0.7.0 — power-user surface (revset builder + streaming log
  + run_jj escape hatch)`); push `main` + tag.
- **Update memory:** new `[[pyjutsu-0-7-0-power-surface]]` recording the `Revset`/`Pattern` builder
  (escaping mirrors jj `escape_string`), the streaming-log design (eager ids → lazy model build,
  `PyCommitStream`), the `run_jj` escape hatch (pure Python, no parsing, external-binary caveat), and
  that async stayed deferred (`to_thread` documented). Refresh the **stale README** (it still says
  "M1 complete; mutations not implemented" — wrong since 0.3.0) to current status as part of this
  milestone.

---

## 8. Guardrails (carried; non-negotiable)
- **Thin Rust, rich Python.** Only dicts / scalars / `None` cross FFI. Slices 1 & 3 are pure Python;
  slice 2 adds exactly one `#[pyclass]` iterator returning dicts.
- **The builder evaluates nothing** — it renders strings the existing evaluator parses; escaping
  mirrors jj's `escape_string` (dsl_util.rs:440) so a rendered query ≡ the hand-written one.
- **`run_jj` parses nothing** and is clearly labeled an escape hatch; it is NOT a transparent fallback,
  NOT a second backend, NOT a shipped CLI app (all flagged out of scope).
- **Streaming is iterator-streaming** (eager ids, lazy model build) — no self-referential lifetimes,
  no `ouroboros`; true lazy revset evaluation stays flagged.
- **Async stays deferred** — document `asyncio.to_thread`; write no async code.
- **Differential, against the pinned `jj` 0.38.0 CLI only.** Builder: rendered-string + result parity;
  stream: `iter_log == log == CLI order`; `run_jj`: read-back parity.
- **Pins frozen:** `jj-lib =0.38.0`, `gix =0.78.0`, `JJ_LIB_TARGET =0.38.0`. `Cargo.lock` committed.
- **Independent semver:** this is `0.7.0`, not a jj-aligned number.

> **Top traps:** (1) **slice 1** — get the quoting EXACTLY right (dsl_util.rs:440): `"`→`\"`,
> `\`→`\\`, `\t\r\n\0` named, other ascii-control → `\xNN`, everything else verbatim; default a bare
> filter `str` to `substring` (revset.rs:55 has `use_glob_by_default=false`). Over-parenthesize
> combinators — never emit a precedence-ambiguous fragment. (2) **slice 2** — `Revset::iter()` yields
> `Result<CommitId, _>` (revset.rs:3375); collect **ids** eagerly (don't hold the revset/iter — they
> borrow the repo), build models per `__next__` off the GIL; `__next__` returning `Ok(None)` is
> StopIteration; register `PyCommitStream` in `lib.rs`. (3) **slice 3** — inherit `os.environ` (so
> `JJ_CONFIG` flows, conftest.py:26), cwd = workspace root, never `shell=True`, raise `JjCliError` on
> non-zero when `check`; document the external-binary/version caveat. See
> [[pyjutsu-0-6-0-diff-surface]] (parent milestone), [[m2-transaction-not-send]] (why async is
> deferred), and `src/revset.rs` (the evaluator both slice 1 and 2 build on).
