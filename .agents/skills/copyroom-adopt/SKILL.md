---
name: copyroom-adopt
description: Adopt or templatize an existing repo — bring a hand-written repo under template management with copyroom adopt / templatize, and read the drift report.
auto_trigger:
  keywords: ["adopt a repo", "adopt this repo", "templatize", "extract a template", "bring a repo under management", "template drift report", "copyroom adopt", "copyroom templatize", "answers file", "drift", "layer add", "add a layer", "personal layer", "my-ai layer"]
---

# Adopt / templatize / layer an existing repo

CopyRoom brings an existing, hand-written repo — one with no CopyRoom markers —
under template management. **Three paths. Pick by two questions: does a template
exist, and are its files already in the repo?**

| Situation | Command |
|---|---|
| Template exists, and the repo **already looks like it** | `copyroom adopt <template> --ref <ref> [--answers <file>] [--write]` |
| Template exists, and its files are **not in the repo yet** | `copyroom layer add <template> [--ref <ref>]` |
| No template yet | `copyroom templatize [--into PATH]` → parameterize (`copyroom golden` loop) → finalize (git init + tag) → `copyroom adopt` |

**`adopt` records a link. `layer add` places files.** Agents confuse these two
most often. `adopt` is report-only: it never writes the template's content into
the repo. Use `adopt` to install a template and you get an answers file and
nothing else. To place the files, use `layer add`.

`layer add` also gives a repo a **second** template. The personal layer (`my-ai`)
carries the user's `AGENTS.md` seed, `CLAUDE.md` symlink, and personal skills. It
sits beside the genome that generated the repo. Run `copyroom layer list` first.
It reports which templates already manage the repo. Full model:
`docs/user/layers.md`.

The template is **named or extracted, never guessed** — CopyRoom will not
fuzzy-match a registry.

## Rules

- **Adoption is report-only unless `--write`.** It never modifies an existing
  repo file: report-only adds only the reviewable patch under
  `.copyroom/adopt/`; `--write` additionally adds `.copier-answers.yml`. Drift
  is information, not a problem to auto-fix; there is no `--reconcile`.
- **The template source must be a git repository** (a ref must be renderable).
- **`adopt` refuses an already-managed repo** unless `--force`. The refusal is
  **per layer**. A genome's `.copier-answers.yml` does not block `--layer my-ai`.
- **`layer add` overwrites the files its template ships.** It must: Copier
  prompts on each conflict and fails when stdin is not a terminal. `layer add` is
  the *apply* step. Run `copyroom update --layer <name>` afterwards to converge;
  that step three-way-merges. Neither step overwrites a file the template
  declares in `_skip_if_exists`, such as `AGENTS.md`.
- **Update the genome before you add an overlay layer.** The genome and the
  personal layer both ship `AGENTS.md`. Whichever lands first seeds it. Adding
  the overlay to a repo that is behind its genome costs one avoidable merge.
  Order does not matter when the repo is current or already owns an `AGENTS.md`.
- **Author the `--answers` file yourself** from the template's `copier.yml`;
  `--write` only records the link once the drift report looks right.
  (`--answers` is optional — questions it leaves out fall back to the
  template's defaults — but authoring it explicitly is the reliable path.)

The drift report has three parts: *Template adds* (files the template produces
that the repo lacks), *Differs* (divergent content), and *Repo-only* (the repo's
legitimately-extra content). A reviewable patch lands under `.copyroom/adopt/`.

Detail: `docs/user/adoption.md` (adopt/templatize) and `docs/user/layers.md`
(`layer add`, `update --layer`) — read them before running the arc.

For when to adopt vs. scaffold a new project, see the `copyroom` skill.
