"""What goes into the index and what does not. Anything not explicitly allowed is excluded.

The policy decides with the path and then with the content. It never runs or unpacks anything.
"""

import fnmatch
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final

from scrinium.core.secret_patterns import TOKEN_PATTERNS

MAX_FILE_BYTES: Final = 1024 * 1024
MAX_INCLUDED_FILES: Final = 20_000
MAX_INCLUDED_BYTES: Final = 100 * 1024 * 1024
# How many entries are examined at most, excluded ones included: bounds the walk.
MAX_SCANNED_ENTRIES: Final = 200_000
MAX_DEPTH: Final = 32
MAX_PATH_LENGTH: Final = 1000
MAX_USER_PATTERNS: Final = 100
MAX_USER_PATTERN_LENGTH: Final = 200


class SourceKind(StrEnum):
    GIT_REPOSITORY = "git_repository"
    FOLDER = "folder"


class ExclusionReason(StrEnum):
    DEFAULT_PATH = "default_path"
    SENSITIVE_FILE = "sensitive_file"
    USER_PATTERN = "user_pattern"
    UNSUPPORTED_TYPE = "unsupported_type"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    NOT_UTF8 = "not_utf8"
    POSSIBLE_SECRET = "possible_secret"  # noqa: S105  name of a reason, not a secret
    SYMLINK = "symlink"
    SUBMODULE = "submodule"
    SPECIAL_FILE = "special_file"
    HARD_LINK = "hard_link"
    TOO_DEEP = "too_deep"
    BAD_PATH = "bad_path"
    LIMIT_REACHED = "limit_reached"
    GENERATED = "generated"


@dataclass(frozen=True)
class Exclusion:
    reason: ExclusionReason
    # Which rule decided it: never the text found.
    rule: str | None = None
    line: int | None = None


_DEFAULT_DIRS: Final = frozenset(
    {
        ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor", ".venv", "venv",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox", "dist",
        "build", "target", ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache",
        "coverage", "htmlcov", ".gradle", ".idea", "Pods", ".terraform", ".cache",
        "site-packages",
    }
)  # fmt: skip
_DEFAULT_FILES: Final = frozenset(
    {
        "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "uv.lock",
        "poetry.lock", "Pipfile.lock", "Cargo.lock", "composer.lock", "Gemfile.lock", "go.sum",
    }
)  # fmt: skip
_DEFAULT_GLOBS: Final = ("*.min.js", "*.min.css", "*.map")

_SENSITIVE_DIRS: Final = frozenset(
    {".ssh", ".aws", ".gnupg", ".docker", ".kube", ".azure", ".gcloud"}
)
_SENSITIVE_FILES: Final = frozenset(
    {
        ".env", ".npmrc", ".pypirc", ".netrc", "_netrc", ".htpasswd", ".git-credentials",
        ".pgpass", "credentials", "credentials.json", "secrets.json", "secrets.yaml",
        "secrets.yml", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    }
)  # fmt: skip
_SENSITIVE_SUFFIXES: Final = frozenset(
    {
        ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".ppk", ".gpg",
        ".tfstate", ".tfvars", ".ovpn", ".dump", ".sqlite", ".sqlite3", ".db", ".bak",
    }
)  # fmt: skip
# `.env.example` and similar are templates, usually without real values.
_ENV_TEMPLATES: Final = frozenset({"example", "sample", "template", "dist"})

