"""`python -m scrinium`, as the Git hook calls it when `scrinium` is not on the PATH."""

import sys

from scrinium.cli.entry import main

sys.exit(main())
