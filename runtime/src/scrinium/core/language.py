"""The language of messages that both the console and the command line can show.

The console speaks Spanish and translates in the browser (ui/src/lib/i18n-en.ts); the command
line speaks English. Each runs in its own process, so the command line switches this once at
startup and the server never does. Text only the command line shows is written in English
directly; this is for the few messages both of them raise, such as a malformed YAML file.
"""

_english = False


def use_english() -> None:
    """Called by the `scrinium` command before it does anything else."""
    global _english
    _english = True


def pick(spanish: str, english: str) -> str:
    return english if _english else spanish
