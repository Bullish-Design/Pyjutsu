"""Driver for the devenv-pinned `jj` CLI — the other half of every differential test.

Each operation Pyjutsu performs is checked against the exact `jj` 0.38.0 binary the devenv
pins (concept §7). This module just runs that binary against a repo with an isolated config
so results are reproducible and independent of the developer's `~/.jjconfig`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Isolated config: a fixed identity so `jj` can author commits, plus a pinned commit timestamp so
# authoring is reproducible. Both the CLI and the binding load this same config (the binding via
# `JJ_CONFIG`), so the *same* mutation applied to two byte-identical repos (made by copying a repo
# directory — see the differential tests) produces identical commit ids: the committer timestamp,
# which would otherwise be "now", is fixed. Change ids stay naturally random/unique (seeding them
# would collide across the separate `jj` processes the harness spawns), so identical starting state
# is achieved by copying, not by reseeding.
_CONFIG_TOML = (
    '[user]\nname = "Pyjutsu Test"\nemail = "test@pyjutsu.invalid"\n'
    "\n[debug]\n"
    'commit-timestamp = "2001-02-03T04:05:06+07:00"\n'
)


def write_config(directory: Path) -> Path:
    """Write the isolated jj config into ``directory`` and return its path."""
    config = directory / "jjconfig.toml"
    config.write_text(_CONFIG_TOML)
    return config


class JjCli:
    """Runs the pinned ``jj`` CLI against repos, using one isolated config file."""

    def __init__(self, config: Path) -> None:
        self._config = config

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["JJ_CONFIG"] = str(self._config)
        return env

    def __call__(self, repo: Path, *args: str) -> str:
        """Run ``jj <args>`` inside ``repo`` and return stdout (raising on non-zero exit)."""
        result = subprocess.run(
            ["jj", *args],
            cwd=repo,
            env=self._env(),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def init_colocated(self, repo: Path) -> None:
        """``jj git init --colocate`` in ``repo``."""
        self(repo, "git", "init", "--colocate")

    def template(self, repo: Path, revset: str, expr: str) -> str:
        """Render jj template ``expr`` for the single revision ``revset`` (stripped)."""
        return self(repo, "log", "-r", revset, "--no-graph", "-T", expr).strip()

    def change_id(self, repo: Path, revset: str) -> str:
        """The change id (reverse-hex letter form) of the single revision ``revset``."""
        return self.template(repo, revset, "change_id")

    def commit_id(self, repo: Path, revset: str) -> str:
        """The commit id (hex) of the single revision ``revset``."""
        return self.template(repo, revset, "commit_id")

    def change_ids(self, repo: Path, revset: str) -> list[str]:
        """Change ids for every revision matched by ``revset``, in jj's default (newest-first) order."""
        out = self(repo, "log", "-r", revset, "--no-graph", "-T", 'change_id ++ "\\n"')
        return [line for line in out.splitlines() if line]

    def parent_commit_ids(self, repo: Path, revset: str) -> list[str]:
        """Commit ids of the parents of the single revision ``revset``."""
        rendered = self.template(repo, revset, 'parents.map(|c| c.commit_id()).join(",")')
        return [p for p in rendered.split(",") if p]

    def is_empty(self, repo: Path, revset: str) -> bool:
        """Whether the single revision ``revset`` is empty (no change to its parent tree)."""
        return self.template(repo, revset, 'if(empty, "true", "false")') == "true"

    def local_bookmarks(self, repo: Path, revset: str) -> list[str]:
        """Local bookmark names pointing at the single revision ``revset`` (sorted)."""
        rendered = self.template(repo, revset, 'local_bookmarks.map(|b| b.name()).join(",")')
        return sorted(b for b in rendered.split(",") if b)

    def op_log_ids(self, repo: Path, limit: int | None = None) -> list[str]:
        """Operation ids from `jj op log` (newest first), optionally capped at ``limit``."""
        args = ["op", "log", "--no-graph", "-T", 'id ++ "\\n"']
        if limit is not None:
            args += ["--limit", str(limit)]
        return [line for line in self(repo, *args).splitlines() if line]

    def op_head_id(self, repo: Path) -> str:
        """The current head operation id."""
        return self(repo, "op", "log", "--no-graph", "--limit", "1", "-T", "id").strip()

    def op_head_description(self, repo: Path) -> str:
        """The description of the current head operation."""
        return self(repo, "op", "log", "--no-graph", "--limit", "1", "-T", "description").strip()

    def change_id_at_op(self, repo: Path, op: str, revset: str) -> str:
        """The change id of ``revset`` as seen at operation ``op`` (`jj --at-op`)."""
        return self(repo, "--at-op", op, "log", "--no-graph", "-r", revset, "-T", "change_id").strip()

    def diff_stat_totals(self, repo: Path, revset: str) -> tuple[int, int]:
        """``(insertions, deletions)`` from the summary line of `jj diff -r <revset> --stat`."""
        out = self(repo, "diff", "-r", revset, "--stat")
        summary = out.splitlines()[-1] if out.strip() else ""
        insertions = deletions = 0
        for part in summary.split(","):
            part = part.strip()
            if "insertion" in part:
                insertions = int(part.split()[0])
            elif "deletion" in part:
                deletions = int(part.split()[0])
        return insertions, deletions

    def conflicted_paths(self, repo: Path) -> dict[str, int]:
        """Map of conflicted path → number of sides, from `jj resolve --list` (operates on `@`).

        Lines look like ``file.txt    2-sided conflict``; returns ``{"file.txt": 2}``.
        """
        out = self(repo, "resolve", "--list")
        result: dict[str, int] = {}
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split()
            path, sides_token = parts[0], parts[1]  # e.g. "2-sided"
            result[path] = int(sides_token.split("-", 1)[0])
        return result

    def bookmarks(self, repo: Path) -> set[tuple[str, str, str, bool]]:
        """All bookmark rows as ``{(name, remote, target_commit_id, tracked)}`` (`--all-remotes`).

        ``remote`` is ``""`` for local rows. Matches what the binding's flat bookmark list emits.
        """
        template = (
            'name ++ "|" ++ if(remote, remote, "") ++ "|" '
            '++ normal_target.commit_id() ++ "|" ++ if(tracked, "T", "F") ++ "\\n"'
        )
        out = self(repo, "bookmark", "list", "--all-remotes", "-T", template)
        rows: set[tuple[str, str, str, bool]] = set()
        for line in out.splitlines():
            if not line:
                continue
            name, remote, target, tracked = line.split("|")
            rows.add((name, remote, target, tracked == "T"))
        return rows

    def workspaces(self, repo: Path) -> set[str]:
        """The set of workspace names tracked in ``repo`` (`jj workspace list -T name`)."""
        out = self(repo, "workspace", "list", "-T", 'name ++ "\\n"')
        return {line for line in out.splitlines() if line}

    def remotes(self, repo: Path) -> dict[str, str]:
        """Map of git remote name → fetch url from `jj git remote list` (lines: ``name url``)."""
        out = self(repo, "git", "remote", "list")
        result: dict[str, str] = {}
        for line in out.splitlines():
            if not line.strip():
                continue
            name, url = line.split(" ", 1)
            result[name] = url
        return result

    def signature(self, repo: Path, revset: str, which: str) -> dict[str, object]:
        """The ``author``/``committer`` of ``revset`` as {name, email, epoch, tz_minutes}."""
        name = self.template(repo, revset, f"{which}.name()")
        email = self.template(repo, revset, f"{which}.email()")
        epoch = int(self.template(repo, revset, f'{which}.timestamp().format("%s")'))
        offset = self.template(repo, revset, f'{which}.timestamp().format("%z")')  # e.g. -0400
        sign = -1 if offset[0] == "-" else 1
        tz_minutes = sign * (int(offset[1:3]) * 60 + int(offset[3:5]))
        return {"name": name, "email": email, "epoch": epoch, "tz_minutes": tz_minutes}
