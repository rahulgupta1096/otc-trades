"""FastAPI backend serving the deduplicated trades from DuckDB.

Run:  python -m otc.app   (serves http://127.0.0.1:8321)

The connection is read-only so the DB file stays safe; run the update pipeline
(python -m otc.update) while the app is stopped, then restart it.
"""
from __future__ import annotations

import threading
from typing import Optional

import duckdb
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import DB_PATH, PROJECT_ROOT

app = FastAPI(title="OTC Equity Trades")

_con: Optional[duckdb.DuckDBPyConnection] = None
_lock = threading.Lock()


def db() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is None:
        if not DB_PATH.exists():
            raise HTTPException(503, "Database not built yet - run: python -m otc.build")
        _con = duckdb.connect(str(DB_PATH), read_only=True)
    return _con


def q(sql: str, params: list) -> list[dict]:
    with _lock:  # duckdb connections are not thread-safe across concurrent cursors
        cur = db().execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------- filters

SORTABLE = {
    "execution_ts", "execution_date", "effective_date", "expiration_date",
    "notional_leg1", "price", "strike_price", "total_notional_qty_leg1",
    "underlier_name", "event_ts", "dissemination_id",
    "tenor_yrs", "per_unit", "moneyness", "lag_seconds", "option_premium",
}

GROUP_BYS = {
    "all": "'all'",
    "underlier_name": "underlier_name",
    "execution_date": "execution_date",
    "week": "date_trunc('week', execution_date)",
    "month": "date_trunc('month', execution_date)",
    "platform_id": "platform_id",
    "upi_fisn": "upi_fisn",
    "option_type": "option_type",
    "status": "status",
    "cleared": "cleared",
    "notional_ccy_leg1": "notional_ccy_leg1",
}


def build_where(
    underlier: Optional[str], underlier_exact: Optional[str], fisn: Optional[str],
    status: Optional[str], date_from: Optional[str], date_to: Optional[str],
    exp_from: Optional[str], exp_to: Optional[str],
    min_notional: Optional[float], max_notional: Optional[float],
    platform: Optional[str], cleared: Optional[str], option_type: Optional[str],
    ccy: Optional[str],
) -> tuple[str, list]:
    clauses, params = [], []
    if underlier:
        clauses.append("upper(coalesce(underlier_name,'')) like '%' || upper(?) || '%'")
        params.append(underlier)
    if underlier_exact:
        clauses.append("underlier_name = ?")
        params.append(underlier_exact)
    if fisn:
        clauses.append("upper(coalesce(upi_fisn,'')) like '%' || upper(?) || '%'")
        params.append(fisn)
    statuses = [s.strip() for s in (status or "active,terminated").split(",") if s.strip()]
    clauses.append(f"status in ({','.join('?' * len(statuses))})")
    params.extend(statuses)
    for col, lo, hi in [("execution_date", date_from, date_to), ("expiration_date", exp_from, exp_to)]:
        if lo:
            clauses.append(f"{col} >= ?"); params.append(lo)
        if hi:
            clauses.append(f"{col} <= ?"); params.append(hi)
    if min_notional is not None:
        clauses.append("notional_leg1 >= ?"); params.append(min_notional)
    if max_notional is not None:
        clauses.append("notional_leg1 <= ?"); params.append(max_notional)
    if platform:
        clauses.append("platform_id = ?"); params.append(platform)
    if cleared:
        clauses.append("cleared = ?"); params.append(cleared)
    if option_type:
        clauses.append("option_type = ?"); params.append(option_type)
    if ccy:
        clauses.append("notional_ccy_leg1 = ?"); params.append(ccy)
    return " and ".join(clauses), params


# ---------------------------------------------------------------- endpoints

@app.get("/api/meta")
def meta():
    r = q("""
        select min(execution_date) filter (where execution_date >= date '2000-01-01') min_date,
               max(execution_date) max_date,
               max(file_date) latest_file, count(*) n_trades
        from trades where status <> 'error'
    """, [])[0]
    r["platforms"] = [x["platform_id"] for x in q(
        "select platform_id, count(*) n from trades where platform_id is not null group by 1 order by n desc limit 50", [])]
    r["fisns"] = [x["upi_fisn"] for x in q(
        "select upi_fisn, count(*) n from trades where upi_fisn is not null group by 1 order by n desc limit 50", [])]
    r["currencies"] = [x["notional_ccy_leg1"] for x in q(
        "select notional_ccy_leg1, count(*) n from trades where notional_ccy_leg1 is not null group by 1 order by n desc limit 30", [])]
    return r


