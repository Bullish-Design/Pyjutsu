# SPEC.md

# Pyjutsu Development Specification

Step-by-step guide to build Pyjutsu from scratch. Each step is self-contained and testable.

---

## Step 0: Project Setup

### Code Work

Create project structure:

```bash
mkdir -p pyjutsu/{src/pyjutsu,tests/{unit,integration}}
cd pyjutsu
```

Create `pyproject.toml`:

```toml
[project]
name = "pyjutsu"
version = "0.1.0"
description = "Pythonic wrapper for Jujutsu VCS"
requires-python = ">=3.13"
dependencies = [
    "pydantic>=2.0.0",
    "sh>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=4.0.0",
    "mypy>=1.8.0",
    "ruff>=0.1.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 120
target-version = "py313"

[tool.mypy]
python_version = "3.13"
strict = true
warn_return_any = true
warn_unused_configs = true

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
```

Create `src/pyjutsu/__init__.py`:

```python
# src/pyjutsu/__init__.py
"""Pyjutsu - Pythonic wrapper for Jujutsu VCS."""

from __future__ import annotations

__version__ = "0.1.0"
```

Create `.gitignore`:

```
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg
.pytest_cache/
.coverage
htmlcov/
.mypy_cache/
.ruff_cache/
.venv/
venv/
```

### Testing

```bash
# Install in development mode
uv pip install -e ".[dev]"

# Verify installation
python -c "import pyjutsu; print(pyjutsu.__version__)"
# Expected: 0.1.0

# Verify tools work
ruff check src/
mypy src/
pytest --collect-only
```

---

## Step 1: Exception Hierarchy

### Code Work

Create `src/pyjutsu/exceptions.py`:

```python
# src/pyjutsu/exceptions.py
"""Exception classes for Pyjutsu."""

from __future__ import annotations


class PyjutsuError(Exception):
    """Base exception for all Pyjutsu errors."""


class JjNotFoundError(PyjutsuError):
    """Raised when jj executable is not found in PATH."""

    def __init__(self, message: str = "jj executable not found in PATH") -> None:
        super().__init__(message)


class JjCommandError(PyjutsuError):
    """Raised when a jj command fails."""

    def __init__(
        self,
        command: str,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        message = f"Command '{command}' failed with exit code {returncode}"
        if stderr:
            message += f"\nstderr: {stderr}"
        super().__init__(message)


class RepositoryNotFoundError(PyjutsuError):
    """Raised when path is not a jj repository."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Not a jj repository: {path}")


class ConflictError(PyjutsuError):
    """Raised when repository has unresolved conflicts."""

    def __init__(self, message: str = "Repository has unresolved conflicts") -> None:
        super().__init__(message)


class InvalidRevisionError(PyjutsuError):
    """Raised when a revision specifier is invalid."""

    def __init__(self, revision: str) -> None:
        self.revision = revision
        super().__init__(f"Invalid revision: {revision}")
```

Update `src/pyjutsu/__init__.py`:

```python
# src/pyjutsu/__init__.py
"""Pyjutsu - Pythonic wrapper for Jujutsu VCS."""

from __future__ import annotations

from pyjutsu.exceptions import (
    ConflictError,
    InvalidRevisionError,
    JjCommandError,
    JjNotFoundError,
    PyjutsuError,
    RepositoryNotFoundError,
)

__version__ = "0.1.0"

__all__ = [
    "ConflictError",
    "InvalidRevisionError",
    "JjCommandError",
    "JjNotFoundError",
    "PyjutsuError",
    "RepositoryNotFoundError",
]
```

### Testing

Create `tests/unit/test_exceptions.py`:

```python
# tests/unit/test_exceptions.py
"""Test exception hierarchy."""

from __future__ import annotations

import pytest

from pyjutsu import (
    ConflictError,
    InvalidRevisionError,
    JjCommandError,
    JjNotFoundError,
    PyjutsuError,
    RepositoryNotFoundError,
)


def test_exception_hierarchy() -> None:
    """All custom exceptions inherit from PyjutsuError."""
    assert issubclass(JjNotFoundError, PyjutsuError)
    assert issubclass(JjCommandError, PyjutsuError)
    assert issubclass(RepositoryNotFoundError, PyjutsuError)
    assert issubclass(ConflictError, PyjutsuError)
    assert issubclass(InvalidRevisionError, PyjutsuError)


def test_jj_not_found_error() -> None:
    """JjNotFoundError has default message."""
    err = JjNotFoundError()
    assert "not found" in str(err).lower()


def test_jj_command_error() -> None:
    """JjCommandError stores command details."""
    err = JjCommandError("jj status", 1, stdout="", stderr="error message")
    assert err.command == "jj status"
    assert err.returncode == 1
    assert err.stderr == "error message"
    assert "jj status" in str(err)
    assert "exit code 1" in str(err)


def test_repository_not_found_error() -> None:
    """RepositoryNotFoundError stores path."""
    err = RepositoryNotFoundError("/tmp/notrepo")
    assert err.path == "/tmp/notrepo"
    assert "/tmp/notrepo" in str(err)


def test_invalid_revision_error() -> None:
    """InvalidRevisionError stores revision."""
    err = InvalidRevisionError("bad@rev")
    assert err.revision == "bad@rev"
    assert "bad@rev" in str(err)
```

