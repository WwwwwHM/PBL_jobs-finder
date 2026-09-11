"""AI job assistant application package."""

from pbl_jobs_finder.models.database import database

__version__ = "0.1.0"


def main() -> None:
    """Initialize backend storage for the command-line entry point."""

    database.initialize()
    print(f"Backend initialized: {database.url}")
