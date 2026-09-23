"""`python -m harness`, which is what `scripts/harness.sh` invokes.

A module entry point rather than a console script, for the reason HARNESS.md
section 10 gives about the `Makefile` and `scripts/test.sh` split: a capability
that exists only behind an installed entry point is one that stops working on
whichever machine has not run the install step, and this repository has already
paid for that with `make`.
"""
from __future__ import annotations

import sys

from harness.cli import main

if __name__ == "__main__":
    sys.exit(main())
