import os
import socket

import pytest

from candly.core.settings import Settings, get_settings

# Tests must never start the background scheduler, even via a TestClient lifespan.
os.environ["SCHEDULER_ENABLED"] = "false"

_KEY_FIELDS = [
    name
    for name in Settings.model_fields
    if name.startswith(("fyers_", "kotak_", "anthropic_", "telegram_")) and name != "fyers_redirect_uri"
]
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _host(address) -> str:
    return address[0] if isinstance(address, tuple) else str(address)


def _guarded_connect(sock, address):
    if _host(address) not in _LOOPBACK:
        raise RuntimeError(f"unit tests must not use the network (tried {address!r}); mock it")
    return _real_connect(sock, address)


def _guarded_connect_ex(sock, address):
    if _host(address) not in _LOOPBACK:
        raise RuntimeError(f"unit tests must not use the network (tried {address!r}); mock it")
    return _real_connect_ex(sock, address)


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("network") is None:
        monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
        monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    yield


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    yield data_dir
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
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
