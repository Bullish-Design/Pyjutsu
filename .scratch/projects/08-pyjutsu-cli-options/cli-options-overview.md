# Pyjutsu CLI — options overview

> **Status:** advisory / scoping report. Nothing here is committed work. Written against the
> surface as of pyjutsu **0.7.0** (`v0.7.0`, power-user surface: revset builder, streaming log,
> `run_jj` escape hatch). Authority for scope decisions remains `docs/PYJUTSU_CONCEPT.md` §12,
> which lists "CLI fallback backend" as a **Later** item and which 0.7.0 deliberately scoped to its
> minimal honest form (`run_jj`) only.

---

## 0. The question

What would it actually take to put a command-line interface on the pyjutsu backend, and what are
the real options? This report frames the option space, the work each implies, and the trade-offs —
so a future milestone can pick a tier deliberately rather than drift into reimplementing `jj`.

---

## 1. The core tension (read this first)

Pyjutsu's thesis is **import-only, typed, in-process, no subprocess, no text parsing**. A CLI is the
inverse contract: text arguments in, formatted text out, an OS process per invocation. So a CLI is
**not an extension of the core library** — it is a *separate consumer* of it.

Two consequences fall out of this immediately and shape every option below:

1. **A CLI belongs in its own package/module**, not in the import-only `pyjutsu` core. Anything else
   muddies the thesis (adds `argparse`/rendering/`[project.scripts]` to a library whose whole point
   is that it isn't those things). The natural home is a sibling `pyjutsu-cli` distribution (or a
   clearly-isolated `pyjutsu.cli` subpackage that the core never imports).
2. **The real `jj` CLI already exists** and is what pyjutsu *differential-tests against*. Any CLI we
   build is measured against it. The closer we try to match it cosmetically (graph, colors,
   interactive editing), the more we are reimplementing the `jj` crate — for a tool that already
   ships. The value of a pyjutsu CLI is therefore **not** "another `jj`"; it's a CLI that is
   *trivially scriptable/extensible in Python* and shares our typed core.

Keep both in mind: they're why the recommendation lands on a *thin* CLI in a *separate* package.

---

## 2. What already exists that a CLI can stand on

The 0.7.0 surface gives a CLI most of its verbs for free:

- **Frozen Pydantic models** (`Commit`, `Bookmark`, `Operation`, `Diff`, `DiffStat`, `Conflict`,
  `WorkspaceInfo`, `Remote`) — structured data that is trivial to format as text.
