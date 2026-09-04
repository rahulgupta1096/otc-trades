"""Maintain the hosted index-products subset under site/data/.

The GitHub Pages app queries a single Parquet of final prints for *index
products only* (UPI FISN contains 'Idx' or the underlier name looks like an
index). State lives as monthly partitions in site/data/months/ — committed to
git — so the daily GitHub Actions run only rewrites the months actually touched
by new or amended chains. The combined site/data/trades.parquet and meta.json
are derived artifacts (gitignored; regenerated every run and shipped in the
Pages artifact).

Usage:
    python -m otc.subset rebuild   # from local data/parquet/*.parquet (full history)
    python -m otc.subset update    # merge raw zips not yet in state (Actions path)
"""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from .build import BASE_COLUMNS_SQL, FINAL_SELECT
from .config import PROJECT_ROOT, RAW_DIR
from .ingest import FILE_RE, select_sql

SITE_DATA = PROJECT_ROOT / "site" / "data"
MONTHS_DIR = SITE_DATA / "months"
STATE_PATH = MONTHS_DIR / "state.json"

SUBSET_WHERE = """(
    coalesce(upi_fisn, '') like '%Idx%'
    or upper(coalesce(underlier_name, '')) like '%INDEX%'
    or upper(coalesce(underlier_name, '')) like '%IDX%'
)"""

# Partition key: execution month when sane, else the file month (some rows
# carry epoch-garbage execution timestamps).
MONTH_EXPR = """strftime(
    case when execution_date between date '2005-01-01' and file_date
         then execution_date else file_date end, '%Y-%m')"""


def _posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def load_state() -> dict:
    if STATE_PATH.exists():
        s = json.loads(STATE_PATH.read_text())
        if "merged_file_dates" in s:  # legacy, CFTC-only format
            s = {"merged_files": [f"CFTC_{d}" for d in s["merged_file_dates"]]}
        return s
    return {"merged_files": []}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=1))


def write_months(con: duckdb.DuckDBPyConnection, source: str, months: list[str] | None) -> None:
    """Write per-month parquet files from `source` (a table/view with a month column)."""
    MONTHS_DIR.mkdir(parents=True, exist_ok=True)
    if months is None:
        months = [r[0] for r in con.execute(f"select distinct month from {source} order by 1").fetchall()]
    for month in months:
        out = MONTHS_DIR / f"{month}.parquet"
        n = con.execute(f"select count(*) from {source} where month = ?", [month]).fetchone()[0]
        if n == 0:
            out.unlink(missing_ok=True)
            continue
        con.execute(f"""
            copy (select * from {source} where month = '{month}' order by execution_ts)
            to '{_posix(out)}' (format parquet, compression zstd)
        """)


def export_site(con: duckdb.DuckDBPyConnection) -> None:
    from .spotref import compute_spot_ref

    glob = _posix(MONTHS_DIR / "*.parquet")
    out = SITE_DATA / "trades.parquet"
    con.execute(f"""
        copy (select * from read_parquet('{glob}') order by execution_ts)
        to '{_posix(out)}' (format parquet, compression zstd)
    """)
    con.execute(f"""
        create or replace temp view subset_trades as
        select * from read_parquet('{glob}')
    """)
    ref_df = compute_spot_ref(con, src="subset_trades")
    con.register("spot_ref_df", ref_df)
    con.execute(f"""
        copy (select underlier_name, cast(execution_date as date) execution_date,
                     ref_price
              from spot_ref_df)
        to '{_posix(SITE_DATA / "spot_ref.parquet")}' (format parquet, compression zstd)
    """)
    con.unregister("spot_ref_df")
    n, latest = con.execute(
        f"select count(*), max(file_date) from read_parquet('{glob}')"
    ).fetchone()
    (SITE_DATA / "meta.json").write_text(json.dumps({
        "n_trades": n,
        "latest_file": str(latest),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }))
    print(f"site export: {n:,} final prints, latest file {latest}, "
          f"{out.stat().st_size / 1e6:.1f} MB")


