"""History summaries, ordering, limits, and user isolation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import frontend
from pbl_jobs_finder.exceptions import AuthenticationError
from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.models.repositories import (
    create_interview_session,
    create_resume_record,
    update_interview_session,
)
from pbl_jobs_finder.modules.history import HistoryService


class HistoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "history.db"
        self.database = Database(f"sqlite:///{database_path.as_posix()}")
        self.database.initialize()
        self.tokens = {
            "token-a": {"phone": "13800138000", "nickname": "用户A"},
            "token-b": {"phone": "13900139000", "nickname": "用户B"},
        }
        self.service = HistoryService(
            self.database,
            token_verifier=lambda token: self.tokens.get(token),
            timezone_name="Asia/Hong_Kong",
        )

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_dir.cleanup()

    def test_returns_latest_five_of_each_type_in_newest_first_order(self) -> None:
        with self.database.session() as session:
            for index in range(7):
                create_resume_record(
                    session,
                    phone="13800138000",
                    original_text=f"简历正文 {index}",
                    target_position=f"岗位 {index}",
                    score=70 + index,
                    missing_keywords=[],
                    suggestions="补充成果",
                    optimized_text=f"优化稿 {index}",
                )
                interview = create_interview_session(
                    session,
                    phone="13800138000",
                    position=f"面试岗位 {index}",
                )
                update_interview_session(
                    session,
                    interview.id,
                    status="completed" if index == 6 else "in_progress",
                    question_rounds=index,
                )

        snapshot = self.service.get_recent("token-a")

        self.assertEqual(len(snapshot.resumes), 5)
        self.assertEqual(len(snapshot.interviews), 5)
        self.assertEqual([item.score for item in snapshot.resumes], [76, 75, 74, 73, 72])
        self.assertEqual(
            [item.question_rounds for item in snapshot.interviews], [6, 5, 4, 3, 2]
        )
        self.assertEqual(snapshot.interviews[0].status, "completed")
        self.assertRegex(snapshot.resumes[0].created_at, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_records_are_isolated_by_authenticated_phone(self) -> None:
        with self.database.session() as session:
            create_resume_record(
                session,
                phone="13900139000",
                original_text="用户B的私有正文",
                target_position="用户B岗位",
                score=91,
                missing_keywords=[],
                suggestions="无",
                optimized_text="用户B优化稿",
            )
            create_interview_session(
                session,
                phone="13900139000",
                position="用户B面试",
                job_description="用户B私有JD",
                resume_text="用户B私有简历",
            )

        snapshot_a = self.service.get_recent("token-a")
        snapshot_b = self.service.get_recent("token-b")

        self.assertEqual(snapshot_a.resumes, ())
        self.assertEqual(snapshot_a.interviews, ())
        self.assertEqual(snapshot_b.resumes[0].target_position, "用户B岗位")
        self.assertEqual(snapshot_b.interviews[0].position, "用户B面试")
        self.assertFalse(hasattr(snapshot_b.resumes[0], "original_text"))
        self.assertFalse(hasattr(snapshot_b.interviews[0], "resume_text"))

    def test_invalid_token_is_rejected_before_querying_records(self) -> None:
        with self.assertRaisesRegex(AuthenticationError, "登录已失效"):
            self.service.get_recent("invalid-token")

    def test_database_summaries_flow_into_frontend_tables(self) -> None:
        with self.database.session() as session:
            create_resume_record(
                session,
                phone="13800138000",
                original_text="不会展示的简历正文",
                target_position="数据工程师",
                score=84,
                missing_keywords=[],
                suggestions="补充数据规模",
                optimized_text="不会展示的优化稿",
            )
            interview = create_interview_session(
                session,
                phone="13800138000",
                position="数据平台工程师",
            )
            update_interview_session(
                session,
                interview.id,
                status="in_progress",
                question_rounds=3,
            )

        with patch.object(frontend, "history_service", self.service):
            resume_rows, interview_rows, status = frontend.load_history_callback(
                "token-a"
            )

        self.assertEqual(resume_rows[0][1:], ["数据工程师", "84 / 100"])
        self.assertEqual(interview_rows[0][1:], ["数据平台工程师", "3 轮", "进行中"])
        self.assertNotIn("简历正文", str(resume_rows))
        self.assertIn("简历诊断 1 条", status)


if __name__ == "__main__":
    unittest.main()
