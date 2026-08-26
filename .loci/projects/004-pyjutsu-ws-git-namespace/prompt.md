# Implementation prompt — Phase D, the `ws.git` colocated namespace

Work in the Pyjutsu repository. Implement **Phase D** of
`.loci/projects/002-pyjutsu-refactor-jj044/IMPLEMENTATION_PLAN.md`, starting
with D1.

Read these first, completely, in this order:

1. `.loci/projects/002-pyjutsu-refactor-jj044/IMPLEMENTATION_PLAN.md` — Phase D is the scope.
2. `.loci/projects/004-pyjutsu-ws-git-namespace/project.md` — this project.
3. `.loci/projects/002-pyjutsu-refactor-jj044/COLOCATED_GIT_SURFACE.md` — why the namespace exists and where it stops.
4. `.loci/projects/002-pyjutsu-refactor-jj044/project.md` — the method, and what 0.17.0 already changed.
5. `AGENTS.md` and the full `.agents/skills/my-ai/SKILL.md`.

Do **not** implement Phase C. That is project 003.

## Objective

Gather the git half of a colocated repository under one namespace.

```text
D1 namespace scaffold      M   blocks every other lane
D2 annotated tags          S
D3 git config              S
D4 HEAD state              S
D5 git worktrees           S
D6 object access           S
D7 submodules              M   read-only
D8 reflog read             S
D9 git index read          S   read-only
```

Land D1 first. D2 through D9 are independent; land each as its own verified
lane.

## Baseline

The project starts from Pyjutsu 0.17.0 on `main`, binding jj-lib 0.44.0 and
gix 0.85.0, with a green gate: 7 Rust tests and 401 Python tests. Record the
numbers you actually observe before your first edit.

## Two facts that shape every lane

**Minimising gix call sites is not a goal.** gix already ships in every wheel
through jj-lib. The cost this project controls is application programming
interface *depth*: prefer the shallow, stable gix call over the low-level one.

**Declare every gix feature Pyjutsu itself calls.** jj-lib already enables
`attributes`, `blob-diff`, `index`, `max-performance-safe`, `sha1`, `sha256`,
and `zlib-rs`, and Cargo unifies features — but relying on that is the mistake
finding F1 recorded. Pyjutsu's own edge currently declares `sha1` and `sha256`.
D7 adds `attributes`; D9 adds `index`.

Do **not** enable the gix `revision`, `blame`, `status`, `dirwalk`, or network
features. The plan's D-reject table gives the reasons.

## What D1 and D2 must finish

Pyjutsu 0.17.0 ships `ws.create_tag(message=...)` with a `DeprecationWarning`
naming `ws.git.create_tag` — a path that does not exist yet. D1 creates the
namespace and D2 lands that verb. Until both land, the warning points at
nothing. Treat this as the first deliverable, not a later cleanup.

## Moves and aliases

D1 moves four verbs and keeps a deprecating alias for each:

| Today | Becomes |
|---|---|
| `ws.git_refs(prefix)` | `ws.git.refs(prefix)` |
| `ws.write_git_ref(name, target)` | `ws.git.write_ref(name, target)` |
| `ws.delete_git_ref(name)` | `ws.git.delete_ref(name)` |
| `ws.remotes()` | `ws.git.remotes()` |

`git_import`, `git_export`, `sync_colocated`, `git_fetch`, and `git_push` stay
on `Workspace`. They publish jj operations; they are not git-side reads.

`apply_head_ref_packed` keeps its current behaviour. It is the deep call site,
it survived the gix 0.84-to-0.85 port unchanged, and its doc comment explains
why it exists. Read that comment before you touch it.

## The gate

Every lane runs the full gate before it lands. No lane lands on a partial gate.

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
ruff check python tests scripts
pytest -q
devenv tasks run pyjutsu:verify
```

Run focused tests after each slice. Run the full gate at the end of each lane.

Every lane's oracle is the **`git` binary**, not `jj`. Each moved verb keeps
its existing test file, plus one test per alias asserting the
`DeprecationWarning`.

## Delivery

Land each lane separately. Never commit on a red gate.

1. Run the full gate. When it is green, commit the lane and push to `origin`.
2. Inspect the diff and the status output before each commit.
3. Append a dated entry to
   `.loci/projects/004-pyjutsu-ws-git-namespace/project.md` for each lane: what
   changed, the validation block, and every decision made.
4. Document every new verb in `docs/USER_GUIDE.md`.

## Non-goals

- Do not implement Phase C.
- Do not add submodule update, init, or clone. Listing and state only.
- Do not write the git index. jj-lib's `reset_head` owns index updates.
- Do not write to the global git configuration file. Repository-local only.
- Do not add network transport through gix. jj shells out to `git`; match that.
- Do not add `jj-cli` as a runtime dependency.
