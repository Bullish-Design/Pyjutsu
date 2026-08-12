---
name: copyroom-template-edit
description: Edit a template from inside a generated project — the template-checkout → edit → template-test → template-preview → template-discard loop, preview-only.
auto_trigger:
  keywords: ["edit the template", "change the template", "add to the template", "fix the template", "preview the update", "what would my project look like", "template-checkout", "template-test", "template-preview", "template-discard", "template change"]
---

# Edit a template from a project

Drive a change *back into the template* from inside a generated project and
preview exactly what the project would receive on update — without touching the
working tree and without pushing anything.

## The loop

```bash
copyroom template-checkout            # 1. template → editable worktree (scratch branch)
# …edit files under the printed worktree path…   2. make the change
copyroom template-test [--check CMD]  # 3. confirm it still renders
copyroom template-preview [--from REF]  # 4. see what your project would receive
copyroom template-discard             # (optional) throw the edit away, start over
```

All commands are **project-mode**; run from the project directory. They re-resolve
the *same* worktree, so edits persist across `template-test` and
`template-preview`.

## Rules

- **Preview only.** Nothing is applied; after previewing, commit/tag the change
  in the template repo and apply it with `copyroom update <ref>`.
- **This loop edits the base layer's template only.** `template-checkout` reads
  `.copier-answers.yml` and has no `--layer` flag. In a repo with overlay layers
  it always picks the genome. To change an overlay template, edit that template's
  own repo. Tag it, then run `copyroom update --layer <name>`. Run
  `copyroom layer list` to find which template owns a file. See
  `docs/user/layers.md`.
- **`.copier-answers.yml` churn in the preview diff is expected metadata**, not a
  content change — its recorded `_commit` advances on update.
- **Conflicts/rejects are information, not failure** — they show where the
  template change would clash with local edits.
- **Reused edit branches with leftover commits are flagged** — run
  `template-discard` to start fresh.
- **Only `.jinja` files render.** `{{ … }}` expressions reference the template's
  questions (`docs/copier/overview.md` §2).

Detail: `docs/user/template-editing.md` — read it before running the loop.

For when to edit the template vs. change only this project, see the `copyroom` skill.
