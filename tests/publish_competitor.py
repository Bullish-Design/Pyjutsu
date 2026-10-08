"""A competing jj CLI writer for the publication race tests.

    publish_competitor.py REPO FIFO KIND

The process starts, then blocks on FIFO until the test writes one byte. That releases it at a
chosen moment relative to the publisher. KIND is ``newcommit`` (``jj new``, a file write, then
``jj commit``) or ``new1`` (``jj new`` only).
"""

import os
import subprocess
import sys

repo, fifo, kind = sys.argv[1:4]
with open(fifo, "rb") as barrier:
    barrier.read(1)


def jj(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["jj", *args], cwd=repo, capture_output=True, text=True)


if kind == "newcommit":
    first = jj("new", "-m", "competitor")
    with open(os.path.join(repo, "comp.txt"), "w") as handle:
        handle.write("competitor bytes\n")
    second = jj("commit", "-m", "competitor work")
    codes = (first.returncode, second.returncode)
else:
    codes = (jj("new", "-m", "competitor").returncode, 0)
print(codes)
