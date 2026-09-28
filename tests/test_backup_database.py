"""WAL-aware backups and restore validation."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.backup_database import backup_sqlite


class BackupTests(unittest.TestCase):
    def test_invalid_sources_do_not_create_a_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "backup.db"
            for url in (
                "sqlite:///:memory:",
                "sqlite://",
                "postgresql://localhost/app",
            ):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    backup_sqlite(url, destination)
            with self.assertRaises(FileNotFoundError):
                backup_sqlite(f"sqlite:///{directory}/missing.db", destination)
            self.assertFalse(destination.exists())

    def test_backup_failure_removes_partial_destination_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            destination = Path(directory) / "backup.db"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE history (id INTEGER PRIMARY KEY)")
                connection.commit()
            original = source.read_bytes()
            with (
                patch(
                    "scripts.backup_database.sqlite3.connect",
                    side_effect=OSError("disk error"),
                ),
                self.assertRaisesRegex(OSError, "disk error"),
            ):
                backup_sqlite(f"sqlite:///{source.as_posix()}", destination)
            self.assertFalse(destination.exists())
            self.assertEqual(source.read_bytes(), original)

    def test_backup_contains_committed_wal_rows_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            backup = Path(directory) / "backup.db"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    "CREATE TABLE history (id INTEGER PRIMARY KEY, body TEXT)"
                )
                connection.execute("INSERT INTO history VALUES (1, 'saved history')")
                connection.commit()
                url = f"sqlite:///{source.as_posix()}"
                backup_sqlite(url, backup)
                with self.assertRaises(FileExistsError):
                    backup_sqlite(url, backup)
                with self.assertRaises(ValueError):
                    backup_sqlite(url, source)
            with closing(sqlite3.connect(backup)) as restored:
                self.assertEqual(
                    restored.execute("SELECT body FROM history").fetchone()[0],
                    "saved history",
                )
