from __future__ import annotations

import duckdb
import pytest

from sentryward import clean, metrics, sec13f
from sentryward.cli import main
from conftest import CUSIP


@pytest.fixture
def con(raw_dir):
    c = duckdb.connect()
    sec13f.load_all(c, raw_dir.glob("*.zip"))
    clean.build_all(c)
    return c


def holding(con, cik, period, ticker):
    rows = con.execute(
        "SELECT value_usd, shares FROM holdings WHERE cik = ? AND period = ?::DATE AND cusip = ?",
        [cik, period, CUSIP[ticker]],
    ).fetchall()
    return rows[0] if rows else None


# ------------------------------------------------------------ discovery


def test_find_zip_links_dedupes_and_resolves():
    html = (
        '<a href="/files/structureddata/data/form-13f-data-sets/2024q1_form13f.zip">x</a>'
        '<a href="/files/structureddata/data/form-13f-data-sets/2024q1_form13f.zip">dup</a>'
        '<a href="https://www.sec.gov/files/x/01jun2025-31aug2025_form13f.zip">y</a>'
        '<a href="/other.pdf">z</a>'
    )
    links = sec13f.find_zip_links(html, "https://www.sec.gov/data-research/page")
    assert links == [
        "https://www.sec.gov/files/structureddata/data/form-13f-data-sets/2024q1_form13f.zip",
        "https://www.sec.gov/files/x/01jun2025-31aug2025_form13f.zip",
    ]


@pytest.mark.parametrize(
    "name,year",
    [("2023q1_form13f.zip", 2023), ("01dec2025-28feb2026_form13f.zip", 2026), ("junk.zip", None)],
)
def test_file_year(name, year):
    assert sec13f.file_year(name) == year


# ------------------------------------------------------------ loading


def test_load_is_idempotent(raw_dir):
    c = duckdb.connect()
    first = sec13f.load_all(c, raw_dir.glob("*.zip"))
    again = sec13f.load_all(c, raw_dir.glob("*.zip"))
    assert len(first) == 2 and again == []
    n = c.execute("SELECT count(*) FROM raw_infotable").fetchone()[0]
    sec13f.load_all(c, raw_dir.glob("*.zip"), force=True)
    assert c.execute("SELECT count(*) FROM raw_infotable").fetchone()[0] == n


def test_newer_columns_are_added(con):
    cols = {r[0] for r in con.execute("DESCRIBE raw_infotable").fetchall()}
    assert "FIGI" in cols


def test_quotes_in_issuer_names_survive(con):
    issuer = con.execute("SELECT any_value(issuer) FROM holdings WHERE cusip = ?", [CUSIP["AAA"]]).fetchall()[0][0]
    assert issuer == 'AAA "CORP"'


# ------------------------------------------------------------ cleaning rules


def test_value_units_switch_on_2023_01_03(con):
    assert holding(con, "1", "2022-09-30", "AAA")[0] == 5000      # thousands -> dollars
    assert holding(con, "3", "2022-09-30", "AAA")[0] == 7000      # filed on the switch day


def test_options_and_bonds_dropped_split_rows_summed(con):
    assert holding(con, "1", "2026-06-30", "AAA") == (500, 5)    # PUT row of 999 excluded
    assert holding(con, "1", "2026-06-30", "BBB") == (500, 5)    # 300 + 200
    assert holding(con, "1", "2026-06-30", "CCC") is None         # PRN row excluded


def test_restatement_replaces_original(con):
    assert holding(con, "2", "2026-06-30", "AAA")[0] == 250
    assert holding(con, "2", "2026-06-30", "CCC")[0] == 750


def test_new_holdings_amendment_adds_to_original(con):
    assert holding(con, "3", "2026-06-30", "AAA")[0] == 400
    assert holding(con, "3", "2026-06-30", "DDD")[0] == 200


def test_notice_filings_ignored(con):
    assert con.execute("SELECT count(*) FROM filings WHERE cik = '4'").fetchall()[0][0] == 0


def test_cik_leading_zeros_stripped(con):
    names = dict(con.execute("SELECT cik, manager_name FROM managers").fetchall())
    assert names["1"] == "CITADEL ADVISORS LLC"


# ------------------------------------------------------------ metrics


