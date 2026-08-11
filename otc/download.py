"""Download daily CFTC cumulative equities zips from DTCC's public portal.

The portal is flaky: it sometimes returns an HTML error page with HTTP 200,
or a transient 404 for a file that exists. Every download is therefore
validated (zip magic bytes) and retried with backoff. Dates outside the
portal's rolling retention window 404 consistently and are recorded as missing.

Usage:
    python -m otc.download                # START_DATE .. today
    python -m otc.download 2026-01-01     # explicit start
    python -m otc.download 2026-01-01 2026-01-31
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import requests

from .config import RAW_DIR, START_DATE, URL_TEMPLATE

MAX_ATTEMPTS = 5
CONCURRENCY = 4


def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def target_path(d: date):
    return RAW_DIR / f"CFTC_CUMULATIVE_EQUITIES_{d.year}_{d.month:02d}_{d.day:02d}.zip"


def is_valid_zip(path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"PK"
    except OSError:
        return False


def fetch_day(d: date, session: requests.Session) -> str:
    """Returns 'ok', 'cached', or 'missing'."""
    path = target_path(d)
    if path.exists() and is_valid_zip(path):
        return "cached"
    url = URL_TEMPLATE.format(y=d.year, m=d.month, d=d.day)
    consecutive_404 = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = session.get(url, timeout=180)
            if r.status_code == 200 and r.content[:2] == b"PK":
                tmp = path.with_suffix(".part")
                tmp.write_bytes(r.content)
                tmp.replace(path)
                return "ok"
            if r.status_code == 404 or r.content[:2] != b"PK":
                # Transient 404s happen for files that exist; only trust a 404
                # after it repeats.
                consecutive_404 += 1
                if consecutive_404 >= 3:
                    return "missing"
        except requests.RequestException:
            pass
        time.sleep(min(2 ** attempt, 30))
    return "missing"


def main(start: date, end: date) -> None:
    days = list(date_range(start, end))
    counts = {"ok": 0, "cached": 0, "missing": 0}
    missing: list[str] = []
    t0 = time.time()
    with requests.Session() as session, ThreadPoolExecutor(CONCURRENCY) as pool:
        futures = {pool.submit(fetch_day, d, session): d for d in days}
        done = 0
        for fut in as_completed(futures):
            d = futures[fut]
            status = fut.result()
            counts[status] += 1
            if status == "missing":
                missing.append(d.isoformat())
            done += 1
            if done % 25 == 0 or done == len(days):
                elapsed = time.time() - t0
                print(
                    f"[{done}/{len(days)}] ok={counts['ok']} cached={counts['cached']} "
                    f"missing={counts['missing']} elapsed={elapsed:.0f}s",
                    flush=True,
                )
    (RAW_DIR / "missing_dates.txt").write_text("\n".join(sorted(missing)))
    print(f"Done. Downloaded {counts['ok']}, cached {counts['cached']}, missing {counts['missing']}.")


if __name__ == "__main__":
    start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.fromisoformat(START_DATE)
    end = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    main(start, end)
