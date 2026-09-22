"""Persistent ChromaDB storage for interview-question retrieval."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from pbl_jobs_finder.config import get_settings
from pbl_jobs_finder.modules.question_bank import InterviewQuestion
from pbl_jobs_finder.utils.embeddings import (
    MAX_EMBEDDING_BATCH_SIZE,
    EmbeddingProvider,
    create_default_embedding,
)

COLLECTION_NAME = "interview_questions"
DEFAULT_METADATA = {
    "position": "通用",
    "difficulty": "medium",
    "category": "综合",
}


class ChromaVectorStore:
    """Own question indexing and semantic search over a Chroma collection."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        *,
        persist_directory: str | Path | None = None,
        client: Any | None = None,
        collection_name: str = COLLECTION_NAME,
    ) -> None:
        if client is None:
            path = Path(persist_directory or get_settings().chroma_dir).resolve()
            path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        self._client = client
        self._embedding_provider = embedding_provider
        self._collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def count(self) -> int:
        return int(self._collection.count())

    def add_question_bank(self, questions: Sequence[InterviewQuestion]) -> int:
        if not questions:
            raise ValueError("待导入的面试题不能为空")
        return self._upsert(
            ids=[question.id for question in questions],
            documents=[question.question for question in questions],
            embedding_texts=[question.embedding_text() for question in questions],
            metadatas=[question.metadata() for question in questions],
        )

    def add_questions(
        self,
        questions: Sequence[str],
        metadatas: Sequence[Mapping[str, str | int | float | bool]] | None = None,
        *,
        ids: Sequence[str] | None = None,
    ) -> int:
        """Validate and idempotently upsert plain-text questions."""

        documents = _normalize_questions(questions)
        normalized_metadatas = _normalize_metadatas(metadatas, len(documents))
        normalized_ids = _normalize_ids(ids, documents, normalized_metadatas)
        embedding_texts = [
            _embedding_text(document, metadata)
            for document, metadata in zip(
                documents, normalized_metadatas, strict=True
            )
        ]
        return self._upsert(
            ids=normalized_ids,
            documents=documents,
            embedding_texts=embedding_texts,
            metadatas=normalized_metadatas,
        )

    def _upsert(
        self,
        *,
        ids: list[str],
        documents: list[str],
        embedding_texts: list[str],
        metadatas: list[dict[str, str | int | float | bool]],
    ) -> int:
        if len(ids) != len(set(ids)):
            raise ValueError("同一批次中不能包含重复题目 ID")
        for offset in range(0, len(documents), MAX_EMBEDDING_BATCH_SIZE):
            batch_slice = slice(offset, offset + MAX_EMBEDDING_BATCH_SIZE)
            embeddings = self._embedding_provider.get_embeddings_batch(
                embedding_texts[batch_slice]
            )
            if len(embeddings) != len(documents[batch_slice]):
                raise ValueError("Embedding 返回数量与题目数量不一致")
            self._collection.upsert(
                ids=ids[batch_slice],
                embeddings=embeddings,
                documents=documents[batch_slice],
                metadatas=metadatas[batch_slice],
            )
        return len(documents)

    def search(self, query: str, top_k: int = 5) -> list[str]:
        normalized_query = query.strip() if isinstance(query, str) else ""
        if not normalized_query:
            raise ValueError("检索文本不能为空")
        if top_k <= 0:
            raise ValueError("top_k 必须大于零")
        available = self.count
        if available == 0:
            return []
        query_embedding = self._embedding_provider.get_embedding(normalized_query)
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, available),
            include=["documents"],
        )
        nested_documents = result.get("documents") or []
        if not nested_documents:
            return []
        return list(dict.fromkeys(nested_documents[0]))

    def search_for_interview(
        self,
        *,
        position: str,
        job_description: str = "",
        resume_text: str = "",
        top_k: int = 5,
    ) -> list[str]:
        query = build_interview_query(
            position=position,
            job_description=job_description,
            resume_text=resume_text,
        )
        return self.search(query, top_k=top_k)

    def search_reference_answers(
        self, question: str, top_k: int = 3
    ) -> list[dict[str, str]]:
        """Retrieve paired questions and answers; legacy question-only rows are excluded."""
        query = question.strip() if isinstance(question, str) else ""
        if not query:
            raise ValueError("检索问题不能为空")
        if top_k <= 0:
            raise ValueError("top_k 必须大于零")
        if self.count == 0:
            return []
        result = self._collection.query(
            query_embeddings=[self._embedding_provider.get_embedding(query)],
            n_results=min(top_k, self.count),
            where={"has_reference_answer": True},
            include=["documents", "metadatas"],
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        return [
            {
                "question": document,
                "reference_answer": str(metadata["reference_answer"]),
                "source": str(metadata.get("source", "")),
                "parent_question": str(metadata.get("parent_question", "")),
            }
            for document, metadata in zip(documents, metadatas, strict=True)
            if metadata and str(metadata.get("reference_answer", "")).strip()
        ]


def build_interview_query(
    *, position: str, job_description: str = "", resume_text: str = ""
) -> str:
    normalized_position = position.strip() if isinstance(position, str) else ""
    if not normalized_position:
        raise ValueError("目标岗位不能为空")
    parts = [f"目标岗位：{normalized_position}"]
    if job_description.strip():
        parts.append(f"岗位描述：{job_description.strip()[:6000]}")
    if resume_text.strip():
        parts.append(f"候选人简历：{resume_text.strip()[:6000]}")
    return "\n".join(parts)


def create_default_vector_store(
    *, persist_directory: str | Path | None = None
) -> ChromaVectorStore:
    return ChromaVectorStore(
        create_default_embedding(), persist_directory=persist_directory
    )


def _normalize_questions(questions: Sequence[str]) -> list[str]:
    if isinstance(questions, (str, bytes)) or not questions:
        raise ValueError("待导入的面试题不能为空")
    normalized: list[str] = []
    for question in questions:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("面试题文本不能为空")
        normalized.append(question.strip())
    return normalized


def _normalize_metadatas(
    metadatas: Sequence[Mapping[str, str | int | float | bool]] | None,
    count: int,
) -> list[dict[str, str | int | float | bool]]:
    if metadatas is None:
        return [dict(DEFAULT_METADATA) for _ in range(count)]
    if len(metadatas) != count:
        raise ValueError("metadata 数量必须与题目数量一致")
    normalized = []
    for metadata in metadatas:
        merged = {**DEFAULT_METADATA, **dict(metadata)}
        if any(not isinstance(value, (str, int, float, bool)) for value in merged.values()):
            raise ValueError("metadata 仅支持字符串、数字和布尔值")
        normalized.append(merged)
    return normalized


def _normalize_ids(
    ids: Sequence[str] | None,
    documents: Sequence[str],
    metadatas: Sequence[Mapping[str, str | int | float | bool]],
) -> list[str]:
    if ids is not None:
        if len(ids) != len(documents) or any(not item.strip() for item in ids):
            raise ValueError("题目 ID 必须非空且与题目数量一致")
        return [item.strip() for item in ids]
    generated = []
    for document, metadata in zip(documents, metadatas, strict=True):
        identity = document + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        generated.append(f"q_{digest}")
    return generated


def _embedding_text(
    document: str, metadata: Mapping[str, str | int | float | bool]
) -> str:
    return (
        f"岗位：{metadata['position']}\n"
        f"分类：{metadata['category']}\n"
        f"难度：{metadata['difficulty']}\n"
        f"问题：{document}"
    )


__all__ = [
    "COLLECTION_NAME",
    "ChromaVectorStore",
    "build_interview_query",
    "create_default_vector_store",
]
