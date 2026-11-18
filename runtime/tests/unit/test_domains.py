"""Domains proposed from folders (coverage by domain)."""

from scrinium.application.knowledge.domains import ROOT_DOMAIN, proposed_domain


def test_the_first_meaningful_folder_names_the_domain() -> None:
    assert proposed_domain("packages/db/drizzle/0001_rls.sql") == "db"
    assert proposed_domain("addons/sale/models/sale_order.py") == "sale"
    assert proposed_domain("src/main/java/com/acme/billing/Invoice.java") == "acme"
    assert proposed_domain("apps/api/src/routes/me.ts") == "api"
    assert proposed_domain("src/app.py") == "src"
    assert proposed_domain("README.md") == ROOT_DOMAIN
    assert proposed_domain(".github/workflows/ci.yml") == "workflows"
