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


class RedactAuthQuery(logging.Filter):
    """Strips query strings from logged /api/auth/ paths (they can carry a one-time auth code)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(
                arg.split("?", 1)[0] if isinstance(arg, str) and "/api/auth/" in arg and "?" in arg else arg
                for arg in record.args
            )
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
    logging.getLogger("uvicorn.access").addFilter(RedactAuthQuery())
    _configured = True
