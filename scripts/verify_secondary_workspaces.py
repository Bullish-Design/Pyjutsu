#!/usr/bin/env python3
"""Run the secondary-workspace acceptance contract against a live jj repository."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import warnings
from pathlib import Path

from pyjutsu import PyjutsuError, Revset, RevsetError, Workspace


def report(event: str, **fields: object) -> None:
    """Write one structured result line."""
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def run_jj(cwd: Path, *args: str, check: bool = True) -> str:
    """Run the pinned jj command and record its complete result."""
    result = subprocess.run(
        ["jj", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    report(
        "command",
        argv=["jj", *args],
        cwd=str(cwd),
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"jj command failed with exit code {result.returncode}: {args!r}")
    return result.stdout


def jj_value(cwd: Path, revision: str, template: str) -> str:
    """Render one template value for one revision."""
    return run_jj(cwd, "log", "-r", revision, "--no-graph", "-T", template).strip()


def jj_parent_ids(cwd: Path, revision: str) -> list[str]:
    """Return the commit IDs of one revision's parents."""
    value = jj_value(cwd, revision, 'parents.map(|c| c.commit_id()).join(",")')
    return [item for item in value.split(",") if item]


def operation_ids(cwd: Path) -> list[str]:
    """Return operation IDs from newest to oldest."""
    output = run_jj(cwd, "op", "log", "--no-graph", "-T", 'id ++ "\\n"')
    return [line for line in output.splitlines() if line]


def operation_descriptions(cwd: Path, limit: int) -> list[str]:
    """Return recent operation descriptions from newest to oldest."""
    output = run_jj(
        cwd,
        "op",
        "log",
        "--no-graph",
        "--limit",
        str(limit),
        "-T",
        'description ++ "\\n"',
    )
    return [line for line in output.splitlines() if line]


def author_from_root(workspace: Workspace, description: str) -> tuple[str, str]:
    """Author a live commit and return its configured identity."""
    with workspace.transaction(description) as transaction:
        commit = transaction.new("root()")
    return commit.author.name, commit.author.email


def assert_equal(name: str, actual: object, expected: object) -> None:
    """Record a passing equality check or raise with both values."""
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
    report("assertion", name=name, status="passed", value=actual)


def assert_true(name: str, condition: bool) -> None:
    """Record a passing Boolean check or raise."""
    if not condition:
        raise AssertionError(name)
    report("assertion", name=name, status="passed")


def configure_isolated_environment(root: Path) -> None:
    """Remove ambient jj authoring configuration from the live run."""
    home = root / "home"
    config_home = root / "config"
    home.mkdir()
    config_home.mkdir()
    os.environ["HOME"] = str(home)
    os.environ["XDG_CONFIG_HOME"] = str(config_home)
    os.environ["JJ_CONFIG"] = ""
    os.environ["NO_COLOR"] = "1"
    os.environ["PAGER"] = "cat"
    for name in (
        "JJ_USER",
        "JJ_EMAIL",
        "JJ_TIMESTAMP",
        "JJ_OP_TIMESTAMP",
        "JJ_OP_HOSTNAME",
        "JJ_OP_USERNAME",
        "JJ_EDITOR",
        "JJ_PAGER",
        "JJ_RANDOMNESS_SEED",
    ):
        os.environ.pop(name, None)


def make_conflict_repository(root: Path) -> Path:
    """Create a repository with two conflicting parent commits and an empty merge @."""
    repository = root / "repository"
    repository.mkdir()
    run_jj(repository, "git", "init", "--colocate")
    run_jj(repository, "config", "set", "--repo", "user.name", "Live Repository Author")
    run_jj(
        repository,
        "config",
        "set",
        "--repo",
        "user.email",
        "repository@live.invalid",
    )

    (repository / "common.txt").write_text("common\n")
    (repository / "conflict.txt").write_text("base\n")
    run_jj(repository, "describe", "-m", "base")
    run_jj(repository, "bookmark", "create", "base", "-r", "@")

    run_jj(repository, "new", "base", "-m", "side A")
    (repository / "conflict.txt").write_text("side A\n")
    (repository / "a-only.txt").write_text("A\n")
    run_jj(repository, "bookmark", "create", "sideA", "-r", "@")

    run_jj(repository, "new", "base", "-m", "side B")
    (repository / "conflict.txt").write_text("side B\n")
    (repository / "b-only.txt").write_text("B\n")
    run_jj(repository, "bookmark", "create", "sideB", "-r", "@")

    run_jj(repository, "new", "sideA", "sideB", "-m", "source merge")
    run_jj(repository, "sparse", "set", "--clear", "--add", "common.txt")
    return repository


