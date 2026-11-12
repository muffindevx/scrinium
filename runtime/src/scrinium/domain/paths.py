"""What a file path says about its content, without reading it."""

from typing import Final

# Tests of every kind: they check rules, they do not implement or explain them.
_TEST_DIRS: Final = frozenset(
    {
        "tests",
        "test",
        "__tests__",
        "spec",
        "e2e",
        "smoke",
        "fixtures",
        "mocks",
        "__mocks__",
        "stories",
    }
)
_TEST_SUFFIXES: Final = ("_test.py", "_test.go", "Test.java", "Tests.cs", "_spec.rb")


def is_test_path(path: str) -> bool:
    """True for tests, fixtures, mocks and stories, in the usual layouts of each language."""
    name = path.rsplit("/", 1)[-1]
    return (
        any(part in _TEST_DIRS for part in path.split("/")[:-1])
        or ".stories." in name
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
        or name.endswith(_TEST_SUFFIXES)
    )
