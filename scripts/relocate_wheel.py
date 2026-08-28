"""Make a nix-built wheel portable, then re-pack it.

maturin applies the `manylinux_2_39_x86_64` platform tag on request, but it does not clean
the extension module. A wheel built inside a nix devenv carries a RUNPATH that points into
`/nix/store`. Those directories do not exist on a normal Linux host. The loader then skips
them and falls back to the default search path, so the wheel usually still imports — but the
manylinux tag is a promise the artifact does not keep, and the failure mode is silent.

This script removes the RUNPATH from every extension module in the wheel and writes the
`RECORD` hashes again. Run it on the wheel that `maturin build` produced, before publishing.

Usage: python scripts/relocate_wheel.py dist/pyjutsu-<version>-cp313-abi3-manylinux_*.whl
"""

from __future__ import annotations

import base64
import csv
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def _record_hash(path: Path) -> tuple[str, int]:
    """The `RECORD` entry for `path`: urlsafe-b64 sha256 without padding, and the size."""
    data = path.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"sha256={digest}", len(data)


def relocate(wheel: Path) -> list[Path]:
    """Strip the RUNPATH from every `.so` in `wheel`, in place. Returns the files changed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(root)

        shared_objects = sorted(root.rglob("*.so"))
        if not shared_objects:
            raise SystemExit(f"{wheel.name}: no extension module found — wrong wheel?")
        for so in shared_objects:
            # patchelf needs the file writable; the zip preserves read-only bits.
            so.chmod(0o755)
            subprocess.run(["patchelf", "--remove-rpath", str(so)], check=True)

        record = next(root.glob("*.dist-info/RECORD"))
        rows = list(csv.reader(record.read_text().splitlines()))
        changed = {str(so.relative_to(root)) for so in shared_objects}
        for row in rows:
            if row and row[0] in changed:
                row[1], size = _record_hash(root / row[0])
                row[2] = str(size)
        with record.open("w", newline="") as fh:
            csv.writer(fh).writerows(rows)

        # Re-pack. RECORD must not list its own hash, and zipfile.write preserves the
        # extracted mode bits, so the executable flag set above survives into the wheel.
        rebuilt = root.parent / wheel.name
        with zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(root.rglob("*")):
                if item.is_file():
                    zf.write(item, item.relative_to(root))
        shutil.move(rebuilt, wheel)
    return shared_objects


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    wheel = Path(sys.argv[1])
    if not wheel.is_file():
        raise SystemExit(f"no such wheel: {wheel}")
    changed = relocate(wheel)
    print(f"relocated {wheel.name}: removed RUNPATH from {len(changed)} extension module(s)")


if __name__ == "__main__":
    main()
