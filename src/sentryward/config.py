"""Paths and settings shared across the pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# SEC requires a descriptive User-Agent with contact details on every request.
# https://www.sec.gov/os/accessing-edgar-data
USER_AGENT_ENV = "SEC_USER_AGENT"

SEC_13F_INDEX_URL = (
    "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
)
SEC_13F_FILE_BASE = "https://www.sec.gov/files/structureddata/data/form-13f-data-sets/"

# Before this date filers reported VALUE in thousands of dollars; from it on, in dollars.
VALUE_UNIT_CHANGE = "2023-01-03"

# SEC fair-access limit is 10 requests/second; stay well under it.
REQUEST_PAUSE_SECONDS = 0.25


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def db(self) -> Path:
        return self.root / "ward.duckdb"

    @property
    def out(self) -> Path:
        return self.root / "out"

    def ensure(self) -> "Paths":
        for p in (self.raw, self.out):
            p.mkdir(parents=True, exist_ok=True)
        return self


def default_paths() -> Paths:
    return Paths(Path(os.environ.get("WARD_DATA", "data"))).ensure()


def user_agent() -> str:
    ua = os.environ.get(USER_AGENT_ENV, "").strip()
    if "@" not in ua:
        raise RuntimeError(
            f"Set {USER_AGENT_ENV} to a name and contact email, e.g. "
            f'export {USER_AGENT_ENV}="Jane Doe jane@example.com". '
            "SEC blocks requests without one."
        )
    return ua
