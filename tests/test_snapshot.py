"""Slice 5: `Workspace.snapshot` + auto-snapshot on tx open — differential vs the CLI.

A snapshot records the on-disk working copy as a rewrite of `@` and publishes a *separate*
`snapshot working copy` operation (concept §0.1) — exactly what the pinned `jj` CLI does
automatically before each command (forced here with a read command on the copy). Because a
snapshot preserves `@`'s change id and the committer timestamp is pinned, the snapshotted `@`
commit id is deterministic across two byte-identical copies given the same on-disk edit — that
commit-id equality is the tree-parity assertion (see [[m2-differential-mutation-testing]]).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyjutsu
import pytest

from tests.diff.jj_cli import JjCli


def _copy_repo(src: Path, dst: Path) -> Path:
    """A byte-identical sibling repo (same change ids, commit ids, and op log)."""
    shutil.copytree(src, dst)
    return dst


def test_snapshot_dirty_creates_op(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    change_before = jj.change_id(scratch_repo, "@")  # identical in both copies
    ops_before = len(jj.op_log_ids(scratch_repo))

    # The same on-disk edit on both sides → the same snapshotted tree → the same `@` commit id.
    (scratch_repo / "new.txt").write_text("edited\n")
    (other / "new.txt").write_text("edited\n")

    ws = pyjutsu.Workspace.load(scratch_repo)
    op = ws.snapshot()
    jj(other, "status")  # force the CLI's implicit snapshot

    assert op is not None
    assert op.is_snapshot is True
    assert op.description == "snapshot working copy"

    # Exactly one new operation on each side.
    assert len(ws.operations()) == ops_before + 1
    assert len(jj.op_log_ids(other)) == ops_before + 1

    # The change id is preserved; the snapshotted `@` matches the CLI's byte-for-byte.
    assert jj.change_id(scratch_repo, "@") == change_before
    assert jj.commit_id(scratch_repo, "@") == jj.commit_id(other, "@")


def test_snapshot_clean_returns_none(scratch_repo: Path) -> None:
    # `@` is in lockstep with disk (the fixture's last `describe` snapshotted it), so there is
    # nothing to record: no operation, and `snapshot()` returns `None`.
    ws = pyjutsu.Workspace.load(scratch_repo)
    ops_before = len(ws.operations())

    assert ws.snapshot() is None
    assert len(ws.operations()) == ops_before


def test_auto_snapshot_two_ops(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    ops_before = len(jj.op_log_ids(scratch_repo))

    (scratch_repo / "new.txt").write_text("edited\n")
    (other / "new.txt").write_text("edited\n")

    ws = pyjutsu.Workspace.load(scratch_repo)
    with ws.transaction("describe") as tx:
        tx.describe("@", "msg")
    jj(other, "describe", "-m", "msg")  # CLI auto-snapshots first

    # Two operations on each side: the snapshot, then the mutation on top of it.
    ops = ws.operations()
    assert len(ops) == ops_before + 2
    assert len(jj.op_log_ids(other)) == ops_before + 2

    # The head op is the mutation (not the snapshot); the op below it is the snapshot.
    assert ops[0].is_snapshot is False
    assert ops[1].is_snapshot is True
    assert ops[1].description == "snapshot working copy"

    # The description was applied on top of the snapshotted tree → identical `@` on both sides.
    assert jj.commit_id(scratch_repo, "@") == jj.commit_id(other, "@")


def test_clean_at_one_op(scratch_repo: Path) -> None:
    # A clean `@` + a mutation is exactly one operation: auto-snapshot is a no-op and does not
    # regress the slice-2/3 "one transaction == one op" invariant.
    ws = pyjutsu.Workspace.load(scratch_repo)
    ops_before = len(ws.operations())

    with ws.transaction("describe") as tx:
        tx.describe("@", "new message")

    assert len(ws.operations()) == ops_before + 1


def test_auto_snapshot_disabled(scratch_repo: Path) -> None:
    # `auto_snapshot=False` is honored literally: the mutation sees the un-snapshotted `@`, so the
    # on-disk edit is *not* captured (diverges from the CLI by design) and only one op is published.
    (scratch_repo / "new.txt").write_text("edited\n")

    ws = pyjutsu.Workspace.load(scratch_repo)
    ops_before = len(ws.operations())

    with ws.transaction("describe", auto_snapshot=False) as tx:
        described = tx.describe("@", "msg")

    # The new file was never snapshotted, so `@` stays empty (its tree == its parent's).
    assert described.is_empty is True
    assert len(ws.operations()) == ops_before + 1


def test_snapshot_respects_gitignore_matches_cli(
    scratch_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    # A repo-root `.gitignore` excludes `ignored.txt` from `@`'s snapshotted tree (jj's snapshotter
    # chains it itself); `tracked.txt` is captured. Binding tree id must equal the CLI's.
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    for d in (scratch_repo, other):
        (d / ".gitignore").write_text("ignored.txt\n")
        (d / "ignored.txt").write_text("secret\n")
        (d / "tracked.txt").write_text("ok\n")

    ws = pyjutsu.Workspace.load(scratch_repo)
    op = ws.snapshot()
    jj(other, "status")  # force the CLI's implicit snapshot

    assert op is not None
    files = set(jj(scratch_repo, "file", "list", "-r", "@").split())
    assert ".gitignore" in files
    assert "tracked.txt" in files
    assert "ignored.txt" not in files  # the gitignore kept it out of the tree
    # Tree parity: identical `@` commit id on both sides.
    assert jj.commit_id(scratch_repo, "@") == jj.commit_id(other, "@")


def test_snapshot_max_file_size_matches_cli(
    scratch_repo: Path, tmp_path: Path, jj: JjCli
) -> None:
    # With `snapshot.max-new-file-size = "10KiB"`, a 20 KiB new file is skipped exactly as the CLI
    # skips it (left untracked), so `@`'s tree — and commit id — match.
    jj.append_config('[snapshot]\nmax-new-file-size = "10KiB"')
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    for d in (scratch_repo, other):
        (d / "big.txt").write_bytes(b"x" * 20_000)  # > 10 KiB cap
        (d / "small.txt").write_text("ok\n")

    ws = pyjutsu.Workspace.load(scratch_repo)
    op = ws.snapshot()
    jj(other, "status")

    assert op is not None
    files = set(jj(scratch_repo, "file", "list", "-r", "@").split())
    assert "small.txt" in files
    assert "big.txt" not in files  # the configured cap kept it out of the tree
    assert jj.commit_id(scratch_repo, "@") == jj.commit_id(other, "@")


def test_snapshot_auto_track_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # `snapshot.auto-track` restricts which *new* files start being tracked. With it set to a glob
    # matching only `tracked.txt`, the sibling `other.txt` stays untracked — out of `@`'s tree — on
    # both sides, so the binding's `@` commit id matches the CLI's.
    jj.append_config('[snapshot]\nauto-track = \'glob:"tracked.txt"\'')
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    for d in (scratch_repo, other):
        (d / "tracked.txt").write_text("yes\n")
        (d / "other.txt").write_text("no\n")

    ws = pyjutsu.Workspace.load(scratch_repo)
    op = ws.snapshot()
    jj(other, "status")  # force the CLI's implicit snapshot

    assert op is not None
    files = set(jj(scratch_repo, "file", "list", "-r", "@").split())
    assert "tracked.txt" in files
    assert "other.txt" not in files  # auto-track kept the unmatched new file untracked
    assert jj.commit_id(scratch_repo, "@") == jj.commit_id(other, "@")


def test_snapshot_auto_track_none_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # `auto-track = "none()"` tracks no new file at all: a brand-new file leaves `@`'s tree unchanged,
    # so there's nothing to snapshot — `snapshot()` returns `None` and the CLI publishes no op either.
    jj.append_config('[snapshot]\nauto-track = "none()"')
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    ops_before = len(jj.op_log_ids(scratch_repo))
    for d in (scratch_repo, other):
        (d / "new.txt").write_text("ignored by auto-track\n")

    ws = pyjutsu.Workspace.load(scratch_repo)

    assert ws.snapshot() is None  # nothing newly tracked ⇒ tree unchanged ⇒ no op
    jj(other, "status")
    assert "new.txt" not in set(jj(scratch_repo, "file", "list", "-r", "@").split())
    assert len(jj.op_log_ids(other)) == ops_before  # CLI snapshots nothing either


def test_snapshot_auto_track_default_unchanged(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # With no `snapshot.auto-track` set, the default is `all()`: every new file is tracked, exactly
    # as before this slice. A new file enters `@` and the commit id matches the CLI's.
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    for d in (scratch_repo, other):
        (d / "new.txt").write_text("tracked by default\n")

    ws = pyjutsu.Workspace.load(scratch_repo)
    op = ws.snapshot()
    jj(other, "status")

    assert op is not None
    assert "new.txt" in set(jj(scratch_repo, "file", "list", "-r", "@").split())
    assert jj.commit_id(scratch_repo, "@") == jj.commit_id(other, "@")


def test_snapshot_bad_auto_track_raises(scratch_repo: Path, jj: JjCli) -> None:
    # A malformed `snapshot.auto-track` fileset is reported as a WorkingCopyError, not a panic.
    jj.append_config('[snapshot]\nauto-track = "no_such_fileset_function()"')
    (scratch_repo / "new.txt").write_text("x\n")

    ws = pyjutsu.Workspace.load(scratch_repo)
    with pytest.raises(pyjutsu.WorkingCopyError):
        ws.snapshot()


def test_snapshot_info_exclude_matches_cli(scratch_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # The repo-local global git-excludes file `.git/info/exclude` keeps `excluded.txt` out of `@`'s
    # tree, exactly as the CLI does; `kept.txt` is captured. copytree copies the exclude file into
    # the sibling, so both sides read it → identical `@` commit id.
    info_dir = scratch_repo / ".git" / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "exclude").write_text("excluded.txt\n")
    other = _copy_repo(scratch_repo, tmp_path / "copy")
    for d in (scratch_repo, other):
        (d / "excluded.txt").write_text("secret\n")
        (d / "kept.txt").write_text("ok\n")

    ws = pyjutsu.Workspace.load(scratch_repo)
    op = ws.snapshot()
    jj(other, "status")  # force the CLI's implicit snapshot

    assert op is not None
    files = set(jj(scratch_repo, "file", "list", "-r", "@").split())
    assert "kept.txt" in files
    assert "excluded.txt" not in files  # .git/info/exclude kept it out of the tree
    assert jj.commit_id(scratch_repo, "@") == jj.commit_id(other, "@")


def test_snapshot_modified_tracked_file(linear_repo: Path, tmp_path: Path, jj: JjCli) -> None:
    # Modifying an already-tracked file dirties `@`; the snapshot records it and matches the CLI.
    other = _copy_repo(linear_repo, tmp_path / "copy")
    ops_before = len(jj.op_log_ids(linear_repo))

    (linear_repo / "a.txt").write_text("rewritten contents\n")
    (other / "a.txt").write_text("rewritten contents\n")

    ws = pyjutsu.Workspace.load(linear_repo)
    op = ws.snapshot()
    jj(other, "status")

    assert op is not None
    assert op.is_snapshot is True
    assert len(jj.op_log_ids(linear_repo)) == ops_before + 1
    assert jj.commit_id(linear_repo, "@") == jj.commit_id(other, "@")
