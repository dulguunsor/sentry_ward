# Sentry Ward

Reveals when hedge funds that look independent are secretly holding the same stocks,
using the holdings they report to the SEC every quarter on Form 13F.

The name comes from Dota, where a sentry ward reveals invisible units. This tool does
the same for crowding hidden behind diversification numbers.

Multi-manager funds run many teams that are supposed to be independent. When those
teams use the same data and models, they end up in the same trades, and a sell-off
in one fund can spread to others. This project measures that overlap from public data.

## What it produces

For each quarter:

- **Most crowded stocks**: how many funds in the universe hold each stock, how much
  of their portfolios it takes up, and a 0 to 100 crowding score.
- **Fund-to-fund overlap**: the share of two portfolios that is identical
  (0 = nothing in common, 1 = the same portfolio).
- **Crowding index**: the average overlap across all fund pairs, tracked over time.

Results are printed and written to `data/out/` as CSV and JSON.

## Quick start

```bash
pip install -e ".[dev]"

# Try it on small sample data (no download needed)
export WARD_DATA=demo
ward sample
ward load
ward build
ward report

# Real data from SEC
export WARD_DATA=data
export SEC_USER_AGENT="Your Name your.email@example.com"   # SEC requires this
ward download --since 2019     # several GB; files already on disk are skipped
ward load
ward build --universe data/universe.csv
ward report --period 2026-06-30
```

On Windows PowerShell, set variables like this instead of `export`:

```powershell
$env:WARD_DATA = "data"
$env:SEC_USER_AGENT = "Your Name your.email@example.com"
```

## Pipeline

| Step | Module | What it does |
|---|---|---|
| `download` | `sec13f.py` | Finds every 13F data set zip on SEC's page and downloads it |
| `load` | `sec13f.py`, `clean.py` | Loads the raw tables into DuckDB, then builds one clean `holdings` table |
| `build` | `metrics.py` | Matches the fund list to filers, computes weights, scores and overlaps |
| `report` | `cli.py` | Prints one quarter and exports CSV and JSON for the dashboard |

## Data rules

13F data is messy. Each rule below is a deliberate choice, and each one has a test.

1. **Holdings reports only.** 13F-HR and 13F-HR/A. Notice filings list no positions.
2. **Amendments.** For each filer and quarter, the latest original or restatement is
   used. "New holdings" amendments filed after it are added. An amendment with no
   type is treated as a restatement so positions are never counted twice.
3. **Units.** Filings before 2023-01-03 reported value in thousands of dollars; later
   filings report dollars. Everything is converted to dollars.
4. **Long stock positions only.** Options (PUT/CALL) and principal amounts (bonds)
   are dropped.
5. **Split rows are summed.** One filing can list the same stock several times.
6. **One company, one position.** Share classes of the same company (Alphabet class A
   and C) are combined using the first 6 characters of the CUSIP, which identify the
   issuer. Fund trusts such as iShares or SPDR are not combined, because one trust
   code covers many different ETFs.
7. **One fund, many filers.** Filers matching the same name in `universe.csv` are
   combined, so a fund is never compared with itself.

## Metric definitions

- `weight`: position value / fund's total 13F value that quarter
- `holder_share`: funds holding the stock / funds in the universe
- `sum_weight`: sum of all funds' weights in the stock
- `hf_ownership`: shares held by the universe / shares outstanding (needs market data)
- `days_to_exit`: shares held / (20% of average daily volume) (needs market data)
- `crowding_score`: 0 to 100, among stocks held by at least 2 funds. `holder_share`
  and `sum_weight` are divided by the quarter's highest value, so the most crowded
  stock scores near 100 and the rest spread out below it. `hf_ownership` and
  `days_to_exit` use percentile ranks because they have extreme outliers.
- `crowding_rank`: position in the quarter's list, 1 = most crowded
- `overlap(A, B)`: sum over stocks of min(weight_A, weight_B)
- `crowding_index`: average overlap across all fund pairs

## Fund universe

`data/universe.csv` lists funds by name pattern. `ward build` prints which SEC
filers each pattern matched and which matched nothing. Check that list: names change
over time and some firms file under several entities.

## What 13F cannot show

- Short positions, futures, swaps and non-US stocks are not reported, so true
  crowding is likely higher than measured.
- Filings come quarterly, up to 45 days after quarter end.
- Some funds get confidential treatment for certain positions.

## Tests

```bash
pytest
```

The tests use synthetic filings small enough to check by hand. For example, three
sample funds have overlaps of 0.80, 0.25 and 0.25, so the crowding index must be
1.30 / 3. See `src/crowding/sample.py`.

## Roadmap

- [x] 13F download, loading and cleaning
- [x] Crowding scores, fund overlap, crowding index
- [ ] Market data: prices, volume, shares outstanding (CUSIP to ticker mapping)
- [ ] Stress tests: do crowded stocks fall harder in sell-offs? (pre-registered)
- [ ] Unwind simulator
- [ ] Dashboard
