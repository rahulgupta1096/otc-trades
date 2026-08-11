"""Central configuration for the OTC trades pipeline."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PARQUET_DIR = DATA_DIR / "parquet"
DB_PATH = DATA_DIR / "trades.duckdb"

# Earliest date to attempt. DTCC's public portal keeps a rolling ~2-year window;
# requests before the window 404 and are simply skipped.
START_DATE = "2024-08-01"

URL_TEMPLATE = (
    "https://pddata.dtcc.com/ppd/api/report/cumulative/cftc/"
    "CFTC_CUMULATIVE_EQUITIES_{y}_{m:02d}_{d:02d}.zip"
)

# Rows whose UPI Underlier Name matches any of these regexes are dropped at
# ingest: aggregate Taiwan buckets, Chinese A-shares, Thai NVDRs, and other
# clearly non-US local listings the app does not care about.
EXCLUDE_UNDERLIER_REGEXES = [
    r"^(TWSE|TPEX) LISTED STOCKS$",          # Taiwan aggregate buckets
    r"\\SHARES [AB]\\[0-9]{6}$",             # Chinese A/B-shares (Shanghai/Shenzhen codes)
    r"\\INDEX\\N[0-9]{5}$",                  # Chinese local index codes (CSI etc.)
    r"^NVDR Shares$",                        # Thai NVDR bucket
    r"\\(OPEN-END ETF|OTHER FUND|OPEN-END MIXED FUND)\\[0-9]{6}$",  # Chinese local ETFs
]

for _p in (RAW_DIR, PARQUET_DIR):
    _p.mkdir(parents=True, exist_ok=True)
