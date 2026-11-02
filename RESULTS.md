# Stress test results

Run on 2026-11-02 with the design in [PREREGISTRATION.md](PREREGISTRATION.md), exactly as
written. Data: SEC 13F holdings of 24 hedge funds, 2019 to Q2 2026; prices from Yahoo
Finance; 98% of the funds' latest stock value had usable prices.

"Difference" is the crowded group's event return minus that of matched stocks in the
same size x beta bucket. Negative means crowded stocks fell more. Intervals are 95%
bootstrap intervals (2,000 resamples, seed 0).

## Summary

| Test | Holdings used | Average difference | 95% interval | Events negative |
|---|---|---|---|---|
| H1 crowded (top 10%) | Last quarter end before event | +0.5% | -0.2% to +1.2% | 2 of 6 |
| H1 crowded (top 10%) | Public 45+ days before event | -0.2% | -1.0% to +0.6% | 4 of 6 |
| H2 crowded and hard to exit | Last quarter end before event | +0.7% | -0.1% to +1.5% | 1 of 6 |
| H2 crowded and hard to exit | Public 45+ days before event | +0.6% | -0.3% to +1.4% | 1 of 6 |

**Neither hypothesis is supported.** Crowded stocks, as measured from 13F long
positions, did not fall significantly more than comparable stocks in these sell-offs.

## By event, H1 (primary: last quarter end before event)

| Event | Holdings | Stocks | Crowded | Crowded return | Matched return | Difference | 95% interval |
|---|---|---|---|---|---|---|---|
| COVID crash | 2019-12-31 | 2,259 | 226 | -39.1% | -39.7% | +0.6% | -1.8% to +2.8% |
| GameStop-week degrossing | 2020-12-31 | 2,483 | 249 | -4.1% | -4.2% | +0.1% | -0.5% to +0.7% |
| 2022 rate shock | 2021-12-31 | 2,892 | 290 | -20.9% | -23.8% | +2.9% | -0.5% to +6.4% |
| Yen carry unwind | 2024-06-30 | 3,374 | 338 | -7.5% | -7.1% | -0.4% | -1.6% to +0.8% |
| Tariff shock | 2025-03-31 | 3,609 | 361 | -13.2% | -14.0% | +0.8% | +0.1% to +1.4% |
| Multi-manager drawdown | 2025-12-31 | 3,986 | 399 | -6.0% | -5.0% | -1.1% | -2.2% to +0.1% |

## By event, H1 (secondary: holdings public 45+ days before event)

| Event | Holdings | Difference | 95% interval |
|---|---|---|---|
| COVID crash | 2019-12-31 | +0.6% | -1.8% to +2.8% |
| GameStop-week degrossing | 2020-09-30 | -0.4% | -1.0% to +0.2% |
| 2022 rate shock | 2021-09-30 | -0.5% | -4.3% to +3.3% |
| Yen carry unwind | 2024-03-31 | -0.3% | -1.5% to +0.9% |
| Tariff shock | 2024-12-31 | +0.2% | -0.6% to +0.9% |
| Multi-manager drawdown | 2025-12-31 | -1.1% | -2.2% to +0.1% |

## By event, H2 (crowded and hard to exit)

| Event | Primary difference | 95% interval | Public-holdings difference | 95% interval |
|---|---|---|---|---|
| COVID crash | +1.4% | -1.1% to +4.0% | +1.4% | -1.1% to +4.0% |
| GameStop-week degrossing | -0.0% | -0.9% to +1.0% | +0.1% | -0.8% to +0.8% |
| 2022 rate shock | +0.7% | -2.8% to +4.0% | -0.3% | -4.2% to +3.6% |
| Yen carry unwind | +1.5% | +0.3% to +2.8% | +1.0% | -0.2% to +2.1% |
| Tariff shock | +0.0% | -0.8% to +0.9% | +0.9% | +0.1% to +1.6% |
| Multi-manager drawdown | +0.5% | -0.7% to +1.7% | +0.5% | -0.8% to +1.7% |

## Ordinary quarters

Over 29 quarter-to-quarter periods, crowded stocks returned on average +1.4% more than
matched peers (spread 2.0 points, range -3.9% to +4.8%). This is not comparable with the
event results (quarters vs days or weeks), and it uses quarter-end holdings that were
not public until 45 days later.

## Observations (not pre-registered; ideas for later tests)

- **March 2026 is the one event leaning toward the hypothesis.** It is also the event
  most directly blamed on crowding. Crowded stocks fell 1.1% more than matched peers
  (regression: -1.3%), but the interval touches zero.
- **Crowded stocks look safer with more recent holdings.** In the 2022 rate shock the
  difference is +2.9% with December 2021 holdings and -0.5% with September 2021
  holdings. One explanation: funds rotated toward defensive names before the sell-off,
  and the later snapshot captures that.
- **Possible reasons for the null result:** 13F shows only long positions, while pod
  shops degross by also buying back shorts; the most crowded longs are mostly large,
  liquid stocks; quarter-end snapshots can be weeks old by the time of an event.

## Changes after results

None. The design was run as written.