@pytest.fixture
def built(con, universe_csv):
    matched = metrics.build_all(con, universe_csv)
    return con, matched


def test_universe_matches_names_not_others(built, universe_csv):
    con, matched = built
    assert set(matched.fund) == {"Citadel", "Millennium", "Point72"}
    assert not {"5", "6"} & set(matched.cik)  # Vanguard and Bad Units are not in the universe
    assert metrics.unmatched_patterns(con, universe_csv) == ["Ghost Fund"]


def test_weights_sum_to_one(built):
    con, _ = built
    sums = con.execute(
        "SELECT fund, period, round(sum(weight), 9) FROM fund_positions GROUP BY ALL"
    ).fetchall()
    assert all(s == 1 for *_, s in sums)


def test_overlap_by_hand(built):
    con, _ = built
    m = metrics.overlap_matrix(con, "2026-06-30")
    assert m.loc["Citadel", "Millennium"] == pytest.approx(0.80)
    assert m.loc["Citadel", "Point72"] == pytest.approx(0.25)
    assert m.loc["Millennium", "Point72"] == pytest.approx(0.25)
    assert (m.values == m.values.T).all()
    idx = con.execute(
        "SELECT crowding_index FROM crowding_index WHERE period = DATE '2026-06-30'"
    ).fetchall()[0][0]
    assert idx == pytest.approx(1.30 / 3)


def test_share_classes_merge_and_etfs_are_excluded(built):
    con, _ = built
    # Q3 2022: Citadel AAA $5k + AAA class B $5k (+ an ETF); Millennium AAA $7k (+ an ETF).
    # Classes merged and ETFs left out: both funds are 100% AAA -> overlap 1.0.
    # If classes were not merged, Citadel would be half AAA, half AAA_B -> 0.5.
    row = con.execute(
        "SELECT overlap FROM fund_overlap WHERE period = DATE '2022-09-30'"
    ).fetchall()[0]
    assert row[0] == pytest.approx(1.0)
    etfs = con.execute(
        "SELECT cusip, n_holders FROM etf_holdings WHERE period = DATE '2022-09-30' ORDER BY cusip"
    ).fetchall()
    assert etfs == [(CUSIP["ETF2"], 1), (CUSIP["ETF1"], 1)]  # two ETFs, kept apart


def test_issuer_key(con):
    metrics.create_issuer_key(con)
    q = "SELECT issuer_key(?, ?)"
    assert con.execute(q, ["02079K305", "ALPHABET INC"]).fetchall()[0][0] == "02079K"
    assert con.execute(q, ["02079K107", "ALPHABET INC"]).fetchall()[0][0] == "02079K"
    assert con.execute(q, ["464287655", "ISHARES TR"]).fetchall()[0][0] == "464287655"
    assert con.execute(q, ["81369Y803", "SELECT SECTOR SPDR TR"]).fetchall()[0][0] == "81369Y803"
    assert con.execute(q, ["78462F103", "SPDR S&P 500 ETF TR"]).fetchall()[0][0] == "78462F103"


@pytest.mark.parametrize(
    "issuer,title,expected",
    [
        ("ISHARES TR", "CORE S&P500 ETF", True),
        ("SPDR S&P 500 ETF TR", "TR UNIT", True),
        ("INVESCO QQQ TR", "UNIT SER 1", True),
        ("SELECT SECTOR SPDR TR", "TECHNOLOGY", True),
        ("VANGUARD INDEX FDS", "S&P 500 ETF SHS", True),
        ("ALPHABET INC", "CAP STK CL C", False),
        ("NORTHERN TR CORP", "COM", False),
        ("VORNADO RLTY TR", "SH BEN INT", False),
        ("VISA INC", "COM CL A", False),
    ],
)
def test_is_fund_product(con, issuer, title, expected):
    metrics.create_macros(con)
    assert con.execute("SELECT is_fund_product(?, ?)", [issuer, title]).fetchall()[0][0] is expected


def test_unit_errors_dropped(built):
    con, _ = built
    assert con.execute("SELECT count(*) FROM holdings WHERE cik = '6'").fetchall()[0][0] == 1
    assert con.execute("SELECT count(*) FROM holdings_ok WHERE cik = '6'").fetchall()[0][0] == 0


