---
name: my-ai
description: The user's standing agent configuration — the cross-repo law that holds in every one of their repositories. Read it alongside this repo's AGENTS.md; AGENTS.md says what this project is, this skill says how the user wants work done anywhere.
auto_trigger:
  keywords: ["my-ai", "personal config", "standing preferences", "how should I work here", "devenv shell", "exit codes", "agent-files convention"]
---

# my-ai — the standing agent configuration

This skill is the **personal layer**: one file, identical in every repository,
distributed by the `my-ai` Copier template and converged by
`copyroom update --layer my-ai`. It holds what is true *everywhere* the user
works. Anything true of only one project belongs in that project's `AGENTS.md`,
not here.

## The law

1. **Run everything inside the devenv shell.** It pins the toolchain. Never
   invoke bare `uv` / `python` / `pytest` / `ruff` / `copier` / `git`. Batch
   commands into one `devenv shell -- …` invocation rather than paying shell
   entry per command.
2. **Exit codes are an API:** `0` ok · `1` finding/decision · `2` infra/config ·
   `3` usage. Never collapse them to `0`/`1`.
3. **Structured plain-text reports.** Simple lines an agent can parse; no rich
   coloring under `--json`.
4. **Route through the manager that owns the domain.** Verification → testee.
   VCS → gitman. Templating / scaffolding / convergence → copyroom. Docs →
   docman. For cross-domain *ordering* (verify before save, scaffold before
   change, release last), read the `repoman` skill — it is the router.
5. **Verify before save.** A green check precedes a commit, always.
6. **Never hand-edit a tool-shipped file as a second copy.** Every file under
   `.agents/skills/` has exactly one owner; see the ownership table below. Edit
   at the source and re-materialize.
7. **Write in Simplified Technical English.** See the next section.

## Writing style

Everything you write follows **Simplified Technical English
([ASD-STE100](https://www.asd-ste100.org/)) style** as closely as the context
allows. This covers docs, skills, code comments, docstrings, commit messages,
CLI help, error text, and replies to the user.

- **One idea per sentence.** Keep sentences short: 20 words or fewer for
  descriptive text, 25 for procedures.
- **One paragraph per topic**, six sentences or fewer.
- **Use the active voice.** Name the actor: "the parser reads the marker", not
  "the marker is read".
- **Use one word for one meaning.** Pick a term and keep it. Never swap in a
  synonym for variety — a repo's domain terms are fixed vocabulary.
- **Use the imperative for instructions.** "Run the suite." Not "You should
  probably run the suite."
- **Say what to do, not only what not to do.**
- **Drop filler.** No "simply", "just", "of course", "as you know", "note
  that", or hedging that adds no information.
- **Spell out an abbreviation on first use** in each document.
- **No slang, no idioms, no metaphors** where a plain term works.

Where STE and clarity conflict, choose clarity. Where STE and an established
domain term conflict, keep the domain term.

## The agent-files convention

| File | Role | Owner |
|------|------|-------|
| `AGENTS.md` | the canonical repo instructions — one source of truth | **the repo** |
| `CLAUDE.md` | a symlink → `AGENTS.md`, so every tool reads the same file | the personal layer |
| `.agents/skills/<name>/SKILL.md` | skills — imperative, short, domain-bounded; they link to docs, never repeat them | see below |

Skill ownership:

| Owner | Which skills | Materialized by |
|---|---|---|
| tool-shipped | copyroom's canonical set (`copyroom`, `copyroom-adopt`, `copyroom-template-edit`) | `copyroom agent-files export` |
| genome / fleet | the `devenv-*` literacy skills, `.agents/devenv/` docs | `copyroom update` (base layer) |
| **personal** | this skill | `copyroom update --layer my-ai` |
| repoman's router | `.agents/skills/repoman/` — *generated* from this repo's manager roster | `repoman install-skills` |
| repo overlay | anything else | the repo |

`.agents/` is dual-use: `.agents/skills/` is tracked, the rest is tool runtime
state and stays gitignored.

## Keeping this configuration current

```bash
copyroom layer list                  # which layers manage this repo
copyroom update --layer my-ai        # converge the personal layer
copyroom agent-files check           # conformance report (warn-level)
```

Permanently diverging on a skill in *this* repo? Declare it rather than fighting
the merge — chronic conflicts are a design smell:

```yaml
# copyroom.project.yml
agent:
  overlay: [my-ai]      # copyroom update stops managing it here
```

To change the law **everywhere**, edit it in the `my-ai` repo, tag a release,
and update each repo's layer. Editing this file in place is a local override, not
a change to the configuration.

---

_Deferral: for cross-domain ordering — when to verify vs. commit vs. release —
route via the `repoman` skill. For this project's specifics, read its
`AGENTS.md`._