Run tests:

```bash
pytest tests/unit/test_exceptions.py -v
# All tests should pass
```

---

## Step 2: Enums and Constants

### Code Work

Create `src/pyjutsu/enums.py`:

```python
# src/pyjutsu/enums.py
"""Enumerations for Pyjutsu."""

from __future__ import annotations

from enum import Enum


class FileStatus(str, Enum):
    """File status codes from jj."""

    ADDED = "A"
    MODIFIED = "M"
    DELETED = "D"
    RENAMED = "R"
    COPIED = "C"
    UNKNOWN = "?"

    @classmethod
    def from_code(cls, code: str) -> FileStatus:
        """Parse status code from jj output."""
        code = code.strip().upper()
        for status in cls:
            if status.value == code:
                return status
        return cls.UNKNOWN


class ChangeState(str, Enum):
    """State of a change/commit."""

    WORKING_COPY = "working_copy"
    IMMUTABLE = "immutable"
    MUTABLE = "mutable"
    ABANDONED = "abandoned"


class BranchTrackingStatus(str, Enum):
    """Branch tracking status."""

    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    UP_TO_DATE = "up_to_date"
    UNTRACKED = "untracked"
```

Update `src/pyjutsu/__init__.py`:

```python
# src/pyjutsu/__init__.py
"""Pyjutsu - Pythonic wrapper for Jujutsu VCS."""

from __future__ import annotations

from pyjutsu.enums import BranchTrackingStatus, ChangeState, FileStatus
from pyjutsu.exceptions import (
    ConflictError,
    InvalidRevisionError,
    JjCommandError,
    JjNotFoundError,
    PyjutsuError,
    RepositoryNotFoundError,
)

__version__ = "0.1.0"

__all__ = [
    "BranchTrackingStatus",
    "ChangeState",
    "ConflictError",
    "FileStatus",
    "InvalidRevisionError",
    "JjCommandError",
    "JjNotFoundError",
    "PyjutsuError",
    "RepositoryNotFoundError",
]
```

### Testing

Create `tests/unit/test_enums.py`:

```python
# tests/unit/test_enums.py
"""Test enumerations."""

from __future__ import annotations

import pytest

from pyjutsu import FileStatus


def test_file_status_values() -> None:
    """FileStatus has expected values."""
    assert FileStatus.ADDED.value == "A"
    assert FileStatus.MODIFIED.value == "M"
    assert FileStatus.DELETED.value == "D"
    assert FileStatus.RENAMED.value == "R"


def test_file_status_from_code() -> None:
    """FileStatus.from_code parses correctly."""
    assert FileStatus.from_code("A") == FileStatus.ADDED
    assert FileStatus.from_code("M") == FileStatus.MODIFIED
    assert FileStatus.from_code(" D ") == FileStatus.DELETED  # Strips whitespace
    assert FileStatus.from_code("m") == FileStatus.MODIFIED  # Case insensitive


def test_file_status_from_code_unknown() -> None:
    """FileStatus.from_code returns UNKNOWN for invalid codes."""
    assert FileStatus.from_code("X") == FileStatus.UNKNOWN
    assert FileStatus.from_code("") == FileStatus.UNKNOWN
```

Run tests:

```bash
pytest tests/unit/test_enums.py -v
```

---

## Step 3: Core Pydantic Models

### Code Work

Create `src/pyjutsu/models.py`:

```python
# src/pyjutsu/models.py
"""Pydantic models for Pyjutsu."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from pyjutsu.enums import BranchTrackingStatus, ChangeState, FileStatus


class FileChange(BaseModel):
    """Represents a file change in the working copy or a commit."""

    path: Path
    status: FileStatus
    old_path: Path | None = None  # For renames/copies

    def __str__(self) -> str:
        """String representation."""
        if self.old_path:
            return f"{self.status.value} {self.old_path} -> {self.path}"
        return f"{self.status.value} {self.path}"


class Change(BaseModel):
    """Represents a jj change (commit)."""

    change_id: str = Field(description="Jujutsu's unique change ID")
    commit_id: str = Field(description="Git-compatible commit hash")
    description: str = Field(description="Commit message")
    author: str = Field(description="Author name <email>")
    timestamp: datetime
    parent_ids: list[str] = Field(default_factory=list)
    state: ChangeState = ChangeState.MUTABLE

    def __str__(self) -> str:
        """String representation."""
        short_id = self.change_id[:12]
        first_line = self.description.split("\n")[0][:50]
        return f"{short_id} {first_line}"


class Branch(BaseModel):
    """Represents a jj branch."""

    name: str
    target_change_id: str = Field(description="Change ID this branch points to")
    target_commit_id: str = Field(description="Commit hash this branch points to")
    tracking_status: BranchTrackingStatus = BranchTrackingStatus.UNTRACKED
    remote_name: str | None = None

    def __str__(self) -> str:
        """String representation."""
        return f"{self.name} -> {self.target_change_id[:12]}"


class WorkspaceStatus(BaseModel):
    """Represents the current workspace status."""

    working_copy_change_id: str
    current_branch: str | None = None
    has_conflicts: bool = False
    modified_files: list[FileChange] = Field(default_factory=list)
    is_colocated: bool = True  # Assume git-colocated by default

    def __str__(self) -> str:
        """String representation."""
        branch_str = f" on {self.current_branch}" if self.current_branch else ""
        files_str = f" ({len(self.modified_files)} files modified)" if self.modified_files else ""
        return f"{self.working_copy_change_id[:12]}{branch_str}{files_str}"


class LogEntry(BaseModel):
    """Single entry from jj log."""

    change: Change
    branches: list[str] = Field(default_factory=list, description="Branches pointing to this change")
    is_working_copy: bool = False

    def __str__(self) -> str:
        """String representation."""
        branches_str = f" ({', '.join(self.branches)})" if self.branches else ""
        wc_str = " @" if self.is_working_copy else ""
        return f"{self.change}{branches_str}{wc_str}"


class DiffSummary(BaseModel):
    """Summary of differences between revisions."""

    from_revision: str
    to_revision: str
    files_changed: list[FileChange]
    insertions: int = 0
    deletions: int = 0

    def __str__(self) -> str:
        """String representation."""
        return f"{len(self.files_changed)} files changed, +{self.insertions} -{self.deletions}"
```