_DOCUMENT_SUFFIXES: Final = frozenset({".md", ".markdown", ".mdx", ".txt", ".rst", ".adoc"})
_CODE_SUFFIXES: Final = _DOCUMENT_SUFFIXES | frozenset(
    {
        ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".java",
        ".kt", ".kts", ".scala", ".groovy", ".go", ".rs", ".rb", ".php", ".cs", ".fs", ".vb",
        ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".m", ".mm", ".swift", ".dart", ".lua",
        ".r", ".jl", ".ex", ".exs", ".erl", ".hrl", ".clj", ".hs", ".ml", ".sql", ".sh",
        ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd", ".html", ".htm", ".css", ".scss",
        ".sass", ".less", ".vue", ".svelte", ".astro", ".xml", ".json", ".jsonc", ".yaml",
        ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties", ".gradle", ".graphql",
        ".gql", ".proto", ".tf", ".hcl", ".dockerfile", ".mk", ".cmake",
    }
)  # fmt: skip
_CODE_FILENAMES: Final = frozenset(
    {
        "Dockerfile", "Makefile", "Jenkinsfile", "Procfile", "Gemfile", "Rakefile", "Vagrantfile",
        "README", "LICENSE", "CHANGELOG", "CODEOWNERS", ".gitignore", ".gitattributes",
        ".editorconfig", ".dockerignore",
    }
)  # fmt: skip

# Generated code says nothing about the rules a person wrote, and it is rewritten wholesale on
# every regeneration. It is left out with its reason; a source can opt back in.
_GENERATED_DIRS: Final = frozenset({"__generated__"})
_GENERATED_GLOBS: Final = ("*.pb.go", "*_pb2.py", "*_pb2_grpc.py", "*.pb.cc", "*.pb.h")
# Only the top of the file counts: a marker further down is usually a comment about it.
GENERATED_HEADER_LINES: Final = 5
_GENERATED_HEADER_CHARS: Final = 2000
_GENERATED_MARKERS: Final = ("@generated", "DO NOT EDIT")
_OPENAPI_MARKERS: Final = ("openapi generator", "openapi-generator")


# A literal assigned to a credential name: `DB_PASSWORD = "s3cr3t-valor"`.
_ASSIGNED_SECRET: Final = re.compile(
    r"(?i)\b[\w.-]*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|"
    r"private[_-]?key)[\w.-]*[\"']?\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"
)
_URL_CREDENTIALS: Final = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:([^/\s:@]{3,})@")
_PLACEHOLDER_HINTS: Final = (
    "${", "{{", "<", "xxx", "***", "changeme", "change_me", "example", "placeholder",
    "dummy", "your_", "your-", "redacted", "process.env", "os.environ",
)  # fmt: skip


def _is_placeholder(value: str) -> bool:
    # `$VAR` or `%VAR%`: a reference to an environment variable, not the value.
    if value.startswith(("$", "%")):
        return True
    lowered = value.lower()
    return any(hint in lowered for hint in _PLACEHOLDER_HINTS)


def validate_user_patterns(patterns: list[str]) -> list[str]:
    if len(patterns) > MAX_USER_PATTERNS:
        raise ValueError("too many patterns")
    cleaned = []
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern or len(pattern) > MAX_USER_PATTERN_LENGTH or "\x00" in pattern:
            raise ValueError("invalid pattern")
        cleaned.append(pattern.lstrip("/"))
    return cleaned


def matches_user_pattern(path: str, pattern: str) -> bool:
    """`docs/` excludes that folder wherever it is; `*.log` matches the path and the name."""
    if pattern.endswith("/"):
        return path.startswith(pattern) or f"/{pattern}" in f"/{path}"
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(
        PurePosixPath(path).name, pattern
    )


def skip_directory(relative_dir: str) -> Exclusion | None:
    """Allows skipping whole folders when walking the disk."""
    name = PurePosixPath(relative_dir).name
    if name in _SENSITIVE_DIRS:
        return Exclusion(ExclusionReason.SENSITIVE_FILE, f"{name}/")
    if name in _DEFAULT_DIRS:
        return Exclusion(ExclusionReason.DEFAULT_PATH, f"{name}/")
    return None


