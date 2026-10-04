# AGENTS.md — project instructions

> This is the repository's canonical instruction file. `CLAUDE.md` is a symlink
> to it so agent tools read the same instructions.

## What this project is

_One paragraph: what it does, who uses it, what it is not._

## Working here

```bash
devenv shell                     # enter the pinned environment
repoman-sync                     # verify toolchain + install agent skills
```

_Add the build / test / lint commands, and the gate that must be green before a
PR._

## Where things live

_The two or three directories a newcomer actually needs. Deeper detail belongs in
`docs/`, not here._

## The standing configuration

The central Devman link plane supplies shared tool and agent instructions under
`.agents/skills/`. Keep this file focused on facts about this project.

```bash
copyroom layer list              # which template layers manage this repo
copyroom agent-files check       # conformance report
```