def test_market_weights_and_active_share(built):
    con, _ = built
    mw = dict(con.execute(
        "SELECT issuer_id, market_weight FROM market_weights WHERE period = DATE '2026-06-30'"
    ).fetchall())
    assert mw == pytest.approx({"11111A": 0.6, "22222B": 0.2, "33333C": 0.1, "44444D": 0.1})
    act = dict(con.execute(
        "SELECT fund, active_share FROM fund_stats WHERE period = DATE '2026-06-30'"
    ).fetchall())
    assert act == pytest.approx({"Citadel": 0.30, "Millennium": 0.30, "Point72": 0.65})


def test_active_overlap_by_hand(built):
    con, _ = built
    m = metrics.overlap_matrix(con, "2026-06-30", column="active_overlap")
    assert m.loc["Citadel", "Millennium"] == pytest.approx(0.50)
    assert m.loc["Citadel", "Point72"] == pytest.approx(0.0)
    assert m.loc["Millennium", "Point72"] == pytest.approx(0.0)


def test_index_change_uses_only_pairs_in_both_quarters(con):
    con.execute(
        """CREATE TABLE fund_overlap AS SELECT * FROM (VALUES
             (DATE '2026-03-31', 'A', 'B', 0.30, 0.10, 5),
             (DATE '2026-03-31', 'A', 'C', 0.10, 0.00, 1),
             (DATE '2026-03-31', 'B', 'C', 0.10, 0.00, 1),
             (DATE '2026-06-30', 'A', 'B', 0.40, 0.20, 5)
           ) t(period, fund_a, fund_b, overlap, active_overlap, shared_stocks)"""
    ).fetchall()
    metrics.build_crowding_index(con)
    rows = dict(
        (str(r[0]), r[1:]) for r in con.execute(
            "SELECT period, crowding_index, change_same_funds, n_pairs_compared FROM crowding_index"
        ).fetchall()
    )
    # Fund C left. The plain average jumps from 0.167 to 0.40, but the
    # like-for-like change is only A-B: 0.40 - 0.30 = 0.10.
    assert rows["2026-06-30"] == (pytest.approx(0.40), pytest.approx(0.10), 1)
    assert rows["2026-03-31"][1] is None


def test_stock_crowding(built):
    con, _ = built
    rows = con.execute(
        """SELECT cusip, n_holders, n_funds, holder_share, sum_weight, crowding_score
           FROM stock_crowding WHERE period = DATE '2026-06-30' ORDER BY crowding_score DESC"""
    ).fetchall()
    # Only stocks with >= 2 universe holders: AAA (3) and BBB (2).
    assert [r[0] for r in rows] == [CUSIP["AAA"], CUSIP["BBB"]]
    aaa = rows[0]
    assert aaa[1:4] == (3, 3, pytest.approx(1.0))
    assert aaa[4] == pytest.approx(0.50 + 0.40 + 0.25)
    assert aaa[5] == pytest.approx(100.0)
    # BBB: holders 2/3 of the top stock's, weight 0.90/1.15 of the top stock's
    assert rows[1][5] == pytest.approx(100 * (2 / 3 + 0.90 / 1.15) / 2)


def test_rank_and_report_helpers(built):
    con, _ = built
    ranks = con.execute(
        "SELECT crowding_rank FROM stock_crowding WHERE period = DATE '2026-06-30' ORDER BY crowding_rank"
    ).fetchall()
    assert [r[0] for r in ranks] == [1, 2]
    assert metrics.missing_funds(con, "2022-09-30") == ["Point72"]
    assert metrics.missing_funds(con, "2026-06-30") == []
    pairs = metrics.top_pairs(con, "2026-06-30")
    assert list(pairs.iloc[0][["fund_a", "fund_b"]]) == ["Citadel", "Millennium"]


def test_score_change_only_vs_previous_quarter(built):
    con, _ = built
    # AAA was also scored in Q3 2022, but that is not the quarter before Q2 2026.
    change = con.execute(
        "SELECT score_change FROM stock_crowding WHERE period = DATE '2026-06-30' AND cusip = ?",
        [CUSIP["AAA"]],
    ).fetchall()[0][0]
    assert change is None