@app.get("/api/underliers")
def underliers(query: str = Query("", alias="q"), limit: int = Query(20, le=100)):
    return q("""
        select underlier_name, n_trades, total_notional_leg1
        from underliers
        where underlier_name is not null
          and upper(underlier_name) like '%' || upper(?) || '%'
        order by n_trades desc limit ?
    """, [query, limit])


@app.get("/api/trades")
def trades(
    underlier: Optional[str] = None, underlier_exact: Optional[str] = None,
    fisn: Optional[str] = None, status: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
    exp_from: Optional[str] = None, exp_to: Optional[str] = None,
    min_notional: Optional[float] = None, max_notional: Optional[float] = None,
    platform: Optional[str] = None, cleared: Optional[str] = None,
    option_type: Optional[str] = None, ccy: Optional[str] = None,
    sort_by: str = "execution_ts", sort_dir: str = "desc",
    limit: int = Query(100, le=1000), offset: int = Query(0, ge=0),
):
    if sort_by not in SORTABLE:
        raise HTTPException(400, f"sort_by must be one of {sorted(SORTABLE)}")
    if sort_dir not in ("asc", "desc"):
        raise HTTPException(400, "sort_dir must be asc or desc")
    where, params = build_where(
        underlier, underlier_exact, fisn, status, date_from, date_to, exp_from,
        exp_to, min_notional, max_notional, platform, cleared, option_type, ccy)
    total = q(f"select count(*) n from trades where {where}", params)[0]["n"]
    rows = q(f"""
        select dissemination_id, chain_id, status, action_type, event_type,
               execution_ts, effective_date, expiration_date,
               underlier_name, upi_fisn, upi,
               notional_leg1, notional_ccy_leg1, notional_capped,
               total_notional_qty_leg1, qty_unit_leg1,
               price, price_ccy, price_unit, spread_leg1, spread_leg2,
               strike_price, option_type, option_style, option_premium,
               platform_id, cleared, block_trade, custom_basket, package,
               event_ts, file_date, first_event_ts,
               datediff('day', execution_date, expiration_date) / 365.25 as tenor_yrs,
               notional_leg1 / nullif(total_notional_qty_leg1, 0) as per_unit,
               strike_price / nullif(ref_price, 0) as moneyness,
               epoch(first_event_ts - execution_ts) as lag_seconds
        from trades
        left join spot_ref using (underlier_name, execution_date)
        where {where}
        order by {sort_by} {sort_dir} nulls last
        limit ? offset ?
    """, params + [limit, offset])
    return {"total": total, "rows": rows}


@app.get("/api/aggregate")
def aggregate(
    group_by: str = "execution_date",
    underlier: Optional[str] = None, underlier_exact: Optional[str] = None,
    fisn: Optional[str] = None, status: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
    exp_from: Optional[str] = None, exp_to: Optional[str] = None,
    min_notional: Optional[float] = None, max_notional: Optional[float] = None,
    platform: Optional[str] = None, cleared: Optional[str] = None,
    option_type: Optional[str] = None, ccy: Optional[str] = None,
    limit: int = Query(500, le=5000),
):
    if group_by not in GROUP_BYS:
        raise HTTPException(400, f"group_by must be one of {sorted(GROUP_BYS)}")
    expr = GROUP_BYS[group_by]
    where, params = build_where(
        underlier, underlier_exact, fisn, status, date_from, date_to, exp_from,
        exp_to, min_notional, max_notional, platform, cleared, option_type, ccy)
    order = "1" if group_by in ("execution_date", "week", "month") else "sum_notional desc nulls last"
    return q(f"""
        select {expr} as key,
               count(*) as n_trades,
               sum(notional_leg1) as sum_notional,
               median(notional_leg1) as median_notional,
               avg(price) as avg_price,
               sum(total_notional_qty_leg1) as sum_qty
        from trades where {where}
        group by 1 order by {order} limit ?
    """, params + [limit])


web_dir = PROJECT_ROOT / "web"


@app.get("/")
def index():
    return FileResponse(web_dir / "index.html")


app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8321)
