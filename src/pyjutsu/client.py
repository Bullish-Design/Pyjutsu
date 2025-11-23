# src/pyjutsu/client.py
"""Main Pyjutsu client."""

from __future__ import annotations

from pathlib import Path

from pyjutsu._commands import JjCommand
from pyjutsu.exceptions import JjCommandError, JjNotFoundError, RepositoryNotFoundError
from pyjutsu.models import FileChange, WorkspaceStatus
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

    def __repr__(self) -> str:
        """String representation."""
        return f"JjClient({self.repo_path})"
