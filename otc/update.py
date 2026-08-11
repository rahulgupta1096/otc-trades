"""Daily update: download recent files, ingest new ones, merge into the DB.

Run while the app is stopped (the DB needs the write lock):
    python -m otc.update
"""
from __future__ import annotations

from datetime import date, timedelta

from . import build, download, ingest


def main() -> None:
    # Re-check the last 10 days: late files appear and recent days can be
    # re-published; anything already valid on disk is skipped.
    download.main(date.today() - timedelta(days=10), date.today())
    ingest.main()
    build.main()


if __name__ == "__main__":
    main()