Update `src/pyjutsu/__init__.py` to export models:

```python
# src/pyjutsu/__init__.py
"""Pyjutsu - Pythonic wrapper for Jujutsu VCS."""

from __future__ import annotations

from pyjutsu.enums import BranchTrackingStatus, ChangeState, FileStatus
from pyjutsu.exceptions import (
    ConflictError,
    InvalidRevisionError,
    JjCommandError,
    JjNotFoundError,
    PyjutsuError,
    RepositoryNotFoundError,
)
from pyjutsu.models import Branch, Change, DiffSummary, FileChange, LogEntry, WorkspaceStatus

__version__ = "0.1.0"

__all__ = [
    "Branch",
    "BranchTrackingStatus",
    "Change",
    "ChangeState",
    "ConflictError",
    "DiffSummary",
    "FileChange",
    "FileStatus",
    "InvalidRevisionError",
    "JjCommandError",
    "JjNotFoundError",
    "LogEntry",
    "PyjutsuError",
    "RepositoryNotFoundError",
    "WorkspaceStatus",
]
```

### Testing

Create `tests/unit/test_models.py`:

```python
# tests/unit/test_models.py
"""Test Pydantic models."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from pyjutsu import Branch, Change, FileChange, FileStatus, WorkspaceStatus


def test_file_change_creation() -> None:
    """FileChange model validates correctly."""
    fc = FileChange(path=Path("src/main.py"), status=FileStatus.MODIFIED)
    assert fc.path == Path("src/main.py")
    assert fc.status == FileStatus.MODIFIED
    assert fc.old_path is None


def test_file_change_rename() -> None:
    """FileChange handles renames."""
    fc = FileChange(
        path=Path("new.py"),
        status=FileStatus.RENAMED,
        old_path=Path("old.py"),
    )
    assert fc.old_path == Path("old.py")
    assert "old.py -> new.py" in str(fc)


def test_change_creation() -> None:
    """Change model validates correctly."""
    change = Change(
        change_id="abc123def456",
        commit_id="0" * 40,
        description="Initial commit",
        author="Test User <test@example.com>",
        timestamp=datetime.now(),
        parent_ids=[],
    )
    assert len(change.change_id) == 12
    assert "Initial commit" in change.description


def test_branch_creation() -> None:
    """Branch model validates correctly."""
    branch = Branch(
        name="main",
        target_change_id="abc123",
        target_commit_id="0" * 40,
    )
    assert branch.name == "main"
    assert branch.remote_name is None


def test_workspace_status_creation() -> None:
    """WorkspaceStatus model validates correctly."""
    status = WorkspaceStatus(
        working_copy_change_id="abc123",
        current_branch="main",
        modified_files=[
            FileChange(path=Path("test.py"), status=FileStatus.MODIFIED)
        ],
    )
    assert status.current_branch == "main"
    assert len(status.modified_files) == 1
    assert not status.has_conflicts
```

Run tests:

```bash
pytest tests/unit/test_models.py -v
```

---

## Step 4: Command Wrapper

### Code Work

Create `src/pyjutsu/_commands.py`:

