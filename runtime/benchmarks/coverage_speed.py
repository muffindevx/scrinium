"""How fast Coverage opens (phase 8.1): target under 300 ms with 10 000 files.

Seeds 10 000 code files as the palette benchmark does, gives each revision its stored
decisions (as after a sync), and times `coverage` at the root and one folder down.

    uv run python benchmarks/coverage_speed.py --files 10000
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import insert, select

sys.path.insert(0, str(Path(__file__).parent))
from palette_speed import seed

from scrinium.application.knowledge.coverage import coverage
from scrinium.infrastructure.decisions import DECISIONS_VERSION
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import RevisionDecisions, SourceRevision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--files", type=int, default=10_000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="scrinium-coverage-") as tmp:
        database = Database.open(Path(tmp))
        project_id = seed(database, args.files * 10, 2_000)
        with database.session() as session:
            revisions = session.execute(
                select(SourceRevision.id, SourceRevision.workspace_id)
            ).all()
            session.execute(
                insert(RevisionDecisions),
                [
                    {
                        "source_revision_id": rid,
                        "workspace_id": wid,
                        "extractor_version": DECISIONS_VERSION,
                        "lines": 100,
                        "spans": [[n * 10 + 1, n * 10 + 3, "guard"] for n in range(8)],
                    }
                    for rid, wid in revisions
                ],
            )
        timings = {}
        for name, path in (("root", ""), ("root_warm", ""), ("folder", "src")):
            with database.session() as session:
                started = time.perf_counter()
                coverage(session, project_id, path)
                timings[f"{name}_ms"] = round((time.perf_counter() - started) * 1000, 1)
        database.dispose()
    print(json.dumps({"files": args.files, **timings}, indent=2))


if __name__ == "__main__":
    main()
