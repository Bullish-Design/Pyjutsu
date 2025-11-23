# README.md

# Pyjutsu

A Pythonic wrapper around [Jujutsu (jj)](https://github.com/martinvonz/jj) version control system, providing a clean Pydantic-based interface for building workflows and automation.

## Overview

Pyjutsu wraps the `jj` CLI in a type-safe, ergonomic Python API using the [`sh`](https://github.com/amoffat/sh) library for subprocess handling. It assumes git-colocated repositories (jj managing a git repo) and provides explicit operations for both local jj commands and git remote interactions.

## Features

- **Type-safe**: All operations use Pydantic models for input/output
- **Clean subprocess handling**: Built on `sh` library for elegant command execution
- **Git-colocated**: Designed for jj repos backed by git remotes
- **Explicit operations**: Clear methods for both jj and git operations
- **Best practices**: Follows modern Python patterns (3.13+)
- **Extensible**: Easy to add new jj command wrappers

## Installation

```bash
# Using uv (recommended)
uv pip install pyjutsu

# Using pip
pip install pyjutsu
```

### Requirements

- Python 3.13+
- Unix-like OS (Linux, macOS, BSD) - Windows not supported due to `sh` library
- Jujutsu (`jj`) installed and available in PATH
- Git (for colocated repositories)

## Quick Start

```python
from pyjutsu import JjClient
from pathlib import Path

# Initialize client for a repository
client = JjClient(Path("./my-repo"))

# Get repository status
status = client.status()
print(f"Working copy: {status.working_copy_id}")
print(f"Current branch: {status.current_branch}")

# Create a new change
client.describe("Add new feature")
client.new()  # Create new working copy

# View recent history
log = client.log(limit=5)
for entry in log.entries:
    print(f"{entry.change_id[:8]}: {entry.description}")

# Push to git remote
client.git_push(branch="main", remote="origin")
```

## Core Concepts

### Repository State

Every operation returns structured Pydantic models:

```python
from pyjutsu.models import RepositoryStatus, LogEntry, FileStatus

# Get comprehensive repo state
status: RepositoryStatus = client.status()

# Access typed fields
change_id: str = status.working_copy_id
branch: str | None = status.current_branch
files: list[FileStatus] = status.modified_files
```

### Change Management

Jujutsu's change-based model is exposed directly:

```python
# Describe current change
client.describe("Implement user authentication")

# Create new working copy
client.new()

# Edit a specific change
client.edit(change_id="abc123")

# Abandon changes
client.abandon(change_id="def456")
```

### Branch Operations

```python
# Create branch at current change
client.branch_create("feature-x")

# Move branch to different change
client.branch_set("feature-x", revision="@-")

# List all branches
branches = client.branch_list()
for branch in branches:
    print(f"{branch.name} -> {branch.target}")

# Delete branch
client.branch_delete("old-feature")
```

### Git Integration

Explicit git operations for remote sync:

```python
# Fetch from remote
client.git_fetch(remote="origin")

# Push branch to remote
client.git_push(branch="main", remote="origin")

# Pull and rebase
client.git_fetch(remote="origin")
client.rebase(destination="origin/main")
```

### Workspace Management

```python
# Add new workspace
client.workspace_add(path=Path("../feature-workspace"))

# List workspaces
workspaces = client.workspace_list()

# Remove workspace
client.workspace_forget(name="old-workspace")
```

## Architecture

```
pyjutsu/
├── __init__.py           # Public API exports
├── client.py             # JjClient - main interface
├── models.py             # Pydantic models for all entities
├── enums.py              # Status codes and enums
├── config.py             # Configuration management
├── exceptions.py         # Custom exceptions
└── _commands.py          # Internal sh-based command wrapper
```

### Design Principles

1. **Fail Fast**: Validate jj installation and requirements immediately
2. **Type Safety**: Pydantic models for all data structures
3. **Explicit > Implicit**: Clear method names, no magic
4. **Git Colocated**: Assume git backend, expose git operations
5. **Extensible**: Easy to add new jj commands as methods
6. **Clean subprocess**: Use `sh` library for elegant command execution

### Using `sh` for Command Execution

Pyjutsu uses the [`sh`](https://github.com/amoffat/sh) library internally for clean subprocess handling:

- **Baked commands**: Repository path is "baked" into commands for clean, DRY code
- **Error handling**: `sh` exceptions are caught and wrapped in Pydantic-friendly errors
- **Output parsing**: Raw command output is parsed into structured Pydantic models
- **Explicit interface**: The `sh` magic is hidden behind clear method names

Internal implementation pattern:
```python
# Inside JjClient
self._jj = Command("jj").bake(_cwd=str(repo_path), _tty_out=False)
```

This gives us clean command execution without repetitive `cwd` parameters while maintaining full type safety through Pydantic models.

## Error Handling

Pyjutsu raises specific exceptions for different failure modes:

```python
from pyjutsu.exceptions import (
    JjNotFoundError,        # jj not installed
    JjCommandError,         # Command failed
    RepositoryNotFoundError,# Not a jj repo
    ConflictError,          # Merge conflicts
)

try:
    client = JjClient(Path("./repo"))
    client.describe("New feature")
except JjNotFoundError:
    print("Install jj first: https://github.com/martinvonz/jj")
except JjCommandError as e:
    print(f"Command failed: {e.stderr}")
    print(f"Exit code: {e.returncode}")
```

## Examples

### Clone and Start Work

```python
from pyjutsu import JjClient
from pathlib import Path

# Clone a git repo and initialize jj
repo_path = Path("./my-project")
client = JjClient.clone_git(
    url="https://github.com/user/repo.git",
    path=repo_path
)

# Create a feature branch
client.branch_create("feature/new-widget")

# Make changes to files...
# Then describe the change
client.describe("Add widget component\n\nImplements #123")

# Create new working copy for next change
client.new()
```

### Review Changes

```python
# Get diff for current change
diff = client.diff()
print(diff.text)

# Get diff between specific revisions
diff = client.diff(from_rev="@-", to_rev="@")

# Show file status
status = client.status()
for file in status.modified_files:
    print(f"{file.status.value}: {file.path}")
```

### Sync with Remote

```python
# Fetch latest from origin
client.git_fetch(remote="origin")

# Check if rebase needed
log = client.log(revset="origin/main..@", limit=10)
if log.entries:
    print(f"Behind by {len(log.entries)} commits")
    
    # Rebase onto origin/main
    client.rebase(destination="origin/main")

# Push your branch
client.git_push(branch="feature/new-widget", remote="origin")
```

### Multiple Workspaces

```python
# Create workspace for bug fix
bug_path = Path("../bugfix-workspace")
client.workspace_add(path=bug_path)

# Work in the new workspace
bug_client = JjClient(bug_path)
bug_client.branch_create("bugfix/critical")
bug_client.describe("Fix critical bug")

# Original workspace unchanged
status = client.status()  # Still on feature branch
```

## Development

### Running Tests

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Run tests with real jj repos
pytest tests/

# Run with coverage
pytest --cov=pyjutsu tests/
```

### Testing Against Real Repository

Tests use the official jj repository:

```bash
# Tests automatically clone https://github.com/martinvonz/jj
# into temp directories for integration testing
pytest tests/integration/
```

### Implementation Notes

- Commands are executed via `sh` library with baked repository context
- All command output is parsed into Pydantic models before returning
- The `sh.Command` instance is private (`_jj`) and not exposed in public API
- Each public method corresponds to a specific jj workflow operation

## Roadmap

### Current (MVP)
- ✅ Core jj operations (status, log, diff)
- ✅ Change management (describe, new, edit)
- ✅ Branch operations
- ✅ Git push/pull/fetch
- ✅ Basic workspace management

### Future
- Advanced operations (squash, split, absorb)
- Conflict resolution helpers
- Multiple workspace checkouts
- CLI interface with Typer
- Rich output formatting
- Hooks and event system

## Contributing

Contributions welcome! Please:

1. Add tests for new functionality
2. Follow existing patterns (Pydantic models, explicit methods)
3. Update documentation
4. Ensure `mypy` and `ruff` pass

## License

MIT License - see LICENSE file

## Links

- [Jujutsu VCS](https://github.com/martinvonz/jj)
- [Documentation](https://martinvonz.github.io/jj/)
- [PyPI Package](https://pypi.org/project/pyjutsu/)

## Acknowledgments

Built on top of:
- [Jujutsu](https://github.com/martinvonz/jj) version control system by Martin von Zweigbergk and contributors
- [sh](https://github.com/amoffat/sh) subprocess library by Andrew Moffat