```python
# src/pyjutsu/_commands.py
"""Internal command execution wrapper using sh library."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sh import Command, ErrorReturnCode

from pyjutsu.exceptions import JjCommandError, JjNotFoundError


def check_jj_installed() -> None:
    """Verify jj is installed and accessible."""
    try:
        jj = Command("jj")
        jj("--version")
    except (ErrorReturnCode, Exception) as e:
        raise JjNotFoundError() from e


class JjCommand:
    """Wrapper around jj command using sh library."""

    def __init__(self, repo_path: Path) -> None:
        """Initialize command wrapper for repository.

        Args:
            repo_path: Path to jj repository

        Raises:
            JjNotFoundError: If jj is not installed
        """
        check_jj_installed()
        self.repo_path = repo_path
        self._jj = Command("jj").bake(_cwd=str(repo_path), _tty_out=False)

    def run(self, *args: str, **kwargs: Any) -> str:
        """Run jj command and return stdout.

        Args:
            *args: Command arguments
            **kwargs: Additional sh keyword arguments

        Returns:
            Command stdout as string

        Raises:
            JjCommandError: If command fails
        """
        try:
            result = self._jj(*args, **kwargs)
            return str(result).strip()
        except ErrorReturnCode as e:
            raise JjCommandError(
                command=f"jj {' '.join(args)}",
                returncode=e.exit_code,
                stdout=str(e.stdout) if e.stdout else "",
                stderr=str(e.stderr) if e.stderr else "",
            ) from e

    def run_lines(self, *args: str, **kwargs: Any) -> list[str]:
        """Run jj command and return stdout as lines.

        Args:
            *args: Command arguments
            **kwargs: Additional sh keyword arguments

        Returns:
            List of output lines (empty lines removed)

        Raises:
            JjCommandError: If command fails
        """
        output = self.run(*args, **kwargs)
        return [line for line in output.split("\n") if line.strip()]
```

### Testing

Create `tests/unit/test_commands.py`:

```python
# tests/unit/test_commands.py
"""Test command wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu._commands import JjCommand, check_jj_installed
from pyjutsu.exceptions import JjNotFoundError


def test_check_jj_installed() -> None:
    """check_jj_installed succeeds if jj is available."""
    # This test assumes jj is installed in test environment
    # If not installed, it should raise JjNotFoundError
    try:
        check_jj_installed()
    except JjNotFoundError:
        pytest.skip("jj not installed")


def test_jj_command_init_checks_installation(tmp_path: Path) -> None:
    """JjCommand init checks for jj installation."""
    # This will only fail if jj is not installed
    try:
        cmd = JjCommand(tmp_path)
        assert cmd.repo_path == tmp_path
    except JjNotFoundError:
        pytest.skip("jj not installed")


def test_jj_command_stores_repo_path(tmp_path: Path) -> None:
    """JjCommand stores repository path."""
    try:
        cmd = JjCommand(tmp_path)
        assert cmd.repo_path == tmp_path
    except JjNotFoundError:
        pytest.skip("jj not installed")
```

Run tests:

```bash
pytest tests/unit/test_commands.py -v
```

---

## Step 5: Basic Client Structure

### Code Work

Create `src/pyjutsu/client.py`:

```python
# src/pyjutsu/client.py
"""Main Pyjutsu client."""

from __future__ import annotations

from pathlib import Path

from pyjutsu._commands import JjCommand
from pyjutsu.exceptions import RepositoryNotFoundError
from pyjutsu.models import WorkspaceStatus


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
        # Try to run a simple jj command to verify it's a repo
        try:
            self._cmd.run("status")
        except Exception as e:
            raise RepositoryNotFoundError(str(self.repo_path)) from e

    @classmethod
    def init(cls, path: Path | str = ".", git_repo: str | None = None) -> JjClient:
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

        # Create temporary command to run init
        cmd = JjCommand(path)

        if git_repo:
            # Clone git repo first, then init jj
            from sh import git

            git("clone", git_repo, str(path))
            cmd.run("git", "init", "--git-repo", str(path))
        else:
            cmd.run("git", "init", str(path))

        return cls(path)

    def __repr__(self) -> str:
        """String representation."""
        return f"JjClient({self.repo_path})"
```

Update `src/pyjutsu/__init__.py`:

```python
# src/pyjutsu/__init__.py
"""Pyjutsu - Pythonic wrapper for Jujutsu VCS."""

from __future__ import annotations

from pyjutsu.client import JjClient
from pyjutsu.enums import BranchTrackingStatus, ChangeState, FileStatus
from pyjutsu.exceptions import (
    ConflictError,
    InvalidRevisionError,
    JjCommandError,
    JjNotFoundError,
    PyjutsuError,
    RepositoryNotFoundError,
)
from pyjutsu.models import Branch, Change, DiffSummary, FileChange, LogEntry, WorkspaceStatus

__version__ = "0.1.0"

__all__ = [
    "Branch",
    "BranchTrackingStatus",
    "Change",
    "ChangeState",
    "ConflictError",
    "DiffSummary",
    "FileChange",
    "FileStatus",
    "InvalidRevisionError",
    "JjClient",
    "JjCommandError",
    "JjNotFoundError",
    "LogEntry",
    "PyjutsuError",
    "RepositoryNotFoundError",
    "WorkspaceStatus",
]
```

### Testing

Create `tests/integration/test_client_init.py`:

