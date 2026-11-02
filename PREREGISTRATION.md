# Stress test: pre-registered design

Written and committed on 2026-11-02, before the test was run on real data. The git
history of this file is the record. Any change after the first real run is listed
under "Changes after results" with the reason, and the original result is reported too.

## Question

Do stocks crowded by large hedge funds fall more than comparable stocks when the
market sells off?

## Hypotheses

- **H1 (crowding).** In sell-offs, the most crowded stocks return less than matched
  comparison stocks.
- **H2 (crowded and hard to exit).** The effect is larger for stocks that are both
  crowded and slow to sell.

Each is tested one-sided (crowded fall more), but both-sided 95% intervals are reported.

## Events (fixed in advance)

| Event | Start (peak) | End (trough) | Why |
|---|---|---|---|
| COVID crash | 2020-02-19 | 2020-03-23 | Largest sell-off in the sample |
| Hedge fund degrossing, GameStop week | 2021-01-22 | 2021-01-29 | Funds cut gross exposure at record pace |
| 2022 rate shock | 2022-01-03 | 2022-06-16 | Long bear market, growth stocks hit |
| Yen carry unwind | 2024-07-16 | 2024-08-05 | Sharp degrossing across funds |
| Tariff shock | 2025-04-02 | 2025-04-08 | Fastest drop since 2020 |
| Multi-manager drawdown | 2026-02-27 | 2026-03-31 | Pod shops' worst month in years; crowding blamed |

Return = adjusted close on the last trading day on or before the end date, divided by
adjusted close on the last trading day on or before the start date, minus 1
(dividends included).

## Holdings used (formation date)

- **Primary:** the last quarter end on or before the event start. This measures the
  positions funds actually had going into the sell-off.
- **Secondary:** the last quarter end at least 45 days before the event start, the
  latest holdings the public could have seen. This answers "could you have known".

## Sample

Stocks held by at least 2 universe funds at the formation date (ETFs excluded), whose
quarter-end price matched the 13F price, with prices at the event start and end,
a beta estimate and a size value.

## Groups

- **Crowded (H1):** top 10% of the sample by `crowding_score`. Comparison: the other 90%.
- **Crowded and hard to exit (H2):** among stocks with `days_to_exit`, those in the
  top 25% by `crowding_score` and the top 25% by `days_to_exit`. Comparison: the rest
  of that same sub-sample.

## Controls

Sector data is not available, so matching uses size and beta:

- **Size:** the stock's weight in the 13F market (all filers' combined holdings).
- **Beta:** slope of the stock's daily log returns on SPY's, over the 252 trading
  days up to the formation date (at least 120 days required).
- Stocks are put in 15 buckets: size quintile x beta tercile, computed within each
  event's sample. In each bucket with at least one crowded and one comparison stock,
  difference = average crowded return - average comparison return. The event result
  is the average of bucket differences weighted by the number of crowded stocks.

Robustness check: regression of event return on a crowded dummy, beta, log size and
12-month momentum. The dummy's coefficient is reported.

## Statistics

- 95% intervals by bootstrap: 2,000 resamples, stocks resampled within each
  bucket and group, random seed 0.
- **Main result:** the average matched difference across the six events, with its
  bootstrap interval (each resample redraws every event).
- **Ordinary quarters:** the same matched difference for every quarter-to-next-quarter
  period in the data, to show what a typical quarter looks like. Event results are
  compared with that distribution.

## Known limitations, stated in advance

- 13F shows long US stock positions only. Shorts, options and futures are invisible.
- Holdings are quarter-end snapshots; positions can change before an event.
- Stocks later delisted often have no price data and drop out, which can make
  crowded stocks look safer if failed companies were crowded.
- Beta and size do not capture everything sectors would (for example semiconductors
  falling together).
- Six events is a small number. The result is evidence, not proof.

## Changes after results

None yet.
