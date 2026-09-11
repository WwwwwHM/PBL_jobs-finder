"""Backend initialization and repository integration tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect

from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.models.repositories import (
    create_interview_session,
    create_resume_record,
    delete_user,
    get_or_create_user,
    get_recent_interview_sessions,
    get_recent_resume_records,
    get_user,
    update_interview_session,
    update_resume_record,
    update_user,
)


class BackendInitializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "test.db"
        self.database = Database(f"sqlite:///{database_path.as_posix()}")
        self.database.initialize()

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_dir.cleanup()

    def test_initialize_is_idempotent_and_creates_expected_tables(self) -> None:
        self.database.initialize()
        table_names = set(inspect(self.database.engine).get_table_names())
        self.assertEqual(
            table_names, {"users", "resume_records", "interview_sessions"}
        )

    def test_user_crud_and_cascade_delete(self) -> None:
        with self.database.session() as session:
            user = get_or_create_user(session, "13800138000")
            self.assertEqual(user.nickname, "求职者")
            update_user(session, user.phone, nickname="测试用户", total_usage=2)
            create_resume_record(
                session,
                phone=user.phone,
                original_text="Python developer",
                target_position="后端工程师",
                score=80,
                missing_keywords=["FastAPI"],
                suggestions="补充接口设计经验",
                optimized_text="使用FastAPI交付服务",
            )

        with self.database.session() as session:
            user = get_user(session, "13800138000")
            self.assertIsNotNone(user)
            self.assertEqual(user.nickname, "测试用户")
            delete_user(session, user.phone)

        with self.database.session() as session:
            self.assertIsNone(get_user(session, "13800138000"))
            self.assertEqual(get_recent_resume_records(session, "13800138000"), [])

    def test_resume_and_interview_crud(self) -> None:
        with self.database.session() as session:
            resume = create_resume_record(
                session,
                phone="13900139000",
                original_text="负责订单系统开发",
                target_position="Java后端工程师",
                score=70,
                missing_keywords=["微服务", "分布式"],
                suggestions="增加量化指标",
                optimized_text="将优化订单服务并降低延迟",
            )
            update_resume_record(session, resume.id, score=76)
            interview = create_interview_session(
                session,
                phone="13900139000",
                position="Java后端工程师",
                job_description="熟悉微服务",
            )
            update_interview_session(
                session,
                interview.id,
                status="in_progress",
                current_question="如何排查接口延迟？",
                question_rounds=1,
                conversation=[{"role": "interviewer", "content": "问题"}],
            )

        with self.database.session() as session:
            resumes = get_recent_resume_records(session, "13900139000")
            interviews = get_recent_interview_sessions(session, "13900139000")
            self.assertEqual(resumes[0].score, 76)
            self.assertEqual(
                json.loads(resumes[0].missing_keywords_json), ["微服务", "分布式"]
            )
            self.assertEqual(interviews[0].question_rounds, 1)
            self.assertEqual(
                json.loads(interviews[0].conversation_json)[0]["role"],
                "interviewer",
            )


if __name__ == "__main__":
    unittest.main()
