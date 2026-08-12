# AGENTS.md — project instructions

> **Seed.** The `my-ai` personal layer wrote this file because this repo had
> none. It is now **the repo's** file: edit it freely, and no `my-ai` update will
> ever overwrite it (`_skip_if_exists`). Every agent tool reads it through the
> `CLAUDE.md` symlink.

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

The user's cross-repo law — devenv discipline, the exit-code contract, manager
routing, the agent-files convention — lives in
[`.agents/skills/my-ai/SKILL.md`](.agents/skills/my-ai/SKILL.md), delivered by
the `my-ai` personal layer. **Read it first.** Keep this file for what is true of
*this* project only.

```bash
copyroom layer list              # which template layers manage this repo
copyroom update --layer my-ai    # converge the personal layer
copyroom agent-files check       # conformance report
```
