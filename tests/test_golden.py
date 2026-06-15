"""Slice 8: golden shape guard + the reads-never-mutate (op-count) invariant.

Differential tests guard *values*; this golden guards model *shape* — the set of fields each
model exposes. A drift in the native layer or an unintended model edit changes the field set
and trips this test, forcing a deliberate golden update (regenerate against the pinned jj).
"""

from __future__ import annotations

import json
from pathlib import Path

import pyjutsu
from pyjutsu import models

from tests.diff.jj_cli import JjCli

GOLDEN = Path(__file__).parent / "golden" / "model_fields.json"


def test_model_fields_match_golden() -> None:
    expected = json.loads(GOLDEN.read_text())
    actual = {name: sorted(getattr(models, name).model_fields) for name in expected}
    assert actual == expected, (
        "model field shape drifted; if intentional, regenerate tests/golden/model_fields.json"
    )


def test_reads_do_not_mutate_the_op_log(linear_repo: Path, jj: JjCli) -> None:
    # The M1 contract: reads operate at the chosen operation without snapshotting. Running every
    # read must leave the op log untouched (same length, same head).
    ws = pyjutsu.Workspace.load(linear_repo)
    before = jj.op_log_ids(linear_repo)

    ws.working_copy()
    ws.resolve("@")
    ws.log("::@")
    ws.operations()
    ws.bookmarks()
    ws.conflicts("@")
    ws.diff_stat("@-")
    ws.at_operation("@-").log("::@")

    after = jj.op_log_ids(linear_repo)
    assert after == before
    assert ws.head_operation() == before[0]
