---
name: copyroom
description: Entry point for all CopyRoom work — scaffolding a repo from a template, template drift and updates, and adopting an existing repo. Routes to the copyroom-adopt and copyroom-template-edit sub-skills for the deep arcs.
auto_trigger:
  keywords: ["scaffold a repo", "template drift", "template update", "adopt a repo", "templatize", "copyroom new", "copyroom update", "copyroom template-checkout", "copyroom inspect", "copyroom doctor", "agent-files", "copyroom layer", "personal layer", "my-ai"]
---

# CopyRoom — the coordinator

CopyRoom is a mode-aware CLI wrapper around Copier. It creates projects from
templates (`new`), updates them (`update`, a three-way merge), edits a template
from inside a project (`template-checkout`/`template-test`/`template-preview`),
runs a template author's workshop (`render`/`golden`/`update-test`), and adopts
existing repos (`adopt`/`templatize`). A repo can be managed by several templates
at once — *layers* (`layer add`, `update --layer`). Full surface: the CLI reference.

## The law

- **Run everything in `devenv shell`.** Never invoke bare `uv`/`python`/`pytest`/
  `copier`/`git`; the shell pins Python and the pinned tools.
- **Mode awareness.** CopyRoom detects project vs. workshop from markers;
  bootstrap commands (`new`, `adopt`, `templatize`) and `doctor`/`agent-files`
  run anywhere. Don't assume a command belongs to the current directory's mode.
- **Never hand-edit an answers file** (`.copier-answers.yml` or a layer's
  `.copier-answers.<name>.yml`). Copier regenerates it on every update; manual
  edits corrupt the merge base.
- **Layers are independent.** `update` converges one layer. The default is
  `base`; `--layer NAME` selects another. Never make one layer manage another's
  files. Run `copyroom layer list` to find which template owns a file.
- **The template-edit loop is preview-only.** `template-preview` never applies
  anything to the project — apply later with `copyroom update <ref>`.
- **Clean worktree before `update`.** Commit or stash first; the clean tree is
  the undo button (`git checkout .`).
- **Exit codes:** `0` ok · `1` finding · `2` infra/config · `3` usage.

## Route

| Ask | Go to |
|-----|-------|
| Bring an existing repo under template management (adopt/templatize) | the `copyroom-adopt` skill |
| Edit the template from inside a generated project | the `copyroom-template-edit` skill |
| Add or converge a second template on a repo (e.g. the personal layer) | `copyroom layer add` / `copyroom update --layer` → `docs/user/layers.md` |
| Everything else (new/update/inspect/status/workshop) | the CLI reference + `copyroom --help` in the shell |

Detail lives in `docs/user/` — read it before improvising; the docs own the facts.

For when to scaffold vs. adopt vs. update across a repo's lifecycle, see the `repoman` skill.
