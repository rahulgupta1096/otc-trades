"""Build the deduplicated final-prints table in DuckDB from Parquet partitions.

Every disseminated row belongs to a revision chain keyed by
coalesce(original_dissemination_id, dissemination_id): a new trade (NEWT) roots
a chain, and MODI/CORR/EROR/TERM rows reference the root. The app only cares
about the final state of each chain, so `trades` holds exactly one row per
chain — the latest by (event_ts, file_date, dissemination_id) — with a status
derived from its action:

    error       final action EROR (reported in error; hidden by default in app)
    terminated  final action TERM / event ETRM (traded, later unwound)
    active      everything else

Incremental: files already merged are recorded in `ingested_files`. For new
files only the touched chains are recomputed, comparing new rows against the
chain's previous final row (older superseded rows can never win, so they are
not needed).

Usage:
    python -m otc.build            # merge any new parquet files
    python -m otc.build --rebuild  # drop and rebuild from scratch
"""
from __future__ import annotations

import sys
import time

import duckdb

from .config import DATA_DIR, DB_PATH, PARQUET_DIR

FINAL_SELECT = """
    select * exclude (rn)
    from (
        select *,
            row_number() over (
                partition by chain_id
                order by event_ts desc nulls last, file_date desc, dissemination_id desc
            ) as rn
        from ({src})
    )
    where rn = 1
"""

# Values >= 1e15 are reporting-error sentinels (observed ~1e20); no real equity
# swap notional reaches a quadrillion in any currency. Nulled so aggregates and
# sorts stay meaningful; the row itself is kept.
BASE_COLUMNS_SQL = """
    * replace (
        case when notional_leg1 < 1e15 then notional_leg1 end as notional_leg1,
        case when notional_leg2 < 1e15 then notional_leg2 end as notional_leg2,
        case when total_notional_qty_leg1 < 1e15 then total_notional_qty_leg1 end
            as total_notional_qty_leg1
    ),
    coalesce(original_dissemination_id, dissemination_id) as chain_id,
    case
        when action_type = 'EROR' then 'error'
        when action_type = 'TERM' or event_type = 'ETRM' then 'terminated'
        else 'active'
    end as status,
    cast(execution_ts as date) as execution_date
"""


def parquet_glob() -> str:
    return str(PARQUET_DIR / "*.parquet").replace("\\", "/")


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(DB_PATH))
    tmp = DATA_DIR / "duckdb_tmp"
    tmp.mkdir(exist_ok=True)
    con.execute(f"set temp_directory='{str(tmp).replace(chr(92), '/')}'")
    con.execute("set preserve_insertion_order=false")
    return con


def full_build(con: duckdb.DuckDBPyConnection) -> None:
    print("Full rebuild from", parquet_glob(), flush=True)
    src = f"select {BASE_COLUMNS_SQL} from read_parquet('{parquet_glob()}')"
    con.execute(f"create or replace table trades as {FINAL_SELECT.format(src=src)}")
    con.execute(f"""
        create or replace table ingested_files as
        select distinct cast(parse_filename(filename, true) as date) as file_date
        from glob('{parquet_glob()}') t(filename)
    """)


def incremental(con: duckdb.DuckDBPyConnection) -> int:
    new_files = con.execute(f"""
        select filename from glob('{parquet_glob()}') t(filename)
        where cast(parse_filename(filename, true) as date) not in
              (select file_date from ingested_files)
        order by 1
    """).fetchall()
    if not new_files:
        return 0
    files_list = ", ".join(f"'{f[0]}'" for f in new_files)
    print(f"Merging {len(new_files)} new file(s)", flush=True)
    con.execute(f"""
        create or replace temp table new_rows as
        select {BASE_COLUMNS_SQL} from read_parquet([{files_list}])
    """)
    src = """
        select * from new_rows
        union all by name
        select * from trades where chain_id in (select distinct chain_id from new_rows)
    """
    con.execute(f"create or replace temp table merged as {FINAL_SELECT.format(src=src)}")
    con.execute("delete from trades where chain_id in (select chain_id from merged)")
    con.execute("insert into trades by name select * from merged")
    con.execute(f"""
        insert into ingested_files
        select distinct cast(parse_filename(filename, true) as date)
        from glob('{parquet_glob()}') t(filename)
        where cast(parse_filename(filename, true) as date) not in
              (select file_date from ingested_files)
    """)
    return len(new_files)


def build_summaries(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        create or replace table underliers as
        select underlier_name,
               count(*) as n_trades,
               sum(notional_leg1) as total_notional_leg1,
               min(execution_date) as first_seen,
               max(execution_date) as last_seen
        from trades
        where status <> 'error'
        group by 1
    """)


def main(rebuild: bool = False) -> None:
    t0 = time.time()
    con = connect()
    have_trades = con.execute(
        "select count(*) from information_schema.tables where table_name='trades'"
    ).fetchone()[0]
    if rebuild or not have_trades:
        full_build(con)
    else:
        if incremental(con) == 0:
            print("No new files.")
    build_summaries(con)
    stats = con.execute("""
        select status, count(*) n from trades group by 1 order by 1
    """).fetchall()
    total = con.execute("select count(*) from trades").fetchone()[0]
    print(f"trades table: {total:,} final prints  {dict((s, n) for s, n in stats)}")
    print(f"elapsed {time.time()-t0:.0f}s")
    con.close()


if __name__ == "__main__":
    main(rebuild="--rebuild" in sys.argv)
