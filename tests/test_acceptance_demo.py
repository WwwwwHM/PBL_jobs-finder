"""Tests for the final acceptance runner's local orchestration."""

import tempfile
import unittest
from pathlib import Path

from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.modules.auth import AuthService
from scripts.run_acceptance_demo import _login


class AcceptanceDemoTests(unittest.TestCase):
    def test_login_reads_mock_code_without_disclosing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "acceptance.db"
            database = Database(f"sqlite:///{database_path.as_posix()}")
            try:
                auth = AuthService(database)
                token = _login(auth)
                self.assertEqual(auth.verify_token(token)["phone"], "13900000023")
            finally:
                database.dispose()


if __name__ == "__main__":
    unittest.main()
