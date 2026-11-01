"""Download SEC Form 13F bulk data sets and load them into DuckDB.

SEC publishes each period as a zip of tab-separated tables:
SUBMISSION, COVERPAGE, INFOTABLE (plus a few others we do not need).
Older files are named like ``2023q1_form13f.zip``; newer ones cover a filing
window, like ``01jun2025-31aug2025_form13f.zip``. We never rely on the file
name for the report quarter: cleaning reads PERIODOFREPORT from the data.
"""

from __future__ import annotations

import re
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import duckdb

from . import config

ZIP_PATTERN = re.compile(r'href="([^"]*?_form13f\.zip)"', re.IGNORECASE)
TABLES = ("SUBMISSION", "COVERPAGE", "INFOTABLE")


# ---------------------------------------------------------------- discovery


def find_zip_links(index_html: str, base_url: str = config.SEC_13F_INDEX_URL) -> list[str]:
    """Return absolute URLs of every 13F data set zip linked from the SEC page."""
    seen: dict[str, None] = {}
    for href in ZIP_PATTERN.findall(index_html):
        seen.setdefault(urljoin(base_url, href), None)
    return list(seen)


_QTR = re.compile(r"(\d{4})q([1-4])_form13f", re.I)
_RANGE = re.compile(r"\d{2}[a-z]{3}(\d{4})-\d{2}[a-z]{3}(\d{4})_form13f", re.I)


def file_year(url_or_name: str) -> int | None:
    """Best-effort year of a data set file, used only to skip old downloads."""
    name = url_or_name.rsplit("/", 1)[-1]
    if m := _QTR.search(name):
        return int(m.group(1))
    if m := _RANGE.search(name):
        return int(m.group(2))
    return None


# ---------------------------------------------------------------- download


def download(
    paths: config.Paths,
    since_year: int,
    session=None,
    index_url: str = config.SEC_13F_INDEX_URL,
) -> list[Path]:
    """Download every data set zip from ``since_year`` on. Skips files already on disk."""
    import requests

    s = session or requests.Session()
    s.headers.update({"User-Agent": config.user_agent(), "Accept-Encoding": "gzip, deflate"})

    resp = s.get(index_url, timeout=60)
    resp.raise_for_status()
    links = [u for u in find_zip_links(resp.text, index_url) if (file_year(u) or 0) >= since_year]
    if not links:
        raise RuntimeError(f"No 13F zip links found on {index_url}; the page layout may have changed.")

    saved: list[Path] = []
    for url in links:
        dest = paths.raw / url.rsplit("/", 1)[-1]
        if dest.exists() and zipfile.is_zipfile(dest):
            saved.append(dest)
            continue
        time.sleep(config.REQUEST_PAUSE_SECONDS)
        with s.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
            tmp.replace(dest)
        saved.append(dest)
    return saved


# ---------------------------------------------------------------- load


def _create_raw_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS loaded_files (source_file VARCHAR PRIMARY KEY, loaded_at TIMESTAMP)").fetchall()


def _member_for(zf: zipfile.ZipFile, table: str) -> str:
    for name in zf.namelist():
        stem = Path(name).stem.upper()
        if stem == table and name.lower().endswith((".tsv", ".txt")):
            return name
    raise FileNotFoundError(f"{table}.tsv not found in {zf.filename}")


def load_zip(con: duckdb.DuckDBPyConnection, zip_path: Path, force: bool = False) -> bool:
    """Load one data set zip into raw_* tables. Returns False if already loaded."""
    _create_raw_tables(con)
    source = zip_path.name
    if not force and con.execute(
        "SELECT 1 FROM loaded_files WHERE source_file = ?", [source]
    ).fetchall()[:1]:
        return False

    with zipfile.ZipFile(zip_path) as zf, tempfile.TemporaryDirectory() as tmp:
        for table in TABLES:
            member = _member_for(zf, table)
            extracted = Path(zf.extract(member, tmp))
            raw = f"raw_{table.lower()}"
            # Read every column as text: SEC files have free-text names with stray
            # quotes, so quoting is disabled and types are set during cleaning.
            rel = con.read_csv(
                str(extracted),
                delimiter="\t",
                header=True,
                all_varchar=True,
                quotechar="",
                escapechar="",
                null_padding=True,
            )
            con.register("incoming", rel)
            exists = con.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [raw]
            ).fetchall()[:1]
            if not exists:
                con.execute(f"CREATE TABLE {raw} AS SELECT *, ?::VARCHAR AS source_file FROM incoming LIMIT 0", [source]).fetchall()
            # Newer files add columns (e.g. FIGI). Widen the table instead of failing.
            have = {r[0] for r in con.execute(f"DESCRIBE {raw}").fetchall()}
            for col in rel.columns:
                if col not in have:
                    con.execute(f'ALTER TABLE {raw} ADD COLUMN "{col}" VARCHAR').fetchall()
            con.execute(f"DELETE FROM {raw} WHERE source_file = ?", [source]).fetchall()
            con.execute(f"INSERT INTO {raw} BY NAME SELECT *, ?::VARCHAR AS source_file FROM incoming", [source]).fetchall()
            con.unregister("incoming")

    con.execute("INSERT OR REPLACE INTO loaded_files VALUES (?, now())", [source]).fetchall()
    return True


def load_all(con: duckdb.DuckDBPyConnection, zips: Iterable[Path], force: bool = False) -> list[str]:
    return [z.name for z in sorted(zips) if load_zip(con, z, force=force)]
