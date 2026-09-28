"""Create a consistent SQLite snapshot, including committed WAL transactions."""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy.engine import make_url

from pbl_jobs_finder.config import get_settings


def backup_sqlite(database_url: str, destination: Path) -> Path:
    url = make_url(database_url)
    if (
        url.get_backend_name() != "sqlite"
        or not url.database
        or url.database == ":memory:"
    ):
        raise ValueError("backup requires a file-backed SQLite database")
    source = Path(url.database).resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError("source database does not exist")
    if source == destination:
        raise ValueError("backup destination must differ from source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental replacement of an older backup.
    with destination.open("xb"):
        pass
    try:
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader,
            closing(sqlite3.connect(destination)) as writer,
        ):
            reader.backup(writer)
            if writer.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup integrity check failed")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--database-url", help="Source SQLite URL (defaults to application settings)"
    )
    args = parser.parse_args()
    path = backup_sqlite(
        args.database_url or get_settings().database_url, args.destination
    )
    print(f"SQLite backup verified: {path}")


if __name__ == "__main__":
    main()