- **`Commit.parent_ids`** — enough to reconstruct the DAG **in Python** from a `log()` result (so a
  graph renderer doesn't strictly need new FFI; see §5).
- **`Workspace.transaction(...)` context manager** — atomicity/rollback already solved; a CLI
  command is just `with ws.transaction(desc) as tx: tx.<verb>(...)`.
- **Mutation verbs already bound:** `describe`, `new`, `edit`, `abandon`, `rebase`, `squash`,
  `restore`, and bookmark CRUD (`create`/`set`/`delete`/`track`/`untrack`).
- **Op log & history:** `operations()`, `undo()`, `restore_operation()`, `at_operation()`.
- **Git interop:** `git_import/export/fetch/push`, remotes CRUD, `git_clone`.
- **Workspace mgmt:** `add_workspace`, `forget_workspace`, `workspaces`, plus `snapshot`,
  `is_stale`, `update_stale`.
- **`PyjutsuError` hierarchy** → clean error→exit-code+hint mapping.
- **Revset builder** (`Revset`/`Pattern`) → could back nicer `--revset` ergonomics / completion.
- **`run_jj(...)`** → a ready-made fallback for any subcommand not yet wired (see Option E).

What a CLI must add on top is mostly **presentation + plumbing**, not new engine verbs.

---

## 3. Cross-cutting infrastructure (needed by *most* options)

Independent of which tier we pick, these are the recurring building blocks. Splitting them out here
so each option can just say "needs X, Y" rather than re-describe them.

| Block | What it is | Cost / layer |
|---|---|---|
| **Workspace discovery** | Walk up from `cwd` to find the `.jj` root (jj's behavior). `Workspace.load(path)` today takes a path — **must verify** whether it discovers or needs the exact root. | small; Python if `load` discovers, else a tiny FFI helper |
| **Command dispatch** | `typer`/`click`/`argparse` → method calls; help text, arg/flag parsing. | Python glue |
| **Output formatting** | Render models to text (one-line + verbose commit forms, bookmark/op/remote lists, status). | Python |
| **`$EDITOR` integration** | `describe`/`new` without `-m` opens an editor (tempfile + subprocess). | Python, easy |
| **Error presentation** | Map `PyjutsuError` subclasses → exit codes, messages, hints (immutable-commit, stale-wc, revset). | Python |
| **Snapshot wiring** | jj snapshots the working copy before each command; we have `snapshot()` + `auto_snapshot`. | Python |
| **Config** | jj reads layered config (`~/.jjconfig`, repo, `JJ_CONFIG`). We rely on `JJ_CONFIG` passthrough; no resolution surface or `config` commands. | Python (basic) / FFI (full) |
| **Colors / theming** | ANSI coloring, `--color=auto/always/never`, `NO_COLOR`. | Python |
| **Packaging** | `[project.scripts]` entry point, separate dist, completions. | Python |

---

## 4. The options

### Option A — Status quo: `run_jj` only (do nothing more)

**Description.** Ship no CLI. Users who want a command line use the real `jj` binary; pyjutsu stays
import-only, with `run_jj` as the labeled escape hatch for scripting unbound ops in-process.

- **Pros**
  - Zero new code, zero new maintenance, zero new differential surface.
  - Keeps the thesis pure; no contradiction to explain.
  - The real `jj` is already the best `jj` CLI.
- **Cons**
  - No Python-native CLI story; "dogfood as a daily driver" stays library-only.
  - `run_jj` is a subprocess to the *external* binary — no in-process advantage for CLI-shaped use.
- **Implications**
  - The differential test oracle (`jj`) and the user's CLI are the same tool — no drift risk.
- **Opportunities**
  - Frees effort for deepening the typed surface (the actual differentiator).

### Option B — Tier 1: thin, honest CLI over the typed surface *(recommended)*

**Description.** A separate `pyjutsu-cli` package: `typer`/`argparse` dispatch mapping subcommands
to the **existing** typed methods, plain (non-graph) text output, `$EDITOR` for messages, error→exit
mapping, workspace discovery, `[project.scripts]` entry point. Covers `status`, `log` (flat),
`show`, `describe`, `new`, `edit`, `abandon`, `rebase`, `squash`, `restore`, `bookmark *`, `git *`,
`op log`/`undo`, `diff`/`diff --stat` (from our structured hunks), `workspace *`.

- **Pros**
  - Mostly Python glue on top of work already done — realistically **one focused milestone**.
  - Keeps the core clean (lives in its own package; core never imports it).
  - Every command is a thin, readable wrapper → trivially scriptable and extensible in Python.
  - Differential-testable the same way the library is (compare to `jj` output where formats align).
- **Cons**
  - Won't *look* like `jj`: no DAG graph, no shortest-prefix id highlighting, no interactive editing.
  - Output format is ours, not byte-identical to `jj` (cosmetic divergence to document).
  - Yet another surface to keep green as the library evolves.
- **Implications**
  - Need a deliberate, documented output format (don't chase `jj` parity).
  - Gaps degrade *gracefully*: unbound/interactive ops point users at real `jj` (or Option E's
    fallback).
- **Opportunities**
  - The revset builder can power friendly `--revset` ergonomics and shell completion.
  - A natural place to showcase Pydantic models as `--format=json` output (a real edge over `jj`
    for scripting — structured, validated JSON straight from the typed core).
  - Establishes the packaging/entry-point pattern for any future tool.

### Option C — Tier 2: `jj`-parity CLI

**Description.** Everything in B plus the work to *feel native*: DAG graph log, shortest-unique
id-prefix highlighting, `--git`/color-words diff rendering, interactive `split`/`commit -i`/
`squash -i`, `config` commands, full theming, completions, man pages.

- **Pros**
  - A drop-in-ish `jj` experience backed by our engine.
- **Cons**
  - **Largely reimplements `jj`'s own CLI crate** — the exact thing we test against. High cost, low
    marginal value.
  - Requires **new FFI** for the hard bits (graph edges, id-prefix index, possibly word diff) — see
    §5 — and a real interactive diff editor / TUI.
  - Permanent, heavy maintenance burden tracking `jj`'s UX across versions.
- **Implications**
  - Multi-milestone effort; expands the differential surface enormously (now matching rendering, not
    just data).
- **Opportunities**
  - Only justified if pyjutsu ever intends to *replace* `jj` for end users (not the stated goal).

### Option D — TUI instead of (or before) a CLI

**Description.** Skip a `jj`-style CLI; build a terminal UI (Textual/`urwid`) over the typed surface
— an interactive log/diff/op-log browser with keybindings for common mutations.

- **Pros**
  - Plays to pyjutsu's strength (rich typed models → rich interactive views) instead of competing
    with `jj`'s text output.
  - Interactive editing (the expensive CLI gap) is *natural* in a TUI.
  - A genuinely differentiated artifact, not a second-rate `jj`.
- **Cons**
  - Bigger UX design surface; harder to differential-test (interactive, not text-in/text-out).
  - Different skill/effort profile than a CLI; arguably its own product.
- **Implications**
  - Still needs most of §3's blocks (discovery, formatting, errors) plus event loop / layout work.
- **Opportunities**
  - Could be the flagship "why pyjutsu" demo; pairs well with `--format=json` plumbing from B.

### Option E — Hybrid: thin CLI (B) with `run_jj` fallback for unbound commands

**Description.** Option B, but any subcommand not yet bound to the typed surface transparently
dispatches to `run_jj` (clearly indicated), so the CLI is *complete* from day one and gets *more
in-process* over time as verbs are bound.

- **Pros**
  - Full command coverage immediately; incremental migration of commands into the typed path.
  - Best practical "daily driver" story with the least up-front work.
- **Cons**
  - **Directly tensions the thesis**: a command that looks native may actually be shelling out +
    parsing nothing (raw passthrough) — exactly the "transparent auto-fallback" we flagged *out* of
    scope in 0.7.0 because it hides subprocess/text behind typed-looking methods.
  - Two execution models behind one surface → confusing fidelity guarantees (in-process vs external
    binary, version skew per `run_jj`'s caveat).
- **Implications**
  - If pursued, the fallback **must be loud** (banner/flag like `--via-jj`), never silent, to stay
    honest.
- **Opportunities**
  - A pragmatic bootstrap: ship E, then "graduate" commands from fallback to typed, tracking
    coverage as a metric.

### Option F — Rendering/format library only (no CLI)

**Description.** Don't ship a CLI; instead expose a small, optional **formatting layer** (graph
construction from `parent_ids`, commit one-line/verbose renderers, JSON serialization) that *others*
(including a future CLI or TUI) build on.

- **Pros**
  - Keeps pyjutsu import-only while removing the main thing every CLI/TUI would re-derive.
  - Lowest commitment; unblocks B/D/E later without prejudging which.
- **Cons**
  - Not a user-facing CLI; doesn't itself satisfy "daily driver."
  - Risk of designing a renderer with no concrete consumer (speculative generality).
- **Implications**
  - Best done *with* a first consumer (i.e. alongside B or D), not purely speculatively.
- **Opportunities**
  - The JSON/`model_dump` path is independently valuable for scripting regardless of any CLI.

---

## 5. The genuine backend gaps (new FFI) vs. pure-Python glue

The single most useful distinction for planning: **what actually needs Rust**, versus what is just
Python on top of the existing surface.

| Capability | Needs new FFI? | Notes |
|---|---|---|
| Command dispatch, help, flags | No | pure Python (`typer`/`argparse`) |
| Flat `log`, `show`, lists, `status` | No | format existing models |
| `$EDITOR` for messages | No | tempfile + subprocess |
| Error → exit code + hints | No | map `PyjutsuError` hierarchy |
| DAG **graph** log (`@ ○ │ ╮`, elision) | **Borderline** | constructible in Python from `Commit.parent_ids` over a `log()` set; *matching jj's exact glyphs/edge-elision* (missing/indirect edges via jj-lib's `RevsetGraphIterator`) would want FFI — **verify** |
| Shortest-unique **id-prefix** highlighting | **Yes** | needs jj-lib `IdPrefixContext`/index query; not currently bound. Can approximate poorly without it |
| `--git` text diff rendering | No (mostly) | we expose structured hunks (`Diff`/`Hunk`); rendering git-format text is Python. Byte-exact match is fiddly |
| Word/inline (color-words) diff | **Yes** | explicitly flagged out of scope; needs engine-side word diff |
| Interactive `split`/`commit -i`/`squash -i` | **Yes** (big) | needs a diff editor / TUI + selection-applying transaction support |
| Layered config resolution / `config` cmds | **Yes** (for full) | basic env passthrough works today; full resolution is unexposed |
| Colors / completion / packaging | No | pure Python |

**Takeaway:** a Tier-1 CLI (Option B) needs *no* new FFI — only workspace-discovery verification.
The two backend bits worth a *small, optional* FFI slice if you want it to feel native are **graph
edges** and **id-prefix**. Everything genuinely expensive (interactive editing, word diff, full
config) is already flagged out of scope and can stay there.

---

## 6. Recommendation

For the stated goal — *dogfood pyjutsu as a daily driver* — **Option B (Tier-1 thin CLI in a
separate `pyjutsu-cli` package)** is the sweet spot:

- mostly Python glue over work already done (~one focused milestone),
- keeps the import-only core clean,
- gaps degrade gracefully ("use real `jj` for graph/interactive"), and
- the `--format=json` path is a real, honest edge over `jj` for scripting.

Add a **small optional FFI slice** later for **graph edges + id-prefix** *only if* the flat log
proves too austere in daily use. **Avoid Option C** (reimplements `jj`). Treat **Option E** with
caution: only with a *loud, never-silent* fallback, or it relitigates the transparent-fallback
decision 0.7.0 explicitly made. **Option D (TUI)** is the most *differentiated* artifact if the
appetite is for something that isn't "another `jj`" — worth considering as an alternative flagship
rather than a follow-on.

Suggested sequence if B is chosen: **(1)** verify workspace discovery + lock the output format and
`--format=json`; **(2)** read commands (`status`/`log`/`show`/`diff`) + error mapping + packaging;
**(3)** mutation commands (`describe`/`new`/`edit`/`abandon`/`rebase`/`squash`/`bookmark`) via the
transaction CM + `$EDITOR`; **(4)** git/op/workspace commands. Each slice differential-tested where
formats align, exactly as the library milestones were.

---

## 7. Open questions to verify before scoping a build

1. **Does `Workspace.load(path)` discover the `.jj` root** by walking up from `path`, or does it
   require the exact workspace root? Determines whether discovery is free or a tiny FFI helper.
2. **Does jj-lib 0.38 expose a graph iterator** (`RevsetGraphIterator` or equivalent) cheaply enough
   to bind for edge-accurate graph rendering, or do we settle for a Python-side graph from
   `parent_ids` (correct topology, approximate glyphs)?
3. **Is `IdPrefixContext`** (shortest-unique id prefix) reasonable to bind as a read, and is the
   highlighting worth it for a Tier-1 CLI?
4. **Packaging shape:** separate `pyjutsu-cli` distribution vs. an optional `pyjutsu[cli]` extra with
   an isolated `pyjutsu.cli` module the core never imports?
5. **`--format=json` contract:** do we commit to model `model_dump(mode="json")` as a stable
   machine-readable output (and version it), given it's a genuine differentiator?