```python
# tests/integration/test_client_init.py
"""Integration tests for client initialization."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient, RepositoryNotFoundError


def test_client_init_creates_repository(tmp_path: Path) -> None:
    """JjClient.init creates a new repository."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    assert client.repo_path == repo_path.resolve()
    assert repo_path.exists()
    assert (repo_path / ".jj").exists()


def test_client_init_on_nonexistent_path_raises(tmp_path: Path) -> None:
    """JjClient raises RepositoryNotFoundError for invalid path."""
    nonexistent = tmp_path / "does-not-exist"
    with pytest.raises(RepositoryNotFoundError):
        JjClient(nonexistent)


def test_client_repr(tmp_path: Path) -> None:
    """JjClient has useful repr."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)
    assert str(repo_path) in repr(client)
```

Run tests:

```bash
pytest tests/integration/test_client_init.py -v
# Note: These tests require jj to be installed
```

---

## Step 6: Status Implementation

### Code Work

Add status method to `src/pyjutsu/client.py`:

```python
# Add to JjClient class in src/pyjutsu/client.py

    def status(self) -> WorkspaceStatus:
        """Get current workspace status.

        Returns:
            WorkspaceStatus with current state

        Raises:
            JjCommandError: If status command fails
        """
        # Get working copy change ID
        output = self._cmd.run("log", "-r", "@", "--no-graph", "-T", "change_id")
        working_copy_id = output.strip()

        # Get current branch if any
        branch_output = self._cmd.run("log", "-r", "@", "--no-graph", "-T", "branches")
        current_branch = branch_output.strip() or None

        # Get file changes
        status_output = self._cmd.run("status")
        modified_files = self._parse_status_output(status_output)

        # Check for conflicts
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
            output: Raw jj status output

        Returns:
            List of FileChange objects
        """
        from pyjutsu.enums import FileStatus

        changes: list[FileChange] = []
        lines = output.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line or line.startswith("Working copy"):
                continue

            # Format: "A path" or "M path" or "R old_path => new_path"
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue

            status_code, path_info = parts
            file_status = FileStatus.from_code(status_code)

            if "=>" in path_info:
                # Rename: "old_path => new_path"
                old, new = path_info.split("=>")
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
```

### Testing

Create `tests/integration/test_status.py`:

```python
# tests/integration/test_status.py
"""Integration tests for status command."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient


def test_status_on_new_repo(tmp_path: Path) -> None:
    """Status works on newly initialized repository."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    status = client.status()
    assert status.working_copy_change_id
    assert len(status.working_copy_change_id) >= 12
    assert not status.has_conflicts
    assert status.modified_files == []


def test_status_detects_new_file(tmp_path: Path) -> None:
    """Status detects newly created files."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    # Create a new file
    test_file = repo_path / "test.txt"
    test_file.write_text("Hello, world!")

    status = client.status()
    assert len(status.modified_files) >= 1
    # Find our test file
    test_changes = [f for f in status.modified_files if f.path.name == "test.txt"]
    assert len(test_changes) == 1
```

Run tests:

```bash
pytest tests/integration/test_status.py -v
```

---

## Step 7: Describe and New Commands

### Code Work

Add methods to `src/pyjutsu/client.py`:

```python
# Add to JjClient class in src/pyjutsu/client.py

    def describe(self, message: str, revision: str = "@") -> None:
        """Set the description (commit message) for a change.

        Args:
            message: Commit message
            revision: Revision to describe (default: working copy)

        Raises:
            JjCommandError: If describe fails
        """
        self._cmd.run("describe", "-r", revision, "-m", message)

    def new(self, revision: str | None = None) -> str:
        """Create a new working copy change.

        Args:
            revision: Optional revision to start from

        Returns:
            Change ID of the new working copy

        Raises:
            JjCommandError: If new fails
        """
        if revision:
            self._cmd.run("new", revision)
        else:
            self._cmd.run("new")

        # Get the new working copy change ID
        output = self._cmd.run("log", "-r", "@", "--no-graph", "-T", "change_id")
        return output.strip()
```

### Testing

Create `tests/integration/test_describe_new.py`:

```python
# tests/integration/test_describe_new.py
"""Integration tests for describe and new commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient


def test_describe_sets_message(tmp_path: Path) -> None:
    """Describe sets commit message."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    # Create a file and describe
    test_file = repo_path / "test.txt"
    test_file.write_text("content")

    client.describe("Initial commit")

    # Verify message was set (check via log)
    output = client._cmd.run("log", "-r", "@", "--no-graph", "-T", "description")
    assert "Initial commit" in output


def test_new_creates_working_copy(tmp_path: Path) -> None:
    """New creates a new working copy change."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    old_status = client.status()
    old_change_id = old_status.working_copy_change_id

    # Create new working copy
    new_change_id = client.new()

    # Verify we have a different change ID
    assert new_change_id != old_change_id

    new_status = client.status()
    assert new_status.working_copy_change_id == new_change_id
```

Run tests:

```bash
pytest tests/integration/test_describe_new.py -v
```

---

## Step 8: Branch Operations

### Code Work

Add branch methods to `src/pyjutsu/client.py`:

