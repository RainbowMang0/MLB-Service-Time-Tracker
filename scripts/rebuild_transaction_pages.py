#!/usr/bin/env python3
"""Rebuild the pages whose --check reads the transaction stores.

Two jobs write transactions, and they do not share a checkout:

  - the roster update writes data/cache/transactions
  - the raw-store job writes data/raw_transactions

The DFA snapshot and the out-of-options pages are built from both.
Whichever job commits second has to rebuild them from the tree it is
about to publish. Otherwise CI compares the stored snapshot with the
transactions on the branch and they disagree.

Debuts and rookie eligibility read the roster cache only, and the roster
job writes those pages in the same run that writes the cache. The changes
page check compares the HTML to the stored log, not back to the feeds.
Those stay with the roster job.

  python3 scripts/rebuild_transaction_pages.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Script paths relative to the repo root. A new --check that reads the
# transaction stores belongs here, and in the commit path list.
PAGES = (
    "scripts/build_dfa_outcomes_page.py",
    "scripts/out_of_options_pages.py",
)


def main(argv: list[str] | None = None) -> int:
    del argv  # one job, no flags
    failed = False
    for rel in PAGES:
        print(f"rebuilding {rel}", flush=True)
        result = subprocess.run([sys.executable, rel], cwd=ROOT)
        if result.returncode != 0:
            print(f"{rel} failed ({result.returncode})", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