def verify_workspace_creation(root: Path, repository: Path) -> dict[str, Path]:
    """Verify default, explicit, typed, merge, validation, sparse, and operation behavior."""
    source = Workspace.load(repository)
    expected_default_parents = jj_parent_ids(repository, "default@")
    side_parents = sorted(
        [source.resolve("sideA").commit_id, source.resolve("sideB").commit_id]
    )

    operation_count = len(operation_ids(repository))
    py_default = root / "py-default"
    source.add_workspace(py_default, name="py-default")
    assert_equal("workspace add operation count", len(operation_ids(repository)), operation_count + 2)
    assert_equal(
        "workspace add operation descriptions",
        operation_descriptions(repository, 2),
        [
            "create initial working-copy commit in workspace py-default",
            "add workspace 'py-default'",
        ],
    )

    cli_default = root / "cli-default"
    run_jj(repository, "workspace", "add", "--name", "cli-default", str(cli_default))
    py_default_commit = Workspace.load(py_default).working_copy()
    cli_default_commit = Workspace.load(cli_default).working_copy()
    assert_equal("default Pyjutsu parents", py_default_commit.parent_ids, expected_default_parents)
    assert_equal("default CLI parents", cli_default_commit.parent_ids, expected_default_parents)
    assert_equal("default merged tree", py_default_commit.tree_id, cli_default_commit.tree_id)
    assert_equal("default conflict state", py_default_commit.has_conflict, True)
    assert_equal("default sparse copy files", sorted(path.name for path in py_default.iterdir()), [".jj", "common.txt"])

    baseline = source.resolve("base")
    py_explicit = root / "py-explicit"
    source.add_workspace(
        py_explicit,
        name="py-explicit",
        revisions=baseline.change_id,
        sparse_patterns="full",
    )
    cli_explicit = root / "cli-explicit"
    run_jj(
        repository,
        "workspace",
        "add",
        "--name",
        "cli-explicit",
        "-r",
        baseline.change_id,
        "--sparse-patterns",
        "full",
        str(cli_explicit),
    )
    py_explicit_commit = Workspace.load(py_explicit).working_copy()
    cli_explicit_commit = Workspace.load(cli_explicit).working_copy()
    assert_equal("stable change ID parent", py_explicit_commit.parent_ids, [baseline.commit_id])
    assert_equal("explicit CLI parent", cli_explicit_commit.parent_ids, [baseline.commit_id])
    assert_equal("explicit working-copy tree", py_explicit_commit.tree_id, cli_explicit_commit.tree_id)
    assert_equal("explicit commit is empty", py_explicit_commit.is_empty, True)
    assert_true("explicit checkout common file", (py_explicit / "common.txt").is_file())
    assert_true("explicit checkout baseline file", (py_explicit / "conflict.txt").is_file())

    py_typed = root / "py-typed"
    typed_info = source.add_workspace(
        py_typed,
        name="py-typed",
        revisions=Revset("base"),
        sparse_patterns="full",
    )
    assert_equal(
        "typed Revset parent",
        Workspace.load(typed_info.path).working_copy().parent_ids,
        [baseline.commit_id],
    )

    py_merge = root / "py-merge"
    source.add_workspace(
        py_merge,
        name="py-merge",
        revisions=["sideA", Revset("sideB")],
        sparse_patterns="full",
    )
    cli_merge = root / "cli-merge"
    run_jj(
        repository,
        "workspace",
        "add",
        "--name",
        "cli-merge",
        "-r",
        "sideA",
        "-r",
        "sideB",
        "--sparse-patterns",
        "full",
        str(cli_merge),
    )
    py_merge_commit = Workspace.load(py_merge).working_copy()
    cli_merge_commit = Workspace.load(cli_merge).working_copy()
    assert_equal("multi-parent topology", sorted(py_merge_commit.parent_ids), side_parents)
    assert_equal("multi-parent CLI topology", sorted(cli_merge_commit.parent_ids), side_parents)
    assert_equal("multi-parent merged tree", py_merge_commit.tree_id, cli_merge_commit.tree_id)
    assert_equal("multi-parent conflict state", py_merge_commit.has_conflict, True)
    assert_equal(
        "multi-parent conflicts",
        [(item.path, item.num_sides, item.num_bases) for item in Workspace.load(py_merge).conflicts("@")],
        [("conflict.txt", 2, 1)],
    )
    assert_true("multi-parent A-only checkout", (py_merge / "a-only.txt").is_file())
    assert_true("multi-parent B-only checkout", (py_merge / "b-only.txt").is_file())
    assert_true("multi-parent conflict checkout", (py_merge / "conflict.txt").is_file())

    py_empty = root / "py-empty"
    source.add_workspace(
        py_empty,
        name="py-empty",
        revisions="base",
        sparse_patterns="empty",
    )
    assert_equal("empty sparse checkout", sorted(path.name for path in py_empty.iterdir()), [".jj"])

    for label, revision in (
        ("invalid", "this is not a revset ("),
        ("empty", "none()"),
        ("multiple", "all()"),
    ):
        destination = root / f"rejected-{label}"
        try:
            source.add_workspace(destination, name=f"rejected-{label}", revisions=revision)
        except RevsetError as error:
            report("expected_error", name=label, error_type=type(error).__name__, message=str(error))
        else:
            raise AssertionError(f"{label} revision did not raise RevsetError")
        assert_equal(f"{label} destination absent", destination.exists(), False)

    duplicate = root / "duplicate"
    try:
        source.add_workspace(duplicate, name="py-explicit", revisions="base")
    except PyjutsuError as error:
        report("expected_error", name="duplicate", error_type=type(error).__name__, message=str(error))
    else:
        raise AssertionError("duplicate workspace name was accepted")
    assert_equal("duplicate destination absent", duplicate.exists(), False)

    nonempty = root / "nonempty"
    nonempty.mkdir()
    marker = nonempty / "keep.txt"
    marker.write_text("keep\n")
    try:
        source.add_workspace(nonempty, name="nonempty", revisions="base")
    except PyjutsuError as error:
        report("expected_error", name="nonempty", error_type=type(error).__name__, message=str(error))
    else:
        raise AssertionError("non-empty destination was accepted")
    assert_equal("non-empty destination preserved", marker.read_text(), "keep\n")

    return {
        "py_explicit": py_explicit,
        "cli_explicit": cli_explicit,
    }


