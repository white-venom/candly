from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/candly/core/settings.py -> repo root is four levels up
REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    data_dir: Path = REPO_ROOT / "data"
    config_dir: Path = REPO_ROOT / "config"
    data_source: Literal["auto", "fyers", "yahoo"] = "auto"
    frontend_url: str = "http://localhost:5173"

    fyers_app_id: str = ""
    fyers_secret_key: SecretStr = SecretStr("")
    fyers_redirect_uri: str = "https://trade.fyers.in/api-login/redirect-uri/index.html"
    fyers_client_id: str = ""
    fyers_pin: SecretStr = SecretStr("")
    fyers_totp_secret: SecretStr = SecretStr("")

    kotak_consumer_key: SecretStr = SecretStr("")
    kotak_consumer_secret: SecretStr = SecretStr("")
    kotak_mobile: str = ""
    kotak_ucc: str = ""
    kotak_mpin: SecretStr = SecretStr("")
    kotak_totp_secret: SecretStr = SecretStr("")

    anthropic_api_key: SecretStr = SecretStr("")
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""

    @property
    def candles_dir(self) -> Path:
        return self.data_dir / "candles"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def db_dir(self) -> Path:
        return self.data_dir / "db"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def has_fyers(self) -> bool:
        return bool(self.fyers_app_id and self.fyers_secret_key.get_secret_value())

    @property
    def has_kotak(self) -> bool:
        return bool(
            self.kotak_consumer_key.get_secret_value() and self.kotak_consumer_secret.get_secret_value()
        )

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key.get_secret_value())

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token.get_secret_value() and self.telegram_chat_id)

    def resolved_data_source(self) -> Literal["fyers", "yahoo"]:
        if self.data_source == "auto":
            return "fyers" if self.has_fyers else "yahoo"
        return self.data_source

    def secret_values(self) -> list[str]:
        secrets = [
            self.fyers_secret_key, self.fyers_pin, self.fyers_totp_secret,
            self.kotak_consumer_key, self.kotak_consumer_secret, self.kotak_mpin, self.kotak_totp_secret,
            self.anthropic_api_key, self.telegram_bot_token,
        ]
        return [s.get_secret_value() for s in secrets if s.get_secret_value()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
