# OTC Equity Trades

Local webapp over the CFTC **Cumulative Equities** swap-dissemination data from
[DTCC's public price dissemination portal](https://pddata.dtcc.com/ppd/cftcdashboard),
with filtering, querying, and aggregation on the **final state of each trade**
(revision history collapsed away).

## Pipeline

```
DTCC daily zips  ──download──▶  data/raw/*.zip        (archive of record, ~25 GB)
                 ──ingest────▶  data/parquet/*.parquet (typed, trimmed, filtered)
                 ──build─────▶  data/trades.duckdb     (one row per trade = final print)
                                      ▲
                        FastAPI app ──┘ (read-only)
```

- **download** (`python -m otc.download [start] [end]`): fetches
  `CFTC_CUMULATIVE_EQUITIES_YYYY_MM_DD.zip` for every calendar day. The portal
  keeps a rolling ~2-year window (dates before ~Aug 2024 are gone) and is flaky
  — downloads are validated (zip magic bytes) and retried; transient 404s are
  only trusted after repeating. Already-valid files are skipped.
- **ingest** (`python -m otc.ingest [--force]`): unzips each day, keeps ~36
  useful columns of the 110, parses numbers ("1,234,567+" → value + capped
  flag) and timestamps, and drops clearly irrelevant local-market rows
  (Taiwan TWSE/TPEX buckets, Chinese A-shares, Thai NVDRs — see
  `EXCLUDE_UNDERLIER_REGEXES` in `otc/config.py`). One zstd parquet per day.
- **build** (`python -m otc.build [--rebuild]`): collapses revision chains.
  Every row's chain key is `coalesce(original_dissemination_id,
  dissemination_id)`; the latest row per chain (by event timestamp) wins.
  Status per final print: `active`, `terminated` (TERM/ETRM — traded, later
  unwound), or `error` (EROR — reported in error; hidden by default in the
  app). Incremental: new parquet files are merged without a full rebuild.
- **update** (`python -m otc.update`): download last 10 days → ingest → build.
  Run it while the app is stopped (build needs the DuckDB write lock).

## Running the app

```powershell
.venv\Scripts\python.exe -m otc.app
# → http://127.0.0.1:8321
```

Filters (date presets, underlier autocomplete, product FISN, status, min
notional, currency) scope everything on the page: stat tiles, the daily/weekly
volume chart, the group-by aggregation table, and the paginated/sortable trades
table.

## Data notes

- The meaningful underlier field is **UPI Underlier Name** (e.g.
  `S&P 500 INDEX`); legacy "Underlying Asset Name" columns are empty in current
  files. Beware: a Chinese A-share named "S&P" exists — search
  `S&P 500 INDEX`, not `S&P`.
- `NA/Swaps Idx …` FISN values are index products; `SStk`/`Sgle Stk` are
  single-stock; `O …` are options; `Nstd` is non-standard.
- Notional amounts above the CFTC cap are disseminated truncated with a `+`
  (kept as `notional_capped`). Some rows carry garbage sentinel notionals
  (~1e20); build nulls any notional/quantity ≥ 1e15 so aggregates stay sane.
  Epoch-garbage execution timestamps also occur (min date clamped in the UI).
- Chains can span the retention boundary: a 2018 trade terminated in 2025
  appears only via its lifecycle rows, its original print being before the
  window. Expect `min(execution_date)` far earlier than the first file date.

## Hosted version (GitHub Pages)

`site/` is a serverless twin of the app: the same UI querying an **index-products
subset** (~360k final prints, ~11 MB Parquet) in the browser via DuckDB-WASM.
GitHub Actions (`.github/workflows/update-data.yml`) refreshes it daily at
12:00 UTC: download recent DTCC files → merge new/amended chains into the
monthly partitions in `site/data/months/` (committed) → regenerate the combined
`site/data/trades.parquet` + `meta.json` (build artifacts) → deploy to Pages.

Subset scope: `UPI FISN` contains `Idx` or underlier name contains
`INDEX`/`IDX` — all S&P/NDX/Eurostoxx/etc. index swaps and options. Local
rebuild of the subset from the full dataset: `python -m otc.subset rebuild`.

## Layout

```
otc/config.py    paths, start date, exclude filters
otc/download.py  otc/ingest.py  otc/build.py  otc/update.py  otc/app.py
web/             static frontend (vanilla JS, no build step)
data/            raw zips, parquet, trades.duckdb (not in git)
```