def check_path(path: str, kind: SourceKind, user_patterns: list[str]) -> Exclusion | None:
    """Decides with the relative (POSIX) path, before reading anything."""
    parts = PurePosixPath(path).parts
    if len(path) > MAX_PATH_LENGTH:
        return Exclusion(ExclusionReason.BAD_PATH, "path_length")
    if len(parts) > MAX_DEPTH:
        return Exclusion(ExclusionReason.TOO_DEEP)
    for directory in parts[:-1]:
        excluded = skip_directory(directory)
        if excluded is not None:
            return excluded

    name = parts[-1]
    lowered = name.lower()
    suffix = PurePosixPath(lowered).suffix
    if lowered in _SENSITIVE_FILES or lowered.startswith(("id_rsa", "id_ed25519", "id_ecdsa")):
        return Exclusion(ExclusionReason.SENSITIVE_FILE, name)
    if lowered.startswith(".env.") and lowered.removeprefix(".env.") not in _ENV_TEMPLATES:
        return Exclusion(ExclusionReason.SENSITIVE_FILE, ".env.*")
    if suffix in _SENSITIVE_SUFFIXES:
        return Exclusion(ExclusionReason.SENSITIVE_FILE, f"*{suffix}")
    if name in _DEFAULT_FILES:
        return Exclusion(ExclusionReason.DEFAULT_PATH, name)
    for glob in _DEFAULT_GLOBS:
        if fnmatch.fnmatchcase(lowered, glob):
            return Exclusion(ExclusionReason.DEFAULT_PATH, glob)
    for pattern in user_patterns:
        if matches_user_pattern(path, pattern):
            return Exclusion(ExclusionReason.USER_PATTERN, pattern)

    if kind is SourceKind.FOLDER:
        allowed = suffix in _DOCUMENT_SUFFIXES
    else:
        # The only `.env.*` that get this far are templates.
        allowed = suffix in _CODE_SUFFIXES or name in _CODE_FILENAMES or lowered.startswith(".env.")
    if not allowed:
        return Exclusion(ExclusionReason.UNSUPPORTED_TYPE, suffix or name)
    return None


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def find_secret(text: str) -> Exclusion | None:
    for rule, pattern in TOKEN_PATTERNS.items():
        match = pattern.search(text)
        if match:
            return Exclusion(ExclusionReason.POSSIBLE_SECRET, rule, _line_of(text, match.start()))
    for rule, pattern in (
        ("assigned_secret", _ASSIGNED_SECRET),
        ("url_credentials", _URL_CREDENTIALS),
    ):
        for match in pattern.finditer(text):
            if not _is_placeholder(match.group(1)):
                return Exclusion(
                    ExclusionReason.POSSIBLE_SECRET, rule, _line_of(text, match.start())
                )
    return None


def check_content(data: bytes) -> tuple[str | None, Exclusion | None]:
    """Returns the text if the file can be indexed, or the exclusion that prevents it."""
    if len(data) > MAX_FILE_BYTES:
        return None, Exclusion(ExclusionReason.TOO_LARGE)
    if b"\x00" in data:
        return None, Exclusion(ExclusionReason.BINARY)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, Exclusion(ExclusionReason.NOT_UTF8)
    secret = find_secret(text)
    if secret is not None:
        return None, secret
    return text, None


def generated_by_path(path: str) -> Exclusion | None:
    """Known output folders and file names of code generators."""
    parts = PurePosixPath(path).parts
    for directory in parts[:-1]:
        if directory in _GENERATED_DIRS:
            return Exclusion(ExclusionReason.GENERATED, f"{directory}/")
    name = parts[-1].lower()
    for glob in _GENERATED_GLOBS:
        if fnmatch.fnmatchcase(name, glob):
            return Exclusion(ExclusionReason.GENERATED, glob)
    return None


def generated_by_header(text: str) -> Exclusion | None:
    """The markers generators write at the top: `@generated`, `DO NOT EDIT`, OpenAPI Generator."""
    head = text[:_GENERATED_HEADER_CHARS].split("\n")[:GENERATED_HEADER_LINES]
    for number, line in enumerate(head, start=1):
        for marker in _GENERATED_MARKERS:
            if marker in line:
                return Exclusion(ExclusionReason.GENERATED, marker, number)
        lowered = line.lower()
        if "generated" in lowered and any(marker in lowered for marker in _OPENAPI_MARKERS):
            return Exclusion(ExclusionReason.GENERATED, "openapi-generator", number)
    return None