def rebuild() -> None:
    from .config import PARQUET_DIR
    con = duckdb.connect()
    con.execute("set preserve_insertion_order=false")
    src = f"""
        select {BASE_COLUMNS_SQL} from read_parquet('{_posix(PARQUET_DIR / "*.parquet")}')
        where {SUBSET_WHERE}
    """
    con.execute(f"""
        create temp table final as
        select *, {MONTH_EXPR} as month from ({FINAL_SELECT.format(src=src)})
    """)
    for old in MONTHS_DIR.glob("*.parquet"):
        old.unlink()
    write_months(con, "final", None)
    file_keys = [r[0] for r in con.execute(f"""
        select distinct parse_filename(filename, true)
        from glob('{_posix(PARQUET_DIR / "*.parquet")}') t(filename) order by 1
    """).fetchall()]
    save_state({"merged_files": file_keys})
    export_site(con)


def update() -> None:
    state = load_state()
    merged = set(state["merged_files"])
    new_zips = []
    for zp in sorted(RAW_DIR.glob("*_CUMULATIVE_EQUITIES_*.zip")):
        m = FILE_RE.search(zp.name)
        source = m.group(1)
        fd = f"{m.group(2)}-{m.group(3)}-{m.group(4)}"
        key = f"{source}_{fd}"
        if key not in merged:
            new_zips.append((key, source, fd, zp))
    if not new_zips:
        print("No new files to merge.")
        con = duckdb.connect()
        export_site(con)
        return

    con = duckdb.connect()
    con.execute("set preserve_insertion_order=false")
    con.execute("create temp table new_rows as select * from (select 1) where false")
    first = True
    for key, source, fd, zp in new_zips:
        with zipfile.ZipFile(zp) as zf:
            names = [n for n in zf.namelist() if n.endswith(".csv")]
            with tempfile.TemporaryDirectory() as tmp:
                zf.extract(names[0], tmp)
                csv_path = _posix(Path(tmp) / names[0]).replace("'", "''")
                sql = f"""
                    select {BASE_COLUMNS_SQL} from (
                        select *, date '{fd}' as file_date, '{source}' as source
                        from ({select_sql(csv_path)})
                    ) where {SUBSET_WHERE}
                """
                if first:
                    con.execute(f"create or replace temp table new_rows as {sql}")
                    first = False
                else:
                    con.execute(f"insert into new_rows {sql}")
        print(f"read {key}", flush=True)

    existing_glob = _posix(MONTHS_DIR / "*.parquet")
    have_existing = any(MONTHS_DIR.glob("*.parquet"))
    if have_existing:
        con.execute(f"""
            create temp table existing as
            select * from read_parquet('{existing_glob}')
        """)
    else:
        con.execute("""
            create temp table existing as
            select *, '' as month from new_rows where false
        """)

    src = """
        select * exclude (month) from existing e
        where exists (select 1 from new_rows n
                      where n.source = e.source and n.chain_id = e.chain_id)
        union all by name
        select * from new_rows
    """
    con.execute(f"""
        create temp table merged_final as
        select *, {MONTH_EXPR} as month from ({FINAL_SELECT.format(src=src)})
    """)
    # months to rewrite: where affected chains lived before + where they live now
    touched = {r[0] for r in con.execute("""
        select distinct month from existing e
        where exists (select 1 from new_rows n
                      where n.source = e.source and n.chain_id = e.chain_id)
        union
        select distinct month from merged_final
    """).fetchall()}
    con.execute("""
        create temp table new_state as
        select * from existing e
        where not exists (select 1 from new_rows n
                          where n.source = e.source and n.chain_id = e.chain_id)
        union all by name
        select * from merged_final
    """)
    write_months(con, "new_state", sorted(touched))
    state["merged_files"] = sorted(merged | {key for key, _, _, _ in new_zips})
    save_state(state)
    export_site(con)
    print(f"merged {len(new_zips)} new file(s); rewrote months: {', '.join(sorted(touched))}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "update"
    if cmd == "rebuild":
        rebuild()
    else:
        update()
