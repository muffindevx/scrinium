"""The `scrinium` command. `check`, `hooks` and `mirrors` run in CI and in a Git hook: they start
without importing the server, its models or its database (cli/rules.py)."""

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    from scrinium.core.language import use_english

    # The command line speaks English; the console, in its own process, stays in Spanish.
    use_english()
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"check", "hooks", "mirrors"}:
        from scrinium.cli.rules import main as light

        return light(args)
    from scrinium.cli.main import main as full

    return full(args)
