"""Ingestion policy and folder reading, without a database."""

from pathlib import Path

import pytest

from scrinium.application.ingestion.policy import (
    MAX_DEPTH,
    MAX_USER_PATTERNS,
    ExclusionReason,
    SourceKind,
    check_content,
    check_path,
    find_secret,
    validate_user_patterns,
)
from scrinium.infrastructure.local_files.folder_reader import FolderChangedError, read_file

GIT = SourceKind.GIT_REPOSITORY


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("src/app.py", None),
        ("Dockerfile", None),
        (".env.example", None),
        (".env.sample", None),
        (".env", ExclusionReason.SENSITIVE_FILE),
        (".ENV.local", ExclusionReason.SENSITIVE_FILE),
        ("config/id_ed25519.pub", ExclusionReason.SENSITIVE_FILE),
        ("a/.aws/config.json", ExclusionReason.SENSITIVE_FILE),
        ("infra/prod.tfvars", ExclusionReason.SENSITIVE_FILE),
        ("backup/app.sqlite3", ExclusionReason.SENSITIVE_FILE),
        ("web/node_modules/x/index.js", ExclusionReason.DEFAULT_PATH),
        ("uv.lock", ExclusionReason.DEFAULT_PATH),
        ("static/app.min.js", ExclusionReason.DEFAULT_PATH),
        ("archive.zip", ExclusionReason.UNSUPPORTED_TYPE),
        ("binary", ExclusionReason.UNSUPPORTED_TYPE),
        ("/".join(["d"] * (MAX_DEPTH + 1)) + "/x.py", ExclusionReason.TOO_DEEP),
        ("x" * 1001 + ".py", ExclusionReason.BAD_PATH),
    ],
)
def test_check_path(path: str, reason: ExclusionReason | None) -> None:
    exclusion = check_path(path, GIT, [])
    assert (exclusion.reason if exclusion else None) == reason


def test_folders_only_accept_documents() -> None:
    assert check_path("notas.md", SourceKind.FOLDER, []) is None
    exclusion = check_path("script.py", SourceKind.FOLDER, [])
    assert exclusion is not None and exclusion.reason is ExclusionReason.UNSUPPORTED_TYPE


@pytest.mark.parametrize(
    ("pattern", "path", "excluded"),
    [
        ("docs/", "docs/a.md", True),
        ("docs/", "web/docs/a.md", True),
        ("docs/", "mydocs/a.md", False),
        ("*.md", "web/a.md", True),
        ("src/*.py", "src/a.py", True),
        ("/src/a.py", "src/a.py", True),
    ],
)
def test_user_patterns(pattern: str, path: str, excluded: bool) -> None:
    patterns = validate_user_patterns([pattern])
    exclusion = check_path(path, GIT, patterns)
    assert (exclusion is not None and exclusion.reason is ExclusionReason.USER_PATTERN) is excluded


def test_user_patterns_cannot_reinclude_defaults() -> None:
    # There are only exclusion patterns: a "!" re-includes nothing.
    exclusion = check_path(".env", GIT, validate_user_patterns(["!.env"]))
    assert exclusion is not None and exclusion.reason is ExclusionReason.SENSITIVE_FILE


@pytest.mark.parametrize(
    "patterns", [[""], ["x" * 201], ["a\x00b"], ["x"] * (MAX_USER_PATTERNS + 1)]
)
def test_invalid_user_patterns(patterns: list[str]) -> None:
    with pytest.raises(ValueError):
        validate_user_patterns(patterns)


@pytest.mark.parametrize(
    "text",
    [
        'password = "changeme"',
        'password = "${DB_PASSWORD}"',
        "api_key: '<your-key>'",
        "secret = os.environ['SECRET']",
        "token = request.args.get('token')",
        'password = "short"',
        "postgres://user@localhost/db",
        'export AWS_ACCESS_KEY_ID="$aws_access_key_id"',
        'DB_URL="mysql://$USR:$DATABASE_PWD@localhost/db"',
        'set API_KEY="%API_KEY_VALUE%"',
    ],
)
def test_common_code_is_not_a_secret(text: str) -> None:
    assert find_secret(text) is None


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ('DB_PASSWORD = "Xk29!qPz77aa"', "assigned_secret"),
        ('"client_secret": "a8f9d7e6c5b4a3f2"', "assigned_secret"),  # gitleaks:allow (falso)
        ("redis://default:s3cr3tvalue@cache:6379", "url_credentials"),
        ("ghp_" + "A" * 36, "github_token"),
        ("AKIA" + "B" * 16, "aws_access_key"),
    ],
)
def test_secrets_are_found_with_rule_and_line(text: str, rule: str) -> None:
    found = find_secret("linea 1\n" + text)
    assert found is not None
    assert (found.rule, found.line) == (rule, 2)


def test_content_checks() -> None:
    assert check_content(b"hola\n") == ("hola\n", None)
    assert check_content("﻿con BOM".encode()) == ("con BOM", None)
    for data, reason in (
        (b"a\x00b", ExclusionReason.BINARY),
        ("ñ".encode("latin-1"), ExclusionReason.NOT_UTF8),
        (b"x" * (1024 * 1024 + 1), ExclusionReason.TOO_LARGE),
    ):
        text, exclusion = check_content(data)
        assert text is None and exclusion is not None and exclusion.reason is reason


def test_read_file_refuses_a_folder_swapped_for_a_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.md").write_text("dentro")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_text("fuera")
    assert read_file(root, "sub/a.md", 100) == b"dentro"

    # Someone swaps the folder for a link between the walk and the read.
    (root / "sub" / "a.md").unlink()
    (root / "sub").rmdir()
    (root / "sub").symlink_to(outside)
    with pytest.raises(FolderChangedError):
        read_file(root, "sub/a.md", 100)


@pytest.mark.parametrize("relative", ["../x.md", "a/../../x.md", "", "/etc/passwd"])
def test_read_file_rejects_escaping_paths(tmp_path: Path, relative: str) -> None:
    with pytest.raises(FolderChangedError):
        read_file(tmp_path, relative, 100)


def test_read_file_reads_at_most_the_limit_plus_one(tmp_path: Path) -> None:
    (tmp_path / "big.md").write_bytes(b"x" * 1000)
    assert len(read_file(tmp_path, "big.md", 10)) == 11
