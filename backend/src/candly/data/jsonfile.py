"""Small JSON state files (sync progress, observed holidays), written atomically."""

import json
import logging
import os
import tempfile
from pathlib import Path

from candly.data.store import replace_file

log = logging.getLogger(__name__)


def read_json(path: Path) -> object | None:
    """The parsed file, or None when it is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s (%s)", path, exc)
        return None


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        replace_file(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
