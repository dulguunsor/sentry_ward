"""Test fixtures. The sample data and its expected answers live in sentryward.sample."""

from __future__ import annotations

from pathlib import Path

import pytest

from sentryward.sample import CUSIP, write_sample  # noqa: F401  (CUSIP re-exported for tests)


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    return write_sample(tmp_path / "raw")


@pytest.fixture
def universe_csv(tmp_path: Path) -> Path:
    p = tmp_path / "universe.csv"
    p.write_text(
        "label,name_pattern,style\n"
        "Citadel,CITADEL ADVISORS%,multi-manager\n"
        "Millennium,MILLENNIUM MANAGEMENT%,multi-manager\n"
        "Point72,POINT72 ASSET MANAGEMENT%,multi-manager\n"
        "Ghost Fund,NO SUCH FILER%,other\n",
        encoding="utf-8",
    )
    return p
