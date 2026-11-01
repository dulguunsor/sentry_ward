"""Turn raw 13F tables into one clean holdings table.

Output tables
-------------
filings    one row per 13F-HR / 13F-HR/A submission, typed
managers   latest name per filer CIK
holdings   one row per (cik, period, cusip): long equity positions in US dollars

Rules (each one is a choice; see README "Data rules"):
1. Only holdings reports (13F-HR and 13F-HR/A). Notice-only filings have no positions.
2. Amendments. For each filer and quarter, the anchor is the latest original filing
   or restatement. "New holdings" amendments filed on or after the anchor are added
   to it. An amendment with no type is treated as a restatement, which avoids
   double counting.
3. Units. VALUE was in thousands of dollars for filings before 2023-01-03 and in
   dollars after. Everything is converted to dollars.
4. Only share positions (SSHPRNAMTTYPE = 'SH') with no PUT/CALL. Options and
   principal amounts (bonds) are dropped.
5. Rows for the same CUSIP inside one filing (split by discretion or other
   manager) are summed.
"""

from __future__ import annotations

import duckdb

from . import config


def build_filings(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE filings AS
        WITH sub AS (
            SELECT
                trim(ACCESSION_NUMBER)                         AS accession,
                ltrim(trim(CIK), '0')                          AS cik,
                upper(trim(SUBMISSIONTYPE))                    AS submission_type,
                strptime(trim(FILING_DATE), '%d-%b-%Y')::DATE  AS filing_date,
                strptime(trim(PERIODOFREPORT), '%d-%b-%Y')::DATE AS period,
                row_number() OVER (PARTITION BY trim(ACCESSION_NUMBER) ORDER BY source_file DESC) AS dup
            FROM raw_submission
        ),
        cov AS (
            SELECT
                trim(ACCESSION_NUMBER)                         AS accession,
                trim(FILINGMANAGER_NAME)                       AS manager_name,
                upper(trim(REPORTTYPE))                        AS report_type,
                upper(coalesce(trim(ISAMENDMENT), 'N')) IN ('Y', 'TRUE', '1') AS is_amendment,
                upper(trim(AMENDMENTTYPE))                     AS amendment_type,
                row_number() OVER (PARTITION BY trim(ACCESSION_NUMBER) ORDER BY source_file DESC) AS dup
            FROM raw_coverpage
        )
        SELECT
            s.accession, s.cik, s.submission_type, s.filing_date, s.period,
            c.manager_name, c.report_type,
            (c.is_amendment OR s.submission_type = '13F-HR/A') AS is_amendment,
            CASE
                WHEN NOT (c.is_amendment OR s.submission_type = '13F-HR/A') THEN 'ORIGINAL'
                WHEN c.amendment_type LIKE '%NEW HOLDING%' THEN 'NEW_HOLDINGS'
                ELSE 'RESTATEMENT'
            END AS filing_kind
        FROM sub s
        JOIN cov c USING (accession)
        WHERE s.dup = 1 AND c.dup = 1
          AND s.submission_type IN ('13F-HR', '13F-HR/A')
          AND coalesce(c.report_type, '') NOT LIKE '%NOTICE%'
        """
    ).fetchall()


def select_filings(con: duckdb.DuckDBPyConnection) -> None:
    """Pick which filings count for each (cik, period) under rule 2."""
    con.execute(
        """
        CREATE OR REPLACE TABLE selected_filings AS
        WITH anchors AS (
            SELECT cik, period, accession AS anchor_accession, filing_date AS anchor_date
            FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY cik, period ORDER BY filing_date DESC, accession DESC
                ) AS rn
                FROM filings
                WHERE filing_kind IN ('ORIGINAL', 'RESTATEMENT')
            )
            WHERE rn = 1
        )
        SELECT f.accession, f.cik, f.period, f.filing_date, f.filing_kind
        FROM filings f
        JOIN anchors a USING (cik, period)
        WHERE f.accession = a.anchor_accession
           OR (f.filing_kind = 'NEW_HOLDINGS' AND f.filing_date >= a.anchor_date)
        """
    ).fetchall()


def build_managers(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE managers AS
        SELECT cik, arg_max(manager_name, filing_date) AS manager_name,
               min(period) AS first_period, max(period) AS last_period
        FROM filings
        GROUP BY cik
        """
    ).fetchall()


def build_holdings(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE holdings AS
        WITH rows AS (
            SELECT
                sf.cik, sf.period,
                upper(trim(i.CUSIP))                  AS cusip,
                trim(i.NAMEOFISSUER)                  AS issuer,
                trim(i.TITLEOFCLASS)                  AS title_of_class,
                TRY_CAST(i.VALUE AS DOUBLE)
                  * CASE WHEN sf.filing_date < DATE '{config.VALUE_UNIT_CHANGE}' THEN 1000 ELSE 1 END
                                                      AS value_usd,
                TRY_CAST(i.SSHPRNAMT AS DOUBLE)       AS shares
            FROM raw_infotable i
            JOIN selected_filings sf ON trim(i.ACCESSION_NUMBER) = sf.accession
            WHERE upper(coalesce(trim(i.SSHPRNAMTTYPE), '')) = 'SH'
              AND coalesce(trim(i.PUTCALL), '') = ''
        )
        SELECT cik, period, cusip,
               any_value(issuer)         AS issuer,
               any_value(title_of_class) AS title_of_class,
               sum(value_usd)            AS value_usd,
               sum(shares)               AS shares
        FROM rows
        WHERE length(cusip) = 9 AND value_usd > 0
        GROUP BY cik, period, cusip
        """
    ).fetchall()


def build_all(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    build_filings(con)
    select_filings(con)
    build_managers(con)
    build_holdings(con)
    return {
        t: con.execute(f"SELECT count(*) FROM {t}").fetchall()[0][0]
        for t in ("filings", "selected_filings", "managers", "holdings")
    }