def verify_configuration(root: Path, repository: Path, paths: dict[str, Path]) -> None:
    """Verify repository, workspace, conditional, environment, and warning configuration."""
    expected_repo_identity = ("Live Repository Author", "repository@live.invalid")
    primary_identity = author_from_root(Workspace.load(repository), "live primary identity")
    secondary_identity = author_from_root(
        Workspace.load(paths["py_explicit"]), "live secondary identity"
    )
    run_jj(paths["cli_explicit"], "new", "root()")
    cli_identity = (
        jj_value(paths["cli_explicit"], "@", "author.name()"),
        jj_value(paths["cli_explicit"], "@", "author.email()"),
    )
    assert_equal("primary repository identity", primary_identity, expected_repo_identity)
    assert_equal("secondary repository identity", secondary_identity, expected_repo_identity)
    assert_equal("CLI repository identity", cli_identity, expected_repo_identity)

    run_jj(
        paths["py_explicit"],
        "config",
        "set",
        "--workspace",
        "user.name",
        "Live Workspace Author",
    )
    workspace_identity = author_from_root(
        Workspace.load(paths["py_explicit"]), "live workspace identity"
    )
    assert_equal(
        "workspace configuration override",
        workspace_identity,
        ("Live Workspace Author", "repository@live.invalid"),
    )
    primary_after_workspace_override = author_from_root(
        Workspace.load(repository), "live primary after workspace override"
    )
    assert_equal(
        "workspace configuration remains distinct",
        primary_after_workspace_override,
        expected_repo_identity,
    )

    conditional = root / "conditional"
    Workspace.load(repository).add_workspace(
        conditional,
        name="conditional",
        revisions="base",
        sparse_patterns="full",
    )
    repo_config = Path(run_jj(repository, "config", "path", "--repo").strip())
    repo_identity = (repository / ".jj" / "repo").resolve()
    with repo_config.open("a") as config_file:
        config_file.write(
            f"""

[[--scope]]
--when.repositories = [{json.dumps(str(repo_identity))}]
[--scope.user]
name = "Conditional Repository Author"

[[--scope]]
--when.workspaces = [{json.dumps(str(conditional.resolve()))}]
[--scope.user]
email = "conditional@live.invalid"
"""
        )

    conditional_primary = author_from_root(
        Workspace.load(repository), "live conditional primary"
    )
    conditional_secondary = author_from_root(
        Workspace.load(conditional), "live conditional secondary"
    )
    run_jj(conditional, "new", "root()")
    conditional_cli = (
        jj_value(conditional, "@", "author.name()"),
        jj_value(conditional, "@", "author.email()"),
    )
    assert_equal(
        "repository path condition",
        conditional_primary,
        ("Conditional Repository Author", "repository@live.invalid"),
    )
    expected_conditional = ("Conditional Repository Author", "conditional@live.invalid")
    assert_equal("workspace path condition", conditional_secondary, expected_conditional)
    assert_equal("CLI conditional identity", conditional_cli, expected_conditional)

    os.environ["JJ_USER"] = "Environment Author"
    os.environ["JJ_EMAIL"] = "environment@live.invalid"
    environment_identity = author_from_root(
        Workspace.load(conditional), "live environment override"
    )
    assert_equal(
        "environment override precedence",
        environment_identity,
        ("Environment Author", "environment@live.invalid"),
    )
    os.environ.pop("JJ_USER")
    os.environ.pop("JJ_EMAIL")


