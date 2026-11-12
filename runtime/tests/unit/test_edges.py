"""Phase B: where an import points inside the project."""

from scrinium.application.retrieval.edges import _module_file

FILES = {
    "src/features/users/services.ts", "src/features/users/repositories.ts",
    "src/lib/api.ts", "src/lib/index.ts",
    "app/billing/__init__.py", "app/billing/tax.py", "app/core.py",
}  # fmt: skip


def test_typescript_relative_paths_and_aliases() -> None:
    importer = "src/features/users/services.ts"
    assert _module_file("./repositories", importer, FILES) == "src/features/users/repositories.ts"
    assert _module_file("../../lib/api", importer, FILES) == "src/lib/api.ts"
    assert _module_file("@/lib", importer, FILES) == "src/lib/index.ts"
    assert _module_file("zod", importer, FILES) is None


def test_python_relative_and_absolute_imports() -> None:
    importer = "app/billing/tax.py"
    assert _module_file(".", importer, FILES) == "app/billing/__init__.py"
    assert _module_file("..core", importer, FILES) == "app/core.py"
    assert _module_file("app.billing.tax", "app/core.py", FILES) == "app/billing/tax.py"
    assert _module_file("requests", importer, FILES) is None
