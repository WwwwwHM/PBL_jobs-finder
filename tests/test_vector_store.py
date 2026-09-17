"""Question-bank validation and Chroma retrieval integration tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings
from pydantic import ValidationError

from pbl_jobs_finder.modules.question_bank import (
    InterviewQuestion,
    load_question_bank,
)
from pbl_jobs_finder.vector_store import ChromaVectorStore, build_interview_query


class KeywordEmbedding:
    """Deterministic vectors that make integration assertions reproducible."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def get_embedding(self, text: str) -> list[float]:
        return self._vector(text)

    def get_embeddings_batch(self, texts: Sequence[str]) -> list[list[float]]:
        self.batch_sizes.append(len(texts))
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.lower()
        if "java" in lowered or "spring" in lowered or "jvm" in lowered:
            return [1.0, 0.0, 0.0, 0.0]
        if "前端" in text or "react" in lowered or "typescript" in lowered:
            return [0.0, 1.0, 0.0, 0.0]
        if "ai" in lowered or "rag" in lowered or "大模型" in text:
            return [0.0, 0.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]


class QuestionBankTests(unittest.TestCase):
    def test_default_bank_is_structured_unique_and_substantial(self) -> None:
        questions = load_question_bank()

        self.assertGreaterEqual(len(questions), 40)
        self.assertEqual(len({question.id for question in questions}), len(questions))
        self.assertTrue(all(question.question for question in questions))
        self.assertTrue(all(question.position for question in questions))

    def test_schema_and_duplicate_ids_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            InterviewQuestion.model_validate(
                {
                    "id": "unsafe id",
                    "question": "too short",
                    "position": "后端",
                    "difficulty": "unknown",
                    "category": "开发",
                }
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "duplicate.json"
            item = {
                "id": "q_duplicate",
                "question": "请说明一个足够完整的测试问题和你的解决方法。",
                "position": "测试工程师",
                "difficulty": "medium",
                "category": "测试设计",
            }
            path.write_text(json.dumps([item, item], ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "重复 ID"):
                load_question_bank(path)


class ChromaVectorStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.embedding = KeywordEmbedding()
        self.client = chromadb.EphemeralClient(
            settings=ChromaSettings(anonymized_telemetry=False)
        )
        self.store = ChromaVectorStore(
            self.embedding,
            client=self.client,
            collection_name="test_interview_questions",
        )

    def tearDown(self) -> None:
        self.client.delete_collection("test_interview_questions")

    def test_bank_import_is_batched_and_idempotent(self) -> None:
        questions = load_question_bank()

        imported = self.store.add_question_bank(questions)
        self.assertEqual(imported, len(questions))
        self.assertEqual(self.embedding.batch_sizes, [20, 20, 2])
        self.assertEqual(self.store.count, len(questions))

        self.embedding.batch_sizes.clear()
        self.store.add_question_bank(questions)
        self.assertEqual(self.store.count, len(questions))
        self.assertEqual(self.embedding.batch_sizes, [20, 20, 2])

    def test_job_jd_and_resume_query_returns_top_five_related_questions(self) -> None:
        questions = load_question_bank()
        self.store.add_question_bank(questions)
        java_questions = {
            item.question for item in questions if item.position == "Java后端开发工程师"
        }

        results = self.store.search_for_interview(
            position="Java后端开发工程师",
            job_description="负责 Spring Boot 微服务和 MySQL 性能优化",
            resume_text="使用 Java、Redis 和消息队列建设订单系统",
        )

        self.assertEqual(len(results), 5)
        self.assertEqual(len(set(results)), 5)
        self.assertTrue(set(results).issubset(java_questions))

    def test_empty_collection_limits_and_stable_plain_question_ids(self) -> None:
        self.assertEqual(self.store.search("Java 后端", top_k=5), [])
        with self.assertRaises(ValueError):
            self.store.search("", top_k=5)
        with self.assertRaises(ValueError):
            self.store.search("valid", top_k=0)

        metadata = [{"position": "后端", "category": "数据库"}]
        self.store.add_questions(["如何优化一条慢 SQL 查询？"], metadata)
        self.store.add_questions(["如何优化一条慢 SQL 查询？"], metadata)
        self.assertEqual(self.store.count, 1)
        self.assertEqual(len(self.store.search("慢 SQL", top_k=5)), 1)

    def test_query_builder_requires_position_and_truncates_context(self) -> None:
        with self.assertRaisesRegex(ValueError, "目标岗位"):
            build_interview_query(position=" ")
        query = build_interview_query(
            position=" 后端工程师 ",
            job_description="J" * 7000,
            resume_text="R" * 7000,
        )
        self.assertIn("目标岗位：后端工程师", query)
        self.assertLessEqual(query.count("J"), 6000)
        self.assertLessEqual(query.count("R"), 6000)


if __name__ == "__main__":
    unittest.main()