def verify_no_create_load(root: Path) -> None:
    """Verify that a normal load does not create secure configuration state."""
    previous_config_home = os.environ["XDG_CONFIG_HOME"]
    clean_config_home = root / "clean-config"
    clean_config_home.mkdir()
    os.environ["XDG_CONFIG_HOME"] = str(clean_config_home)
    repository = root / "clean-repository"
    repository.mkdir()
    run_jj(repository, "git", "init", "--colocate")
    Workspace.load(repository)
    assert_equal("normal load repo config ID absent", (repository / ".jj" / "repo" / "config-id").exists(), False)
    assert_equal("normal load workspace config ID absent", (repository / ".jj" / "workspace-config-id").exists(), False)
    assert_equal("normal load secure repo directory absent", (clean_config_home / "jj" / "repos").exists(), False)
    assert_equal("normal load secure workspace directory absent", (clean_config_home / "jj" / "workspaces").exists(), False)
    os.environ["XDG_CONFIG_HOME"] = previous_config_home


def verify_migration_warning(root: Path) -> None:
    """Verify that Python receives secure configuration migration warnings."""
    previous_config_home = os.environ["XDG_CONFIG_HOME"]
    migration_config_home = root / "migration-config"
    migration_config_home.mkdir()
    os.environ["XDG_CONFIG_HOME"] = str(migration_config_home)
    repository = root / "migration-repository"
    repository.mkdir()
    run_jj(repository, "git", "init", "--colocate")
    (repository / ".jj" / "repo" / "config.toml").write_text(
        '[user]\nname = "Migrated Author"\nemail = "migrated@live.invalid"\n'
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        workspace = Workspace.load(repository)
    assert_true("secure migration warning emitted", any("migrated" in str(item.message) for item in caught))
    assert_equal(
        "migrated configuration authors commits",
        author_from_root(workspace, "live migrated identity"),
        ("Migrated Author", "migrated@live.invalid"),
    )
    os.environ["XDG_CONFIG_HOME"] = previous_config_home


def main() -> int:
    """Run all live acceptance checks."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    arguments = parser.parse_args()
    root = arguments.run_root.resolve()
    if root.exists():
        raise SystemExit(f"run root already exists: {root}")
    root.mkdir(parents=True)
    configure_isolated_environment(root)
    report(
        "environment",
        run_root=str(root),
        jj=subprocess.run(["jj", "--version"], capture_output=True, text=True, check=True).stdout.strip(),
        python=os.sys.version,
    )

    repository = make_conflict_repository(root)
    paths = verify_workspace_creation(root, repository)
    verify_configuration(root, repository, paths)
    verify_no_create_load(root)
    verify_migration_warning(root)
    report("result", status="passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
