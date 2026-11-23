from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from multi_source_agg import render
from multi_source_agg.config import Settings
from multi_source_agg.models import Record

CORPUS = Path(__file__).parent.parent / "data"

ROWS = [f"r{i:02d}" for i in range(1, 11)]


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    """Rich wraps to the terminal, and pytest reports a narrow one.

    Assertions on output would otherwise depend on the width of whoever's terminal ran the suite.
    """
    monkeypatch.setattr(render, "console", Console(width=200, no_color=True))


@pytest.fixture
def corpus_settings() -> Settings:
    return Settings(data_dir=CORPUS)


@pytest.fixture
def rows() -> list[str]:
    return list(ROWS)


def claim(
    source: str,
    value: str,
    *,
    native_id: str | None = None,
    shared_key: str | None = "K-1",
    seen_at: str | None = None,
    confidence: float | None = None,
    field_name: str = "price",
) -> Record:
    return Record(
        source=source,
        native_id=native_id or f"{source.upper()}-1",
        shared_key=shared_key,
        field_name=field_name,
        value=value,
        seen_at=seen_at,
        confidence=confidence,
    )


@pytest.fixture
def timed_and_scored() -> list[Record]:
    """Three claims carrying everything every policy needs.

    The probe that started this project used exactly these numbers: latest wins picks 41.50,
    confidence and majority both pick 42.00.
    """
    return [
        claim("alpha", "42.00", seen_at="2025-06-01T10:00", confidence=0.9),
        claim("beta", "41.50", seen_at="2025-06-01T14:00", confidence=0.5),
        claim("gamma", "42.00", seen_at="2025-06-01T09:00", confidence=0.7),
    ]
