"""Small synthetic SEC 13F data sets with answers you can check by hand.

Used by the tests and by `ward sample`, so the pipeline can be tried
without downloading anything from SEC.

Two files, in the two naming styles SEC has used:

2022q4_form13f.zip  (quarter ending 30-SEP-2022, old style, VALUE in thousands)
    Citadel  AAA 5 (= $5,000), filed 14-NOV-2022
    Millennium AAA 7000 dollars, filed 03-JAN-2023 (first day of dollar units)

01jun2026-31aug2026_form13f.zip  (quarter ending 30-JUN-2026, adds a FIGI column)
    Citadel     AAA 500, BBB 300 + 200 (split rows), plus a PUT and a bond row to drop
    Point72     original AAA 100, CCC 300; restatement AAA 250, CCC 750 (replaces)
    Millennium  original AAA 400, BBB 400; new-holdings amendment adds DDD 200
    Notice filer 13F-NT (no holdings)          -> ignored
    Vanguard    AAA 9,999,999 (not in universe) -> excluded from metrics

Expected 2026 weights
    Citadel     AAA .50  BBB .50
    Millennium  AAA .40  BBB .40  DDD .20
    Point72     AAA .25  CCC .75
Expected overlaps
    Citadel-Millennium .80   Citadel-Point72 .25   Millennium-Point72 .25
    crowding index = 1.30 / 3
"""

from __future__ import annotations



import zipfile
from pathlib import Path


SUB_COLS = ["ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE", "CIK", "PERIODOFREPORT"]
COV_COLS = [
    "ACCESSION_NUMBER", "REPORTCALENDARORQUARTER", "ISAMENDMENT", "AMENDMENTNO",
    "AMENDMENTTYPE", "FILINGMANAGER_NAME", "REPORTTYPE",
]
INFO_COLS = [
    "ACCESSION_NUMBER", "INFOTABLE_SK", "NAMEOFISSUER", "TITLEOFCLASS", "CUSIP", "VALUE",
    "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL", "INVESTMENTDISCRETION", "OTHERMANAGER",
]

CUSIP = {"AAA": "000000AA1", "BBB": "000000BB2", "CCC": "000000CC3", "DDD": "000000DD4"}


def _tsv(cols, rows):
    lines = ["\t".join(cols)]
    for r in rows:
        lines.append("\t".join("" if v is None else str(v) for v in r))
    return "\n".join(lines) + "\n"


def _info(acc, sk, ticker, value, shares, kind="SH", putcall=None, figi=None):
    row = [acc, sk, f'{ticker} "CORP"', "COM", CUSIP.get(ticker, "999999ZZ9"), value, shares, kind, putcall, "SOLE", None]
    return row + ([figi] if figi is not None else [])


def write_zip(path: Path, subs, covs, infos, figi=False):
    info_cols = INFO_COLS + (["FIGI"] if figi else [])
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("SUBMISSION.tsv", _tsv(SUB_COLS, subs))
        zf.writestr("COVERPAGE.tsv", _tsv(COV_COLS, covs))
        zf.writestr("INFOTABLE.tsv", _tsv(info_cols, infos))
        zf.writestr("readme.htm", "<html></html>")


HOLD = "13F HOLDINGS REPORT"


def write_sample(raw: Path) -> Path:
    """Write the two sample data set zips into ``raw`` and return it."""
    raw.mkdir(parents=True, exist_ok=True)

    # ---- old file: quarter ending 30-SEP-2022
    write_zip(
        raw / "2022q4_form13f.zip",
        subs=[
            ["0001-22-000001", "14-NOV-2022", "13F-HR", "0000000001", "30-SEP-2022"],
            ["0003-23-000001", "03-JAN-2023", "13F-HR", "0000000003", "30-SEP-2022"],
        ],
        covs=[
            ["0001-22-000001", "30-SEP-2022", "N", None, None, "CITADEL ADVISORS LLC", HOLD],
            ["0003-23-000001", "30-SEP-2022", "N", None, None, "MILLENNIUM MANAGEMENT LLC", HOLD],
        ],
        infos=[
            _info("0001-22-000001", 1, "AAA", 5, 50),        # thousands -> $5,000
            _info("0003-23-000001", 1, "AAA", 7000, 70),     # dollars
        ],
    )

    # ---- new file: quarter ending 30-JUN-2026
    F = "BBG000000000"
    write_zip(
        raw / "01jun2026-31aug2026_form13f.zip",
        figi=True,
        subs=[
            ["0001-26-000001", "10-AUG-2026", "13F-HR", "0000000001", "30-JUN-2026"],
            ["0002-26-000001", "11-AUG-2026", "13F-HR", "0000000002", "30-JUN-2026"],
            ["0002-26-000002", "20-AUG-2026", "13F-HR/A", "0000000002", "30-JUN-2026"],
            ["0003-26-000001", "12-AUG-2026", "13F-HR", "0000000003", "30-JUN-2026"],
            ["0003-26-000002", "25-AUG-2026", "13F-HR/A", "0000000003", "30-JUN-2026"],
            ["0004-26-000001", "12-AUG-2026", "13F-NT", "0000000004", "30-JUN-2026"],
            ["0005-26-000001", "13-AUG-2026", "13F-HR", "0000000005", "30-JUN-2026"],
        ],
        covs=[
            ["0001-26-000001", "30-JUN-2026", "N", None, None, "CITADEL ADVISORS LLC", HOLD],
            ["0002-26-000001", "30-JUN-2026", "N", None, None, "POINT72 ASSET MANAGEMENT, L.P.", HOLD],
            ["0002-26-000002", "30-JUN-2026", "Y", 1, "RESTATEMENT", "POINT72 ASSET MANAGEMENT, L.P.", HOLD],
            ["0003-26-000001", "30-JUN-2026", "N", None, None, "MILLENNIUM MANAGEMENT LLC", HOLD],
            ["0003-26-000002", "30-JUN-2026", "Y", 1, "NEW HOLDINGS", "MILLENNIUM MANAGEMENT LLC", HOLD],
            ["0004-26-000001", "30-JUN-2026", "N", None, None, "SOME NOTICE FILER LLC", "13F NOTICE"],
            ["0005-26-000001", "30-JUN-2026", "N", None, None, "VANGUARD GROUP INC", HOLD],
        ],
        infos=[
            _info("0001-26-000001", 1, "AAA", 500, 5, figi=F),
            _info("0001-26-000001", 2, "BBB", 300, 3, figi=F),
            _info("0001-26-000001", 3, "BBB", 200, 2, figi=F),
            _info("0001-26-000001", 4, "AAA", 999, 9, putcall="PUT", figi=F),
            _info("0001-26-000001", 5, "CCC", 888, 8, kind="PRN", figi=F),
            _info("0002-26-000001", 1, "AAA", 100, 1, figi=F),
            _info("0002-26-000001", 2, "CCC", 300, 3, figi=F),
            _info("0002-26-000002", 1, "AAA", 250, 2, figi=F),
            _info("0002-26-000002", 2, "CCC", 750, 7, figi=F),
            _info("0003-26-000001", 1, "AAA", 400, 4, figi=F),
            _info("0003-26-000001", 2, "BBB", 400, 4, figi=F),
            _info("0003-26-000002", 1, "DDD", 200, 2, figi=F),
            _info("0005-26-000001", 1, "AAA", 9999999, 99999, figi=F),
        ],
    )
    return raw
