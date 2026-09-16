"""AI job assistant application package."""

import logging

from pbl_jobs_finder.models.database import database
from pbl_jobs_finder.utils.logging import configure_logging, report_exception

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


def main() -> None:
    """Initialize backend storage for the command-line entry point."""

    configure_logging()
    try:
        database.initialize()
    except Exception as exc:
        report_exception(logger, "backend.initialize", exc)
        raise
    logger.info("Backend storage initialized")
    print(f"Backend initialized: {database.url}")
