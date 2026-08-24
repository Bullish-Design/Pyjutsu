---
title: Revset configuration fidelity
type: project
status: active
loci:
  schema: 1
  id: 01a02558-2047-7000-9372-7f439920f294
  projects: []
---

# PROJECT: Revset configuration fidelity

Make Pyjutsu revsets read the configuration Pyjutsu already loads, and give the
binding jj's configurable immutability.

Issue 001 fixed configuration loading. `src/config_loader.rs` resolves user,
secure repository, and secure workspace configuration from the correct Jujutsu
identity. Revset evaluation never consumes it.

This project covers three strands. They are separable, and they land in order.

1. **Plumbing.** Build the revset alias map and the glob-by-default flag from
   the resolved `UserSettings`, then pass both into `RevsetParseContext`. A
   correctness fix, but not a silent one: flipping the glob default changes what
   existing string patterns match, with no error. It ships as a documented break.
2. **Default aliases.** Ship jj's own alias definitions as a Pyjutsu default
   configuration layer, so `trunk()` and its siblings resolve out of the box.
   The user chose this over documenting the aliases as absent.
3. **Immutability.** Enforce jj's configurable `immutable_heads()` set on the
   rewrite verbs. Pyjutsu protects only the root commit today.

Strand 1 blocks strands 2 and 3. Strand 3 is a new safety feature, not a
correctness fix, so it carries its own design decisions.

The project makes two behaviour breaks for existing callers — the glob default
in strand 1 and enforcement in strand 3. Both land on by default in a minor bump
to 0.16.0, with one shared release note. `transaction(ignore_immutable=True)` is
the escape hatch. Those choices are settled; `prompt.md` records them.

The implementation prompt lives beside this record in `prompt.md`.

Related records:

- [[.loci/issues/003-revset-ignores-configuration/issue.md]] states the gap.
- [[.loci/issues/002-jj-lib-0-44-upgrade-investigation/issue.md]] must gain a
  re-sync step for the vendored alias file.