```python
# Add to JjClient class in src/pyjutsu/client.py

    def branch_create(self, name: str, revision: str = "@") -> Branch:
        """Create a new branch.

        Args:
            name: Branch name
            revision: Revision to create branch at (default: working copy)

        Returns:
            Branch object

        Raises:
            JjCommandError: If branch creation fails
        """
        self._cmd.run("branch", "create", name, "-r", revision)

        # Get the change and commit IDs for this revision
        change_id = self._cmd.run("log", "-r", revision, "--no-graph", "-T", "change_id").strip()
        commit_id = self._cmd.run("log", "-r", revision, "--no-graph", "-T", "commit_id").strip()

        return Branch(
            name=name,
            target_change_id=change_id,
            target_commit_id=commit_id,
        )

    def branch_list(self) -> list[Branch]:
        """List all branches.

        Returns:
            List of Branch objects

        Raises:
            JjCommandError: If branch list fails
        """
        output = self._cmd.run("branch", "list")
        branches: list[Branch] = []

        for line in output.strip().split("\n"):
            line = line.strip()
            if not line:
                continue

            # Parse format: "branch_name: change_id"
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue

            name = parts[0].strip()
            change_id = parts[1].strip().split()[0]  # Get first token (change ID)

            # Get commit ID for this branch
            try:
                commit_id = self._cmd.run("log", "-r", name, "--no-graph", "-T", "commit_id").strip()
            except Exception:
                commit_id = "0" * 40  # Fallback

            branches.append(
                Branch(
                    name=name,
                    target_change_id=change_id,
                    target_commit_id=commit_id,
                )
            )

        return branches

    def branch_delete(self, name: str) -> None:
        """Delete a branch.

        Args:
            name: Branch name to delete

        Raises:
            JjCommandError: If branch deletion fails
        """
        self._cmd.run("branch", "delete", name)

    def branch_set(self, name: str, revision: str) -> None:
        """Move an existing branch to a different revision.

        Args:
            name: Branch name
            revision: Revision to move branch to

        Raises:
            JjCommandError: If branch set fails
        """
        self._cmd.run("branch", "set", name, "-r", revision)
```

### Testing

Create `tests/integration/test_branches.py`:

```python
# tests/integration/test_branches.py
"""Integration tests for branch operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient


def test_branch_create(tmp_path: Path) -> None:
    """Can create a new branch."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    branch = client.branch_create("feature-x")
    assert branch.name == "feature-x"
    assert branch.target_change_id


def test_branch_list(tmp_path: Path) -> None:
    """Can list branches."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    client.branch_create("main")
    client.branch_create("dev")

    branches = client.branch_list()
    branch_names = [b.name for b in branches]

    assert "main" in branch_names
    assert "dev" in branch_names


def test_branch_delete(tmp_path: Path) -> None:
    """Can delete a branch."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    client.branch_create("temp")
    client.branch_delete("temp")

    branches = client.branch_list()
    branch_names = [b.name for b in branches]
    assert "temp" not in branch_names
```

Run tests:

```bash
pytest tests/integration/test_branches.py -v
```

---

## Step 9: Log Implementation

### Code Work

Add log method to `src/pyjutsu/client.py`:

```python
# Add to JjClient class in src/pyjutsu/client.py

    def log(self, revset: str = "@", limit: int = 10) -> list[LogEntry]:
        """Get commit log.

        Args:
            revset: Revset expression (default: working copy)
            limit: Maximum number of entries

        Returns:
            List of LogEntry objects

        Raises:
            JjCommandError: If log command fails
        """
        from datetime import datetime

        # Use template to get structured output
        template = "change_id:{change_id}\\ncommit_id:{commit_id}\\ndesc:{description}\\nauthor:{author}\\ntime:{committer_timestamp}\\n---\\n"  # noqa: E501
        output = self._cmd.run("log", "-r", revset, "-l", str(limit), "-T", template, "--no-graph")

        entries: list[LogEntry] = []
        current_entry: dict[str, str] = {}

        for line in output.split("\n"):
            line = line.strip()

            if line == "---":
                # End of entry
                if current_entry:
                    try:
                        change = Change(
                            change_id=current_entry.get("change_id", ""),
                            commit_id=current_entry.get("commit_id", ""),
                            description=current_entry.get("desc", ""),
                            author=current_entry.get("author", ""),
                            timestamp=datetime.fromisoformat(
                                current_entry.get("time", "").replace("Z", "+00:00")
                            ),
                        )
                        entries.append(LogEntry(change=change))
                    except Exception:
                        pass  # Skip malformed entries
                    current_entry = {}
            elif ":" in line:
                key, value = line.split(":", 1)
                current_entry[key] = value

        return entries
```

### Testing

Create `tests/integration/test_log.py`:

```python
# tests/integration/test_log.py
"""Integration tests for log command."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient


def test_log_returns_entries(tmp_path: Path) -> None:
    """Log returns commit entries."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    # Create some commits
    test_file = repo_path / "test.txt"
    test_file.write_text("v1")
    client.describe("First commit")
    client.new()

    test_file.write_text("v2")
    client.describe("Second commit")

    entries = client.log(limit=5)
    assert len(entries) >= 2

    # Verify structure
    for entry in entries:
        assert entry.change.change_id
        assert entry.change.commit_id
        assert entry.change.author


def test_log_respects_limit(tmp_path: Path) -> None:
    """Log respects limit parameter."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    # Create several commits
    test_file = repo_path / "test.txt"
    for i in range(5):
        test_file.write_text(f"version {i}")
        client.describe(f"Commit {i}")
        client.new()

    entries = client.log(limit=3)
    assert len(entries) <= 3
```

