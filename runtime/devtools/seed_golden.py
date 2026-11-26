"""Seeds a reference set's rules as verified rules of a demo project (never the real data).

For each rule of the golden file: a rule written by a person, cited at its lines in the
project's current revision of that file, and verified by «Usuario de pruebas». Rules already
seeded (`golden/<id>`) are left alone. Run it after the project's first sync:

    SCRINIUM_DEMO_DIR=<folder> uv run --project runtime python \\
        runtime/devtools/seed_golden.py --project <name> --golden <reference.json>

The reference lists are kept outside the repository: the analyzed projects' rules never go in it.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from platformdirs import user_data_dir
from sqlalchemy import select

from scrinium.application.knowledge.content import RuleContent
from scrinium.application.knowledge.entities import create_business_rule, set_verification
from scrinium.application.knowledge.evidence import cite_source
from scrinium.domain.enums import (
    EntityType,
    EvidenceRole,
    EvidenceType,
    ItemLifecycle,
    RuleCategory,
    VerificationMethod,
    VerificationStatus,
)
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import (
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    SourceConnection,
    SourceItem,
    SourceRevision,
)

REVIEWER = "Usuario de pruebas"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--golden", type=Path, required=True)
    args = parser.parse_args()

    root = Path(os.environ.get("SCRINIUM_DEMO_DIR", Path.home() / ".cache" / "scrinium-demo"))
    data_dir = root.expanduser().absolute() / "data"
    if data_dir.resolve() == Path(user_data_dir("scrinium", appauthor=False)).resolve():
        print("No se siembra el directorio de datos real.", file=sys.stderr)
        return 1
    golden = json.loads(args.golden.read_text())
    database = Database.open(data_dir)
    with database.session() as session:
        project = session.scalars(select(Project).where(Project.name == args.project)).first()
        if project is None:
            print(f"No hay un proyecto {args.project}.", file=sys.stderr)
            return 1
        revisions = {
            str(revision.metadata_.get("path", "")): revision
            for revision in session.scalars(
                select(SourceRevision)
                .join(SourceItem, SourceItem.current_revision_id == SourceRevision.id)
                .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
                .where(
                    SourceConnection.project_id == project.id,
                    SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
                )
            )
        }
        seeded = skipped = 0
        for rule in golden["rules"]:
            key = f"golden/{rule['id'].lower()}"
            exists = session.scalars(
                select(KnowledgeEntity.id).where(
                    KnowledgeEntity.project_id == project.id,
                    KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
                    KnowledgeEntity.canonical_key == key,
                )
            ).first()
            revision = revisions.get(rule["path"])
            if exists is not None or revision is None:
                skipped += 1
                continue
            # The reference set is a draft: a range past the end of its file is cut there.
            length = len((revision.text_content or "").splitlines())
            end = min(rule["end"], length)
            if rule["start"] > end:
                print(f"{rule['id']}: líneas fuera del archivo; se omite.", file=sys.stderr)
                skipped += 1
                continue
            content = RuleContent(
                title=f"{rule['id']} · {rule['rule'][:120]}", statement=rule["rule"]
            )
            version = create_business_rule(
                session, project, canonical_key=key, category=RuleCategory.OTHER, content=content
            )
            version_id = version.knowledge_entity_version_id
            entity_id = session.get_one(KnowledgeEntityVersion, version_id).knowledge_entity_id
            cite_source(
                session, project, entity_id=entity_id, version_id=version_id,
                revision_id=revision.id, start_line=rule["start"], end_line=end,
                role=EvidenceRole.SUPPORTS, evidence_type=EvidenceType.CODE,
                method=VerificationMethod.DIRECT_REFERENCE,
            )  # fmt: skip
            set_verification(
                session, project, version_id, VerificationStatus.HUMAN_VERIFIED, reviewer=REVIEWER
            )
            seeded += 1
    print(f"{seeded} reglas de {golden['project']} sembradas y verificadas; {skipped} ya estaban.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
