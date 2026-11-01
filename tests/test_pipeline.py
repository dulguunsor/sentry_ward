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
    assert "5" not in set(matched.cik)  # Vanguard is not in the universe
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


def test_pairs_with_nothing_shared_count_as_zero(built):
    con, _ = built
    # In 2022 only Citadel and Millennium filed, both holding only AAA -> overlap 1.
    row = con.execute(
        "SELECT overlap FROM fund_overlap WHERE period = DATE '2022-09-30'"
    ).fetchall()[0]
    assert row[0] == pytest.approx(1.0)


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
    assert rows[1][5] == pytest.approx(0.0)


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
             (?, DATE '2026-06-30', 100.0, 10.0),
             (?, DATE '2026-06-30', 1000.0, 100.0)
           ) t(cusip, period, shares_outstanding, adv_shares)""",
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


# ------------------------------------------------------------ end to end


def test_cli_end_to_end(raw_dir, universe_csv, tmp_path, monkeypatch, capsys):
    data = tmp_path
    monkeypatch.setenv("WARD_DATA", str(data))
    main(["load"])
    main(["build", "--universe", str(universe_csv)])
    main(["report", "--period", "2026-06-30", "--top", "5"])
    out = capsys.readouterr().out
    assert "Ghost Fund" in out
    assert (data / "out" / "dashboard_2026-06-30.json").exists()
    assert (data / "out" / "overlap_matrix_2026-06-30.csv").exists()