Run tests:

```bash
pytest tests/integration/test_log.py -v
```

---

## Step 10: Diff Implementation

### Code Work

Add diff method to `src/pyjutsu/client.py`:

```python
# Add to JjClient class in src/pyjutsu/client.py

    def diff(self, from_rev: str | None = None, to_rev: str | None = None) -> DiffSummary:
        """Get diff between revisions.

        Args:
            from_rev: Starting revision (default: parent of working copy)
            to_rev: Ending revision (default: working copy)

        Returns:
            DiffSummary object

        Raises:
            JjCommandError: If diff command fails
        """
        args = ["diff"]

        if from_rev and to_rev:
            args.extend(["--from", from_rev, "--to", to_rev])
        elif from_rev:
            args.extend(["--from", from_rev])
        else:
            # Default: diff working copy against parent
            args.extend(["-r", "@"])

        output = self._cmd.run(*args, "--summary")

        # Parse summary output
        files = self._parse_status_output(output)

        return DiffSummary(
            from_revision=from_rev or "@-",
            to_revision=to_rev or "@",
            files_changed=files,
            insertions=0,  # Would need --stat for actual counts
            deletions=0,
        )
```

### Testing

Create `tests/integration/test_diff.py`:

```python
# tests/integration/test_diff.py
"""Integration tests for diff command."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient


def test_diff_detects_changes(tmp_path: Path) -> None:
    """Diff detects file changes."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    # Create and modify file
    test_file = repo_path / "test.txt"
    test_file.write_text("initial content")

    diff = client.diff()
    assert len(diff.files_changed) >= 1

    # Find our file
    test_changes = [f for f in diff.files_changed if f.path.name == "test.txt"]
    assert len(test_changes) >= 1


def test_diff_empty_on_clean_working_copy(tmp_path: Path) -> None:
    """Diff is empty on clean working copy."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    # Commit everything
    client.describe("Initial state")
    client.new()

    diff = client.diff()
    assert len(diff.files_changed) == 0
```

Run tests:

```bash
pytest tests/integration/test_diff.py -v
```

---

## Step 11: Git Operations

### Code Work

Add git methods to `src/pyjutsu/client.py`:

```python
# Add to JjClient class in src/pyjutsu/client.py

    def git_fetch(self, remote: str = "origin") -> None:
        """Fetch from git remote.

        Args:
            remote: Remote name (default: origin)

        Raises:
            JjCommandError: If fetch fails
        """
        self._cmd.run("git", "fetch", "--remote", remote)

    def git_push(
        self,
        branch: str | None = None,
        remote: str = "origin",
        force: bool = False,
    ) -> None:
        """Push branch to git remote.

        Args:
            branch: Branch name (default: current branch)
            remote: Remote name (default: origin)
            force: Force push

        Raises:
            JjCommandError: If push fails
        """
        args = ["git", "push"]

        if branch:
            args.extend(["--branch", branch])
        else:
            args.append("--all")

        if remote != "origin":
            args.extend(["--remote", remote])

        if force:
            args.append("--force")

        self._cmd.run(*args)

    def git_remote_add(self, name: str, url: str) -> None:
        """Add a git remote.

        Args:
            name: Remote name
            url: Remote URL

        Raises:
            JjCommandError: If remote add fails
        """
        # Use git directly for remote management
        from sh import git

        git("remote", "add", name, url, _cwd=str(self.repo_path))
```

### Testing

Create `tests/integration/test_git_operations.py`:

```python
# tests/integration/test_git_operations.py
"""Integration tests for git operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient, JjCommandError


def test_git_remote_add(tmp_path: Path) -> None:
    """Can add git remote."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    # Add a remote (won't actually connect)
    client.git_remote_add("origin", "https://github.com/example/repo.git")

    # Verify remote was added
    from sh import git

    output = git("remote", "-v", _cwd=str(repo_path))
    assert "origin" in str(output)


def test_git_fetch_fails_without_remote(tmp_path: Path) -> None:
    """Git fetch fails gracefully without remote."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    with pytest.raises(JjCommandError):
        client.git_fetch()


def test_git_push_fails_without_remote(tmp_path: Path) -> None:
    """Git push fails gracefully without remote."""
    repo_path = tmp_path / "test-repo"
    client = JjClient.init(repo_path)

    client.branch_create("main")

    with pytest.raises(JjCommandError):
        client.git_push(branch="main")
```

Run tests:

```bash
pytest tests/integration/test_git_operations.py -v
```

---

## Step 12: Documentation and Final Integration

### Code Work

Create comprehensive docstrings and examples.

Add `examples/basic_workflow.py`:

