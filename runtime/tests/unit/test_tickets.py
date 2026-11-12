"""IMP-2: ticket keys named by commit messages."""

from scrinium.application.knowledge.tickets import tickets_in


def test_jira_keys_and_issue_numbers_are_found_once_each() -> None:
    message = "PAY-142: comision del 2 %\n\nCierra #37 y PAY-142 (ver tambien OPS-9)."
    assert tickets_in(message) == ["PAY-142", "OPS-9", "#37"]


def test_what_is_not_a_ticket_is_ignored() -> None:
    assert tickets_in("Merge pull request #12 from ana/rama") == []
    assert tickets_in("UTF-8, ISO-8859-1, SHA-256, RFC-7231, x-1, A-0, color #fff, &#38;") == []
    assert tickets_in("sube a v2 y quita UTF8") == []
