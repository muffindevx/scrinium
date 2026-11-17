"""How long the Home health and the review inbox take with many rules (phase 4.4).

Creates a throwaway database with N rules (a third verified, some flagged) and times
`health` (target: under 200 ms with 5 000 rules) and `inbox`, cold and warm.

    uv run python benchmarks/inbox_speed.py --rules 5000
"""

import argparse
import json
import tempfile
import time
from pathlib import Path

from scrinium.application.knowledge.content import RuleContent
from scrinium.application.knowledge.entities import create_business_rule, set_verification
from scrinium.application.knowledge.inbox import health, inbox
from scrinium.application.workspaces import ensure_local_workspace
from scrinium.domain.enums import RuleCategory, VerificationStatus
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import Project


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rules", type=int, default=5000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="scrinium-inbox-") as tmp:
        database = Database.open(Path(tmp))
        with database.session() as session:
            workspace = ensure_local_workspace(session)
            project = Project(workspace_id=workspace.id, name="Bench", project_key="bench")
            session.add(project)
            session.flush()
            for number in range(args.rules):
                version = create_business_rule(
                    session, project, canonical_key=f"bench/{number}", category=RuleCategory.OTHER,
                    content=RuleContent(
                        title=f"Regla {number}",
                        statement=f"Los pedidos del tipo {number % 97} tienen un límite propio.",
                    ),
                )  # fmt: skip
                if number % 3 == 0:
                    set_verification(
                        session, project, version.knowledge_entity_version_id,
                        VerificationStatus.HUMAN_VERIFIED, reviewer="bench",
                    )  # fmt: skip
            project_id = project.id
        timings = {}
        # The second inbox call is the usual one: statement sketches are cached per version.
        for name, call in (("health", health), ("inbox", inbox), ("inbox_warm", inbox)):
            with database.session() as session:
                project = session.get_one(Project, project_id)
                started = time.perf_counter()
                call(session, project)
                timings[f"{name}_ms"] = round((time.perf_counter() - started) * 1000, 1)
        database.dispose()
    print(json.dumps({"rules": args.rules, **timings}, indent=2))


if __name__ == "__main__":
    main()
