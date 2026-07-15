from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "data" / "logs"
APP_LOG_PATH = LOG_DIR / "app.log"
ERROR_LOG_PATH = LOG_DIR / "errors.log"


def configure_logging() -> None:
    """Send backend logs to the terminal and persist errors to a local file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if any(getattr(handler, "_portfolio_tracker", False) for handler in root_logger.handlers):
        return

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler._portfolio_tracker = True  # type: ignore[attr-defined]

    app_file_handler = RotatingFileHandler(
        APP_LOG_PATH,
        maxBytes=2_000_000,
        backupCount=5,
    )
    app_file_handler.setLevel(logging.INFO)
    app_file_handler.setFormatter(formatter)
    app_file_handler._portfolio_tracker = True  # type: ignore[attr-defined]

    error_file_handler = RotatingFileHandler(
        ERROR_LOG_PATH,
        maxBytes=1_000_000,
        backupCount=5,
    )
    error_file_handler.setLevel(logging.ERROR)
    error_file_handler.setFormatter(formatter)
    error_file_handler._portfolio_tracker = True  # type: ignore[attr-defined]

    root_logger.addHandler(console_handler)
    root_logger.addHandler(app_file_handler)
    root_logger.addHandler(error_file_handler)
