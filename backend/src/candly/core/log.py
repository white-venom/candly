import logging
from logging.handlers import RotatingFileHandler

from candly.core.settings import get_settings

_configured = False


class RedactSecrets(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            message = record.getMessage()
            for secret in self._secrets:
                message = message.replace(secret, "***")
            record.msg, record.args = message, None
        return True


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    redact = RedactSecrets(settings.secret_values())
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        RotatingFileHandler(
            settings.logs_dir / "candly.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        ),
    ]
    root = logging.getLogger()
    root.setLevel(level)
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(redact)
        root.addHandler(handler)
    _configured = True
