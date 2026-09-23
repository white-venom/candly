import pytest

from candly.core.settings import Settings, get_settings

_KEY_FIELDS = [
    name
    for name in Settings.model_fields
    if name.startswith(("fyers_", "kotak_", "anthropic_", "telegram_")) and name != "fyers_redirect_uri"
]


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    yield data_dir
    get_settings.cache_clear()


@pytest.fixture
def no_keys(monkeypatch):
    # The developer's real .env holds live keys; tests must never depend on or use them.
    for name in _KEY_FIELDS:
        monkeypatch.setenv(name.upper(), "")
    monkeypatch.setenv("DATA_SOURCE", "auto")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_fyers_keys(monkeypatch, no_keys):
    monkeypatch.setenv("FYERS_APP_ID", "TESTAPP-100")
    monkeypatch.setenv("FYERS_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("FYERS_CLIENT_ID", "XY00000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
