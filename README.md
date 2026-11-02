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
- **Fund-to-fund overlap**, two ways:
  - *Raw overlap*: the share of two portfolios that is identical (0 = nothing in
    common, 1 = the same portfolio). Big diversified funds score high partly just
    because both look like the market.
  - *Active overlap*: of the bets each fund makes beyond the market, how much is the
    same bet. This is the number that shows real crowding.
- **Active share per fund**: how far each fund is from simply holding the market.
- **Crowding index**: the average overlap across fund pairs each quarter, plus the
  change measured only on pairs present in both quarters, so a fund joining or
  leaving does not move the index by itself.
- **ETFs** are listed separately and kept out of all scores, since funds mostly hold
  them as hedges.

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
ward market                    # tickers, prices, volume, shares outstanding
ward report --period 2026-06-30
ward stress                    # do crowded stocks fall more in sell-offs?
```

On Windows PowerShell, set variables like this instead of `export`:

```powershell
$env:WARD_DATA = "data"
$env:SEC_USER_AGENT = "Your Name your.email@example.com"
$env:OPENFIGI_API_KEY = "your-key"   # optional, makes ward market much faster
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
7. **Unit errors are fixed, then stray rows dropped.** Some filers kept reporting in
   thousands after the 2023 switch, or the reverse. For each row, the price per share
   (value / shares) is compared with the median price all filers reported for that
   stock. If a filing's typical ratio is about 1/1000 or 1000, the whole filing is
   rescaled. Any row still more than 10x off is dropped. `ward build` prints both
   counts and a per-fund check.
8. **ETFs are separated.** Identified by sponsor names (iShares, SPDR, Invesco
   QQQ, ProShares and others) and words like ETF or INDEX in the title of class.
   Companies with "Trust" in their name, such as Northern Trust, are not flagged.
9. **One fund, many filers.** Filers matching the same name in `universe.csv` are
   combined, so a fund is never compared with itself.

## Market data (`ward market`)

| Data | Source | Notes |
|---|---|---|
| CUSIP to ticker | [OpenFIGI](https://www.openfigi.com/api) | Free. Codes that are not found are retried once: codes starting with a letter (CINS, for companies incorporated outside the US) as `ID_CINS`, and without an exchange filter, keeping US listings. Without a key about 25 requests a minute of 10 CUSIPs; a free key (set `OPENFIGI_API_KEY`) allows 100 CUSIPs per request. |
| Daily prices, volume, splits | Yahoo Finance via `yfinance` | Unofficial. Delisted stocks usually have no data. |
| Shares outstanding | SEC XBRL frames | One request per quarter covers every US filer. Foreign companies (ADRs) are usually missing. |

Everything is cached, so rerunning only fetches what is missing.

Checks:
- **Splits.** Yahoo adjusts old prices and volumes for later splits. 13F share counts
  are as reported at the time, so the split adjustment is undone before comparing.
- **Wrong tickers.** Tickers get reused after companies disappear. A stock's
  quarter-end close must be within 0.8x to 1.25x of the median price per share 13F
  filers reported, or its market data is not used.
- `ward market` prints how much of the funds' stock value has usable data.

Known gaps: stocks that were later delisted often have no Yahoo data, which matters
for stress tests (failed companies drop out). For share classes merged into one
company, volume comes from the largest class only, so days to exit is overstated.

## Stress test (`ward stress`)

Do crowded stocks fall more than comparable stocks when the market sells off? The
design was written in [PREREGISTRATION.md](PREREGISTRATION.md) and committed before
the test was run on real data, so it could not be tuned to the results.

- **Events:** COVID crash (2020), GameStop-week degrossing (2021), 2022 rate shock,
  yen carry unwind (2024), tariff shock (2025), multi-manager drawdown (March 2026).
- **H1:** top 10% by crowding score vs the rest. **H2:** stocks in the top 25% for both
  crowding and days to exit vs the rest.
- **Matching:** within size x beta buckets, because crowded stocks are often big
  high-beta tech names that fall more in any sell-off. The tests include a case where
  crowded stocks are simply high beta: a plain comparison reports a 6%+ "crowding
  effect", the matched test reports about zero.
- **Statistics:** bootstrap 95% intervals (2,000 resamples, seed 0), a regression
  check, and the same measure in ordinary quarters for comparison.
- `ward stress --public` uses only holdings that were public 45+ days before each event.

## Metric definitions

- `weight`: position value / fund's total stock value that quarter (ETFs excluded)
- `market_weight`: the stock's share of all 13F filers' combined stock holdings, used
  as the "no view" baseline
- `overweight`: max(weight - market_weight, 0)
- `active_share`: sum of a fund's overweights (0 = holds the market)
- `holder_share`: funds holding the stock / funds in the universe
- `sum_weight`: sum of all funds' weights in the stock
- `hf_ownership`: shares held by the universe / shares outstanding (needs market data)
- `days_to_exit`: shares held / (20% of average daily volume) (needs market data)
- `crowding_score`: 0 to 100, among stocks held by at least 2 funds: the average of
  `holder_share` and `sum_weight`, each divided by the quarter's highest value.
- `exit_risk`: 0 to 100, where market data exists: average percentile rank of
  `hf_ownership` and `days_to_exit`. Kept separate from the crowding score so missing
  market data never moves a stock's rank.
- `crowding_rank`: position in the quarter's list, 1 = most crowded
- `overlap(A, B)`: sum over stocks of min(weight_A, weight_B)
- `active_overlap(A, B)`: sum of min(overweight_A, overweight_B) divided by sum of
  max(overweight_A, overweight_B). 0 = no shared bets, 1 = identical bets
- `crowding_index` / `active_index`: average raw / active overlap across fund pairs
- `raw_change` / `active_change`: average change over pairs present in both quarters

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
- [x] Market data: prices, volume, shares outstanding (CUSIP to ticker mapping)
- [x] Stress tests: do crowded stocks fall harder in sell-offs? (pre-registered)
- [ ] Unwind simulator
- [ ] Dashboard
