"""The testers' zip, byte for byte the same from the same commit (phase 5.3).

`zip` records each file's modification time and the order the file system lists them in, so two
builds of the same commit differ. Here the entries are sorted, every time is
`SOURCE_DATE_EPOCH` (the commit's date), permissions are fixed and there are no extra fields.
Next to the zip, `SHA256SUMS` lets anyone check what they downloaded.

Usage: python devtools/release_zip.py OUT.zip FILE... (entries go under `scrinium/`)
"""

import hashlib
import os
import sys
import time
import zipfile
from pathlib import Path

_FOLDER = "scrinium"
_EXECUTABLE = {".sh", ".ps1"}


def build(out: Path, files: list[Path], epoch: int) -> None:
    # Zip times start in 1980 and have a two-second resolution.
    stamp = time.gmtime(max(epoch, 315532800))[:6]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files, key=lambda p: p.name):
            entry = zipfile.ZipInfo(f"{_FOLDER}/{path.name}", date_time=stamp)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3  # Unix: the permissions below mean the same everywhere
            mode = 0o755 if path.suffix in _EXECUTABLE else 0o644
            entry.external_attr = (0o100000 | mode) << 16
            archive.writestr(entry, path.read_bytes())


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    out, files = Path(argv[0]), [Path(name) for name in argv[1:]]
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    build(out, files, epoch)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    sums = out.with_name("SHA256SUMS")
    sums.write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    print(f"{out}\n{digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