def test_market_data_adds_ownership_and_days_to_exit(con, universe_csv):
    con.execute(
        """CREATE TABLE market AS SELECT * FROM (VALUES
             (?, DATE '2026-06-30', 'AAA', 100.0, 100.0, 10.0, 0.02, true),
             (?, DATE '2026-06-30', 'BBB', 100.0, 1000.0, 100.0, 0.02, false)
           ) t(cusip, period, ticker, close, shares_outstanding, adv_shares, daily_vol, price_match)""",
        [CUSIP["AAA"], CUSIP["BBB"]],
    ).fetchall()
    metrics.build_all(con, universe_csv)
    aaa = con.execute(
        "SELECT total_shares, hf_ownership, days_to_exit FROM stock_crowding "
        "WHERE period = DATE '2026-06-30' AND cusip = ?",
        [CUSIP["AAA"]],
    ).fetchall()[0]
    # Citadel 5 + Point72 2 + Millennium 4 = 11 shares
    assert aaa == (11, pytest.approx(0.11), pytest.approx(11 / 2.0))
    # BBB's price did not match the 13F price, so its market data is not used.
    bbb = con.execute(
        "SELECT hf_ownership FROM stock_crowding WHERE period = DATE '2026-06-30' AND cusip = ?",
        [CUSIP["BBB"]],
    ).fetchall()[0][0]
    assert bbb is None


# ------------------------------------------------------------ end to end


def test_cli_end_to_end(raw_dir, universe_csv, tmp_path, monkeypatch, capsys):
    data = tmp_path
    monkeypatch.setenv("WARD_DATA", str(data))
    main(["load"])
    main(["build", "--universe", str(universe_csv)])
    main(["report", "--period", "2026-06-30", "--top", "5"])
    out = capsys.readouterr().out
    assert "Ghost Fund" in out
    import json

    dash = json.loads((data / "out" / "dashboard_2026-06-30.json").read_text())  # strict JSON
    assert dash["top_pairs"][0]["active_overlap"] == pytest.approx(0.5)
    assert dash["crowding_index"][0]["active_index"] is None  # 2022 has no active bets
    assert (data / "out" / "overlap_matrix_2026-06-30.csv").exists()


def test_unit_fix_rescales_whole_filings_and_drops_stray_rows():
    c = duckdb.connect()
    # Three filers report prices near $100. Filer 4 reported its whole filing in
    # thousands after the switch (prices look 1000x too small). Filer 5 has one
    # stray row that is 50x off. Filer 6 holds only one comparable stock.
    rows = []
    for cik in ("1", "2", "3"):
        for cusip, px in (("AAAAAA101", 100), ("BBBBBB102", 50), ("CCCCCC103", 20)):
            rows.append((cik, cusip, px * 10.0, 10.0))
    rows += [("4", "AAAAAA101", 1.0, 10.0), ("4", "BBBBBB102", 0.5, 10.0), ("4", "CCCCCC103", 0.2, 10.0)]
    rows += [("5", "AAAAAA101", 1000.0, 10.0), ("5", "BBBBBB102", 25000.0, 10.0), ("5", "CCCCCC103", 200.0, 10.0)]
    rows += [("6", "AAAAAA101", 1.0, 10.0)]
    c.execute(
        "CREATE TABLE holdings (cik VARCHAR, period DATE, cusip VARCHAR, issuer VARCHAR, "
        "title_of_class VARCHAR, value_usd DOUBLE, shares DOUBLE)"
    ).fetchall()
    c.executemany(
        "INSERT INTO holdings VALUES (?, DATE '2026-06-30', ?, 'X INC', 'COM', ?, ?)", rows
    )
    checks = metrics.build_holdings_ok(c)
    got = {(r[0], r[1]): r[2] for r in c.execute("SELECT cik, cusip, value_usd FROM holdings_ok").fetchall()}
    # Filer 4 is rescaled by 1000 and kept in full.
    assert got[("4", "AAAAAA101")] == pytest.approx(1000)
    assert got[("4", "CCCCCC103")] == pytest.approx(200)
    # Filer 5 keeps its good rows; only the 50x row is dropped.
    assert ("5", "BBBBBB102") not in got and got[("5", "AAAAAA101")] == 1000
    # Filer 6 has too few comparable rows to rescale, so its 1000x-off row is dropped.
    assert ("6", "AAAAAA101") not in got
    assert checks["rescaled_filings"] == 1 and checks["rescaled_rows"] == 3
    assert checks["dropped"] == 2