```python
#!/usr/bin/env python3
"""Basic Pyjutsu workflow example."""

from pathlib import Path
from pyjutsu import JjClient

# Initialize a new repository
repo_path = Path("./example-repo")
client = JjClient.init(repo_path)

print(f"Initialized repository at {repo_path}")

# Check status
status = client.status()
print(f"Working copy: {status.working_copy_change_id[:12]}")

# Create a file
readme = repo_path / "README.md"
readme.write_text("# Example Project\n\nHello, Pyjutsu!")

# Check status again
status = client.status()
print(f"Modified files: {len(status.modified_files)}")
for file in status.modified_files:
    print(f"  {file}")

# Describe the change
client.describe("Add README")
print("Described change")

# Create a branch
branch = client.branch_create("main")
print(f"Created branch: {branch.name}")

# Create new working copy
new_change_id = client.new()
print(f"New working copy: {new_change_id[:12]}")

# View log
print("\nCommit history:")
for entry in client.log(limit=5):
    print(f"  {entry}")
```

### Testing

Create comprehensive integration test:

```python
# tests/integration/test_full_workflow.py
"""End-to-end workflow test."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyjutsu import JjClient


def test_full_workflow(tmp_path: Path) -> None:
    """Complete workflow from init to branch creation."""
    repo_path = tmp_path / "workflow-test"

    # Initialize
    client = JjClient.init(repo_path)
    assert client.repo_path.exists()

    # Check initial status
    status = client.status()
    assert status.working_copy_change_id
    initial_change_id = status.working_copy_change_id

    # Create file
    test_file = repo_path / "test.txt"
    test_file.write_text("Hello, world!")

    # Verify file detected
    status = client.status()
    assert len(status.modified_files) > 0

    # Describe and create new change
    client.describe("Initial commit")
    new_change_id = client.new()
    assert new_change_id != initial_change_id

    # Create branch on the commit
    client.describe("Empty commit")  # Describe new working copy
    branch = client.branch_create("main", revision="@-")
    assert branch.name == "main"

    # View history
    log = client.log(limit=5)
    assert len(log) >= 2

    # Create another change
    test_file.write_text("Updated content")
    client.describe("Update test file")

    # Check diff
    diff = client.diff()
    assert len(diff.files_changed) > 0

    print("Full workflow completed successfully!")
```

Run all tests:

```bash
# Run all unit tests
pytest tests/unit/ -v

# Run all integration tests
pytest tests/integration/ -v

# Run with coverage
pytest --cov=pyjutsu --cov-report=html tests/

# Type check
mypy src/

# Lint
ruff check src/
```

---

## Step 13: Package Distribution

### Code Work

Create `README.md` (use the one we created earlier).

Create `LICENSE`:

```
MIT License

Copyright (c) 2024 [Your Name]

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Testing

Build and test package:

```bash
# Build package
uv build

# Install from wheel
uv pip install dist/pyjutsu-0.1.0-py3-none-any.whl

# Test installation
python -c "from pyjutsu import JjClient; print('Success!')"

# Test in clean environment
uv venv test-env
source test-env/bin/activate
uv pip install dist/pyjutsu-0.1.0-py3-none-any.whl
python -c "from pyjutsu import JjClient; print(JjClient.__doc__)"
deactivate
```

---

## Completion Checklist

- [ ] Step 0: Project setup complete
- [ ] Step 1: Exception hierarchy implemented and tested
- [ ] Step 2: Enums defined and tested
- [ ] Step 3: Pydantic models created and tested
- [ ] Step 4: Command wrapper implemented and tested
- [ ] Step 5: Basic client structure complete
- [ ] Step 6: Status implementation working
- [ ] Step 7: Describe/new commands working
- [ ] Step 8: Branch operations complete
- [ ] Step 9: Log implementation working
- [ ] Step 10: Diff implementation working
- [ ] Step 11: Git operations implemented
- [ ] Step 12: Documentation and examples complete
- [ ] Step 13: Package builds and installs successfully
- [ ] All unit tests passing
- [ ] All integration tests passing
- [ ] Type checking passes
- [ ] Linting passes
- [ ] Code coverage > 80%

---

## Notes for Developers

### Prerequisites

- Python 3.13+ installed
- `uv` package manager installed
- `jj` (Jujutsu) installed and in PATH
- Unix-like OS (Linux, macOS, BSD)

### Development Workflow

1. Create feature branch
2. Write tests first (TDD)
3. Implement feature
4. Run tests locally
5. Check types and lint
6. Commit with descriptive message

### Testing Philosophy

- **Unit tests**: Fast, isolated, mock external dependencies
- **Integration tests**: Use real jj commands, require jj installation
- **Test organization**: Mirror source structure in tests/

### Common Patterns

- All file paths use `pathlib.Path`
- All command output parsed into Pydantic models
- Errors wrapped in custom exceptions
- Repository path baked into command wrapper

### Extending the Library

To add a new jj command:

1. Add method to `JjClient` class
2. Parse output into Pydantic model (create new model if needed)
3. Add unit tests for parsing logic
4. Add integration test using real jj
5. Update `__all__` in `__init__.py`
6. Add docstring with examples
