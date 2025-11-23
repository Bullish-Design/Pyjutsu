# src/pyjutsu/client.py
"""Main Pyjutsu client."""

from __future__ import annotations

from pathlib import Path

from pyjutsu._commands import JjCommand
from pyjutsu.exceptions import JjCommandError, JjNotFoundError, RepositoryNotFoundError
from pyjutsu.models import Branch, Change, FileChange, LogEntry, WorkspaceStatus
from pyjutsu.enums import FileStatus


class JjClient:
    """Main interface for interacting with a Jujutsu repository."""

    def __init__(self, repo_path: Path | str = ".") -> None:
        """Initialize client for repository.

        Args:
            repo_path: Path to jj repository (default: current directory)

        Raises:
            JjNotFoundError: If jj is not installed
            RepositoryNotFoundError: If path is not a jj repository
        """
        self.repo_path = Path(repo_path).resolve()
        self._cmd = JjCommand(self.repo_path)
        self._validate_repository()

    def _validate_repository(self) -> None:
        """Validate that path is a jj repository.

        Raises:
            RepositoryNotFoundError: If not a valid jj repository
        """
        try:
            # This will fail quickly if we're not in a jj repo
            self._cmd.run("status")
        except Exception as exc:  # pragma: no cover - behavior depends on jj
            raise RepositoryNotFoundError(str(self.repo_path)) from exc

    @classmethod
    def init(cls, path: Path | str = ".", git_repo: str | None = None) -> "JjClient":
        """Initialize a new jj repository.

        Args:
            path: Path for new repository
            git_repo: Optional git repository URL to colocate with

        Returns:
            JjClient instance for the new repository

        Raises:
            JjCommandError: If init fails
        """
        path = Path(path).resolve()
        path.mkdir(parents=True, exist_ok=True)

        cmd = JjCommand(path)

        if git_repo:
            from sh import git

            git("clone", git_repo, str(path))
            cmd.run("git", "init", "--git-repo", str(path))
        else:
            cmd.run("git", "init", str(path))

        return cls(path)

    def status(self) -> WorkspaceStatus:
        """Get current workspace status.

        Returns:
            WorkspaceStatus with current state

        Raises:
            JjCommandError: If underlying jj commands fail
        """
        # 1) Working copy change ID: use commit template keyword `change_id`
        wc_output = self._cmd.run("log", "-r", "@", "--no-graph", "-T", "change_id")
        working_copy_id = wc_output.strip()

        # 2) "Branch" name in jj = local bookmark(s) pointing at @
        #    `jj bookmark list -r @ -T name()` prints one name per line.
        try:
            bookmark_output = self._cmd.run(
                "bookmark",
                "list",
                "-r",
                "@",
                "--no-pager",
                "-T",
                "name()",
            )
            bookmark_lines = [
                line.strip()
                for line in bookmark_output.splitlines()
                if line.strip()
            ]
            current_branch = bookmark_lines[0] if bookmark_lines else None
        except JjCommandError:
            # Older jj or unusual setups: treat as "no branch"
            current_branch = None

        # 3) File changes: parse `jj status` output
        status_output = self._cmd.run("status")
        modified_files = self._parse_status_output(status_output)

        # 4) Conflicts: jj marks commits with conflicts; `jj status` also mentions them
        has_conflicts = "conflict" in status_output.lower()

        return WorkspaceStatus(
            working_copy_change_id=working_copy_id,
            current_branch=current_branch,
            has_conflicts=has_conflicts,
            modified_files=modified_files,
        )

    def _parse_status_output(self, output: str) -> list[FileChange]:
        """Parse jj status output into FileChange objects.

        Args:
            output: Raw `jj status` output

        Returns:
            List of FileChange objects
        """

        changes: list[FileChange] = []

        for raw in output.splitlines():
            line = raw.strip()
            if not line:
                continue

            # Expect "X path" or "X old => new", where X is a 1-char status code.
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue

            status_code, path_info = parts
            file_status = FileStatus.from_code(status_code)

            # Ignore non-status lines like "The working copy has no changes.",
            # "Working copy (@): ...", "Parent commit (@-): ...", etc.
            if file_status is FileStatus.UNKNOWN:
                continue

            if "=>" in path_info:
                # Rename: "old_path => new_path"
                old, new = path_info.split("=>", maxsplit=1)
                changes.append(
                    FileChange(
                        path=Path(new.strip()),
                        status=file_status,
                        old_path=Path(old.strip()),
                    )
                )
            else:
                changes.append(
                    FileChange(
                        path=Path(path_info.strip()),
                        status=file_status,
                    )
                )

        return changes

    def describe(self, message: str, revision: str = "@") -> None:
        """Set the description (commit message) for a change.

        Args:
            message: Commit message to set
            revision: Revision to describe (default: working copy '@')

        Raises:
            JjCommandError: If describe command fails
        """
        self._cmd.run("describe", "-r", revision, "-m", message)

    def new(self, revision: str | None = None) -> str:
        """Create a new working copy change.

        Args:
            revision: Optional revision to start from. If omitted, uses current working copy.

        Returns:
            The change ID of the new working copy change.

        Raises:
            JjCommandError: If new command fails
        """
        if revision:
            self._cmd.run("new", revision)
        else:
            self._cmd.run("new")

        output = self._cmd.run("log", "-r", "@", "--no-graph", "-T", "change_id")
        return output.strip()

    def branch_create(self, name: str, revision: str = "@") -> Branch:
        """Create a new branch/bookmark.

        Args:
            name: Branch name to create
            revision: Revision to create the branch at (default: working copy '@')

        Returns:
            Branch object representing the created branch.

        Raises:
            JjCommandError: If branch creation fails
        """
        self._cmd.run("bookmark", "create", name, "-r", revision)

        change_id = self._cmd.run(
            "log",
            "-r",
            revision,
            "--no-graph",
            "-T",
            "change_id",
        ).strip()
        commit_id = self._cmd.run(
            "log",
            "-r",
            revision,
            "--no-graph",
            "-T",
            "commit_id",
        ).strip()

        return Branch(
            name=name,
            target_change_id=change_id,
            target_commit_id=commit_id,
        )

    def branch_list(self) -> list[Branch]:
        """List all branches/bookmarks.

        Returns:
            List of Branch objects.

        Raises:
            JjCommandError: If listing branches fails
        """
        output = self._cmd.run("bookmark", "list")
        branches: list[Branch] = []

        for raw in output.strip().split("\n"):
            line = raw.strip()
            if not line:
                continue

            # Expected format: "branch_name: <change-id> ..."
            parts = line.split(":", maxsplit=1)
            if len(parts) != 2:
                continue

            name = parts[0].strip()

            # Resolve full change_id and commit_id via jj log
            try:
                change_id = self._cmd.run(
                    "log",
                    "-r",
                    name,
                    "--no-graph",
                    "-T",
                    "change_id",
                ).strip()
            except JjCommandError:
                change_id = ""

            try:
                commit_id = self._cmd.run(
                    "log",
                    "-r",
                    name,
                    "--no-graph",
                    "-T",
                    "commit_id",
                ).strip()
            except JjCommandError:
                commit_id = "0" * 40

            branches.append(
                Branch(
                    name=name,
                    target_change_id=change_id,
                    target_commit_id=commit_id,
                )
            )

        return branches

    def branch_delete(self, name: str) -> None:
        """Delete a branch/bookmark.

        Args:
            name: Branch name to delete

        Raises:
            JjCommandError: If branch deletion fails
        """
        self._cmd.run("bookmark", "delete", name)

    def branch_set(self, name: str, revision: str) -> None:
        """Move an existing branch to a different revision.

        Args:
            name: Branch name to move
            revision: Target revision for the branch

        Raises:
            JjCommandError: If branch set fails
        """
        self._cmd.run("bookmark", "set", name, "-r", revision)

    def log(self, revset: str = "@", limit: int = 10) -> list[LogEntry]:
        """Get commit log.

        Args:
            revset: Revset expression (default: full history for current repo)
            limit: Maximum number of entries

        Returns:
            List of LogEntry objects

        Raises:
            JjCommandError: If log command fails
        """
        from datetime import datetime

        # For the default revset, show full history rather than just '@'
        revset_expr = "all()" if revset == "@" else revset
        print(f"Using revset expression: {revset_expr}")
        
        change_ids_raw = self._cmd.run(
            "log",
            "-r",
            revset_expr,
            "--no-graph",
            "-T",
            "change_id",
        )
        print(f"Raw change_ids: {change_ids_raw}")
        commit_ids_raw = self._cmd.run(
            "log",
            "-r",
            revset_expr,
            "--no-graph",
            "-T",
            "commit_id",
        )
        print(f"Raw commit_ids: {commit_ids_raw}")
        descriptions_raw = self._cmd.run(
            "log",
            "-r",
            revset_expr,
            "--no-graph",
            "-T",
            "description.first_line()",
        )
        print(f"Raw descriptions: {descriptions_raw}")
        authors_raw = self._cmd.run(
            "log",
            "-r",
            revset_expr,
            "--no-graph",
            "-T",
            "author",
        )
        print(f"Raw authors: {authors_raw}")
        times_raw = self._cmd.run(
            "log",
            "-r",
            revset_expr,
            "--no-graph",
            "-T",
            "committer.timestamp()",
        )
        print(f"Raw times: {times_raw}")

        change_ids = [line.strip() for line in change_ids_raw.splitlines() if line.strip()]
        commit_ids = [line.strip() for line in commit_ids_raw.splitlines() if line.strip()]
        descriptions = [line.strip() for line in descriptions_raw.splitlines() if line.strip()]
        authors = [line.strip() for line in authors_raw.splitlines() if line.strip()]
        times = [line.strip() for line in times_raw.splitlines() if line.strip()]

        # Zip up to the shortest to avoid index errors if jj output changes slightly
        num_entries = min(len(change_ids), len(commit_ids), len(descriptions), len(authors), len(times))

        # print(f"Parsing {num_entries} log entries")?
        entries: list[LogEntry] = []
        for i in range(num_entries):
            ts_raw = times[i].replace("Z", "+00:00")
            try:
                ts = datetime.fromisoformat(ts_raw)
            except ValueError:
                ts = datetime.fromtimestamp(0)

            change = Change(
                change_id=change_ids[i],
                commit_id=commit_ids[i],
                description=descriptions[i],
                author=authors[i],
                timestamp=ts,
            )
            entries.append(LogEntry(change=change))

        if limit >= 0:
            return entries[:limit]
        return entries


    def __repr__(self) -> str:
        """String representation."""
        return f"JjClient({self.repo_path})"
