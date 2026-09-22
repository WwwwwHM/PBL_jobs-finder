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
    def test_markdown_preserves_answers_code_and_follow_up_parent(self) -> None:
        content = (
            "# Interview bank\n\n"
            "## 1. How does an AI agent use tools?\n\n"
            "Use a loop.\n\n```python\n## Not a question\nprint('tool')\n```\n\n"
            "#### Implementation details\nKeep the tool result.\n\n"
            "### 1.1 追问：When should the agent stop?\n\nStop on completion.\n\n"
            "## 2. How should failures be handled?\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bank.md"
            path.write_text(content, encoding="utf-8-sig")
            questions = load_question_bank(path)
        self.assertEqual(len(questions), 3)
        self.assertEqual(questions[0].question, "How does an AI agent use tools?")
        self.assertIn("## Not a question", questions[0].reference_answer)
        self.assertIn("#### Implementation details", questions[0].reference_answer)
        self.assertNotIn("Stop on completion", questions[0].reference_answer)
        self.assertEqual(questions[1].parent_question, questions[0].question)
        self.assertEqual(questions[1].reference_answer, "Stop on completion.")
        self.assertEqual(questions[2].reference_answer, "")
        self.assertEqual(questions[1].source, "bank.md")

    def test_markdown_ids_survive_answer_edits_and_renumbering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bank.md"
            path.write_text("## 1. How does retrieval work?\nOld answer.", encoding="utf-8")
            first = load_question_bank(path)
            path.write_text("## 8. How does retrieval work?\nNew answer.", encoding="utf-8")
            second = load_question_bank(path, position="检索工程师", category="检索系统")
        self.assertEqual(first[0].id, second[0].id)
        self.assertEqual(second[0].reference_answer, "New answer.")
        self.assertEqual(second[0].position, "检索工程师")

    def test_invalid_markdown_structure_is_rejected(self) -> None:
        for content, error in [
            ("# Empty bank", "不能为空"),
            ("### An orphan follow up question?", "缺少所属"),
            ("## A repeated question?\nOne\n## A repeated question?\nTwo", "重复 ID"),
        ]:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "bank.md"
                path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, error):
                    load_question_bank(path)

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

    def test_reference_answers_are_paired_filtered_and_updated(self) -> None:
        self.store.add_questions(["A legacy Java question without an answer?"])
        self.assertEqual(self.store.search_reference_answers("Java"), [])
        questions = [
            InterviewQuestion(
                id="q_java_answer", question="How does Java handle memory?",
                position="Java engineer", difficulty="medium", category="runtime",
                reference_answer="The garbage collector reclaims unused objects.",
                source="bank.md",
            ),
            InterviewQuestion(
                id="q_ai_answer", question="How does AI retrieval work?",
                position="AI engineer", difficulty="medium", category="retrieval",
                reference_answer="Retrieve relevant context before generation.",
            ),
        ]
        self.store.add_question_bank(questions)
        results = self.store.search_reference_answers("Java memory", top_k=1)
        self.assertEqual(results[0]["question"], questions[0].question)
        self.assertEqual(results[0]["reference_answer"], questions[0].reference_answer)
        self.assertEqual(results[0]["source"], "bank.md")
        self.assertIn(self.store.search("Java memory", top_k=1)[0], {
            "A legacy Java question without an answer?", questions[0].question,
        })
        questions[0] = questions[0].model_copy(update={"reference_answer": "Updated answer."})
        self.store.add_question_bank(questions)
        self.assertEqual(self.store.count, 3)
        self.assertEqual(
            self.store.search_reference_answers("Java memory", top_k=1)[0]["reference_answer"],
            "Updated answer.",
        )
        questions[0] = questions[0].model_copy(update={"reference_answer": ""})
        self.store.add_question_bank(questions)
        self.assertEqual(len(self.store.search_reference_answers("AI", top_k=5)), 1)
        with self.assertRaises(ValueError):
            self.store.search_reference_answers(" ")
        with self.assertRaises(ValueError):
            self.store.search_reference_answers("AI", top_k=0)

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
