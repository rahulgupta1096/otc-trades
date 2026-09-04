"""Convert raw daily zips into filtered, typed Parquet partitions.

Each day's zip becomes data/parquet/<date>.parquet containing only the columns
the app uses, with numbers/timestamps parsed (source CSV uses thousands
separators and a trailing '+' on capped notionals) and clearly irrelevant
local-market rows dropped (see EXCLUDE_UNDERLIER_REGEXES).

Idempotent: days whose parquet already exists are skipped unless --force.

Usage:
    python -m otc.ingest            # all raw zips without parquet yet
    python -m otc.ingest --force    # reprocess everything
"""
from __future__ import annotations

import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import duckdb

from .config import EXCLUDE_UNDERLIER_REGEXES, PARQUET_DIR, RAW_DIR

# Source column -> (output name, parse kind)
COLUMNS: dict[str, tuple[str, str]] = {
    "Dissemination Identifier": ("dissemination_id", "bigint"),
    "Original Dissemination Identifier": ("original_dissemination_id", "bigint"),
    "Action type": ("action_type", "text"),
    "Event type": ("event_type", "text"),
    "Event timestamp": ("event_ts", "timestamp"),
    "Execution Timestamp": ("execution_ts", "timestamp"),
    "Effective Date": ("effective_date", "date"),
    "Expiration Date": ("expiration_date", "date"),
    "Cleared": ("cleared", "text"),
    "Platform identifier": ("platform_id", "text"),
    "Block trade election indicator": ("block_trade", "bool"),
    "Notional amount-Leg 1": ("notional_leg1", "number"),
    "Notional amount-Leg 2": ("notional_leg2", "number"),
    "Notional currency-Leg 1": ("notional_ccy_leg1", "text"),
    "Notional currency-Leg 2": ("notional_ccy_leg2", "text"),
    "Total notional quantity-Leg 1": ("total_notional_qty_leg1", "number"),
    "Quantity unit of measure-Leg 1": ("qty_unit_leg1", "text"),
    "Price": ("price", "number"),
    "Price unit of measure": ("price_unit", "text"),
    "Price currency": ("price_ccy", "text"),
    "Price notation": ("price_notation", "text"),
    "Spread-Leg 1": ("spread_leg1", "number"),
    "Spread-Leg 2": ("spread_leg2", "number"),
    "Strike Price": ("strike_price", "number"),
    "Option Premium Amount": ("option_premium", "number"),
    "Option Premium Currency": ("option_premium_ccy", "text"),
    "Option Type": ("option_type", "text"),
    "Option Style": ("option_style", "text"),
    "First exercise date": ("first_exercise_date", "date"),
    "Settlement currency-Leg 1": ("settlement_ccy_leg1", "text"),
    "Custom basket indicator": ("custom_basket", "bool"),
    "Package indicator": ("package", "bool"),
    "Unique Product Identifier": ("upi", "text"),
    "UPI FISN": ("upi_fisn", "text"),
    "UPI Underlier Name": ("underlier_name", "text"),
}

FILE_RE = re.compile(r"(CFTC|SEC)_CUMULATIVE_EQUITIES_(\d{4})_(\d{2})_(\d{2})\.zip$")


def _num(src: str) -> str:
    """Parse a number that may contain thousands separators and a '+' cap marker."""
    return (
        f"try_cast(replace(replace(\"{src}\", ',', ''), '+', '') as double)"
    )


def select_sql(csv_path: str) -> str:
    exprs = []
    for src, (out, kind) in COLUMNS.items():
        if kind == "number":
            exprs.append(f'{_num(src)} as {out}')
        elif kind == "bigint":
            exprs.append(f'try_cast("{src}" as bigint) as {out}')
        elif kind == "timestamp":
            exprs.append(f'try_cast("{src}" as timestamp) as {out}')
        elif kind == "date":
            exprs.append(f'try_cast("{src}" as date) as {out}')
        elif kind == "bool":
            exprs.append(f'(upper(trim("{src}")) in (\'TRUE\',\'Y\',\'1\')) as {out}')
        else:
            exprs.append(f'nullif(trim("{src}"), \'\') as {out}')
    # Capped-notional flag: source shows e.g. "1,100,000,000+"
    exprs.append("(\"Notional amount-Leg 1\" like '%+') as notional_capped")
    exclude = " or ".join(
        f"regexp_matches(coalesce(\"UPI Underlier Name\", ''), '{rx}')"
        for rx in EXCLUDE_UNDERLIER_REGEXES
    )
    return f"""
        select {', '.join(exprs)}
        from read_csv('{csv_path}', all_varchar=true, header=true,
                      delim=',', quote='"', escape='"',
                      ignore_errors=true, null_padding=true, strict_mode=false)
        where not ({exclude})
    """


def ingest_zip(con: duckdb.DuckDBPyConnection, zip_path: Path, out_path: Path) -> int:
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        with tempfile.TemporaryDirectory() as tmp:
            zf.extract(names[0], tmp)
            csv_path = str(Path(tmp) / names[0]).replace("'", "''").replace("\\", "/")
            m = FILE_RE.search(zip_path.name)
            source = m.group(1)
            file_date = f"{m.group(2)}-{m.group(3)}-{m.group(4)}"
            tmp_out = out_path.with_suffix(".parquet.tmp")
            con.execute(f"""
                copy (
                    select *, date '{file_date}' as file_date, '{source}' as source
                    from ({select_sql(csv_path)})
                ) to '{str(tmp_out).replace("\\", "/")}' (format parquet, compression zstd)
            """)
            tmp_out.replace(out_path)
            return con.execute(
                f"select count(*) from read_parquet('{str(out_path).replace('\\', '/')}')"
            ).fetchone()[0]


def main(force: bool = False) -> None:
    zips = sorted(RAW_DIR.glob("*_CUMULATIVE_EQUITIES_*.zip"))
    con = duckdb.connect()
    con.execute("set preserve_insertion_order=false")
    done = skipped = failed = 0
    t0 = time.time()
    for zp in zips:
        m = FILE_RE.search(zp.name)
        out = PARQUET_DIR / f"{m.group(1)}_{m.group(2)}-{m.group(3)}-{m.group(4)}.parquet"
        if out.exists() and not force:
            skipped += 1
            continue
        try:
            n = ingest_zip(con, zp, out)
            done += 1
            if done % 20 == 0:
                print(f"ingested {done} files ({skipped} cached) elapsed={time.time()-t0:.0f}s", flush=True)
        except Exception as e:  # noqa: BLE001 - keep going, report at end
            failed += 1
            print(f"FAILED {zp.name}: {e}", flush=True)
    print(f"Done. ingested={done} cached={skipped} failed={failed} elapsed={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
