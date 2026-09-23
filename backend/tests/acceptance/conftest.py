"""Fixtures for acceptance tests (trader-tester).

Stored-data tests read the real series under <repo>/data/candles directly, because the suite-wide
conftest points DATA_DIR at a temp dir. They skip when the data isn't there (e.g. a fresh clone).
Live-API tests are marked `network` and skip when nothing listens on 127.0.0.1:8000.
"""

from __future__ import annotations

import pytest
from acceptance_helpers import API_BASE, read_real


@pytest.fixture
def real_candles():
    def load(instrument_id: str, tf: str):
        df = read_real(instrument_id, tf)
        if df is None or df.empty:
            pytest.skip(f"no stored {tf} candles for {instrument_id}; run ingest")
        return df

    return load


@pytest.fixture
def api():
    import httpx

    client = httpx.Client(base_url=API_BASE, timeout=60)
    try:
        client.get("/api/health")
    except httpx.HTTPError:
        client.close()
        pytest.skip("candly API is not running on 127.0.0.1:8000")
    yield client
    client.close()
