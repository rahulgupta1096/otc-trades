"""Per-underlier daily spot reference, for option moneyness.

Disseminated prices for one underlier mix regimes: true levels (index points,
share prices) alongside per-unit contract prices that are just notional/qty in
some vehicle's units. No single-day statistic separates them reliably, so this
tracks each underlier's level through time: a day's reference is the median of
that day's candidate prints that sit within a band of the previous reference,
and days with no consistent print carry the last reference forward.

Candidates, per (underlier, day):
  - non-option swap prints (TRS / price-return FISNs) whose price is NOT
    exactly notional/qty (those carry no independent information), plus
    anything explicitly quoted in index points (IPNT);
  - if a day has no swap candidates, that day's option strikes stand in —
    ladders are struck around spot, and the band keeps far wings out.

Only underliers that ever printed an option are tracked (moneyness is the sole
consumer). Re-anchors from scratch after 20 consecutive out-of-band days.
"""
from __future__ import annotations

import duckdb
import pandas as pd

BAND_LO, BAND_HI = 0.80, 1.25
MAX_STALE_DAYS = 20
INIT_WINDOW = 1.20  # densest-window width used when (re-)anchoring

CANDIDATES_SQL = """
    with optioned as (
        select distinct underlier_name from {src}
        where (upi_fisn like 'NA/O%' or strike_price is not null)
          and underlier_name is not null
    )
    select underlier_name, execution_date, price, 'swap' as kind
    from {src}
    where underlier_name in (select underlier_name from optioned)
      and status <> 'error'
      and upi_fisn not like 'NA/O%' and strike_price is null
      and price > 0 and execution_date is not null
      and (upi_fisn like '%Tot Rtn%' or upi_fisn like '%TRtn%' or upi_fisn like '%Pr%')
      and (price_unit = 'IPNT'
           or total_notional_qty_leg1 is null or notional_leg1 is null
           or total_notional_qty_leg1 <= 0
           or abs(price * total_notional_qty_leg1 - notional_leg1) > 0.001 * notional_leg1)
    union all
    select underlier_name, execution_date, strike_price, 'opt' as kind
    from {src}
    where underlier_name is not null
      and status <> 'error'
      and strike_price > 0 and execution_date is not null
"""


def _densest_median(values: list[float]) -> float:
    """Median of the densest multiplicative window (x .. x*INIT_WINDOW)."""
    vs = sorted(values)
    best_i, best_j = 0, 1
    j = 0
    for i in range(len(vs)):
        if j < i + 1:
            j = i + 1
        while j < len(vs) and vs[j] <= vs[i] * INIT_WINDOW:
            j += 1
        if j - i > best_j - best_i:
            best_i, best_j = i, j
    win = vs[best_i:best_j]
    return float(pd.Series(win).median())


def compute_spot_ref(con: duckdb.DuckDBPyConnection, src: str = "trades") -> pd.DataFrame:
    """Returns DataFrame(underlier_name, execution_date, ref_price)."""
    cand = con.execute(CANDIDATES_SQL.format(src=src)).df()
    cand = cand.dropna(subset=["underlier_name", "execution_date", "price"])
    out_u, out_d, out_r = [], [], []
    for underlier, g in cand.groupby("underlier_name", sort=False):
        ref = None
        stale = 0
        for day, dg in g.sort_values("execution_date").groupby("execution_date"):
            swaps = dg.loc[dg["kind"] == "swap", "price"].tolist()
            day_cands = swaps if swaps else dg.loc[dg["kind"] == "opt", "price"].tolist()
            if not day_cands:
                continue
            if ref is None or stale > MAX_STALE_DAYS:
                ref = _densest_median(day_cands)
                stale = 0
            else:
                in_band = [c for c in day_cands if BAND_LO * ref <= c <= BAND_HI * ref]
                if in_band:
                    ref = float(pd.Series(in_band).median())
                    stale = 0
                else:
                    stale += 1  # carry the previous reference forward
            out_u.append(underlier)
            out_d.append(day)
            out_r.append(ref)
    return pd.DataFrame({
        "underlier_name": out_u,
        "execution_date": out_d,
        "ref_price": out_r,
    })


def build_spot_ref_table(con: duckdb.DuckDBPyConnection, src: str = "trades") -> int:
    df = compute_spot_ref(con, src)
    con.register("spot_ref_df", df)
    con.execute("""
        create or replace table spot_ref as
        select underlier_name, cast(execution_date as date) execution_date,
               ref_price
        from spot_ref_df
    """)
    con.unregister("spot_ref_df")
    return len(df)
