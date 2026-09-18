"""Validated client for the configured Alibaba Cloud embedding service."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import requests

from pbl_jobs_finder.config import get_settings
from pbl_jobs_finder.exceptions import (
    EmbeddingConfigurationError,
    EmbeddingServiceError,
)

MAX_EMBEDDING_BATCH_SIZE = 20


class EmbeddingProvider(Protocol):
    def get_embedding(self, text: str) -> list[float]:
        """Return one embedding vector."""

    def get_embeddings_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Return vectors in the same order as the supplied texts."""


class _HttpSession(Protocol):
    def post(self, url: str, **kwargs: Any) -> Any:
        """Submit one HTTP request."""


class AliyunEmbedding:
    """Small provider adapter with strict request and response validation."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        endpoint: str,
        dimensions: int = 1024,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        session: _HttpSession | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key.strip():
            raise EmbeddingConfigurationError("未配置 ALIYUN_API_KEY，无法生成向量")
        if not model.strip() or not endpoint.strip():
            raise EmbeddingConfigurationError("Embedding 模型或服务地址未配置")
        if dimensions <= 0 or timeout_seconds <= 0 or max_retries < 0:
            raise EmbeddingConfigurationError("Embedding 数值配置无效")
        self._api_key = api_key
        self.model = model.strip()
        self.endpoint = endpoint.strip()
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._session = session or requests.Session()
        self._sleeper = sleeper

    def get_embedding(self, text: str) -> list[float]:
        return self.get_embeddings_batch([text])[0]

    def get_embeddings_batch(self, texts: Sequence[str]) -> list[list[float]]:
        normalized = _validate_texts(texts)
        response = self._request(normalized)
        return self._parse_response(response, expected_count=len(normalized))

    def _request(self, texts: list[str]) -> Any:
        payload = {
            "model": self.model,
            "input": texts,
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(self.max_retries + 1):
            try:
                response = self._session.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                if attempt < self.max_retries:
                    self._sleeper(0.25 * (2**attempt))
                    continue
                raise EmbeddingServiceError(
                    "Embedding 服务连接失败，请稍后重试"
                ) from exc

            status_code = int(getattr(response, "status_code", 0))
            if status_code in {408, 429} or status_code >= 500:
                if attempt < self.max_retries:
                    self._sleeper(0.25 * (2**attempt))
                    continue
                raise EmbeddingServiceError("Embedding 服务繁忙，请稍后重试")
            if status_code in {401, 403}:
                raise EmbeddingServiceError("Embedding 服务配置无效，请联系管理员")
            if status_code < 200 or status_code >= 300:
                raise EmbeddingServiceError(
                    f"Embedding 服务请求失败（HTTP {status_code}）"
                )
            return response
        raise EmbeddingServiceError("Embedding 服务暂时不可用，请稍后重试")

    def _parse_response(
        self, response: Any, *, expected_count: int
    ) -> list[list[float]]:
        try:
            payload = response.json()
            items = payload["data"]
            if not isinstance(items, list):
                raise TypeError("data is not a list")
            ordered = sorted(items, key=lambda item: int(item["index"]))
            vectors = [self._validate_vector(item["embedding"]) for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingServiceError("Embedding 服务返回格式无效，请重试") from exc
        if len(vectors) != expected_count:
            raise EmbeddingServiceError("Embedding 服务返回的向量数量不一致，请重试")
        expected_indexes = list(range(expected_count))
        actual_indexes = [int(item["index"]) for item in ordered]
        if actual_indexes != expected_indexes:
            raise EmbeddingServiceError("Embedding 服务返回的向量顺序无效，请重试")
        return vectors

    def _validate_vector(self, raw_vector: Any) -> list[float]:
        if not isinstance(raw_vector, list) or len(raw_vector) != self.dimensions:
            raise EmbeddingServiceError(
                f"Embedding 向量维度异常，预期 {self.dimensions} 维"
            )
        try:
            return [float(value) for value in raw_vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingServiceError("Embedding 向量包含非法数值") from exc


def _validate_texts(texts: Sequence[str]) -> list[str]:
    if isinstance(texts, (str, bytes)):
        raise TypeError("批量 Embedding 输入必须是文本列表")
    if not texts:
        raise ValueError("Embedding 输入不能为空")
    if len(texts) > MAX_EMBEDDING_BATCH_SIZE:
        raise ValueError(
            f"单次最多处理 {MAX_EMBEDDING_BATCH_SIZE} 条 Embedding 文本"
        )
    normalized: list[str] = []
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Embedding 文本不能为空")
        normalized.append(text.strip())
    return normalized


def create_default_embedding() -> AliyunEmbedding:
    settings = get_settings()
    if not settings.aliyun_api_key:
        raise EmbeddingConfigurationError("未配置 ALIYUN_API_KEY，无法生成向量")
    return AliyunEmbedding(
        api_key=settings.aliyun_api_key,
        model=settings.aliyun_embedding_model,
        endpoint=settings.aliyun_embedding_url,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.embedding_timeout_seconds,
        max_retries=settings.embedding_max_retries,
    )


def get_embedding(text: str) -> list[float]:
    return create_default_embedding().get_embedding(text)


def get_embeddings_batch(texts: Sequence[str]) -> list[list[float]]:
    return create_default_embedding().get_embeddings_batch(texts)


__all__ = [
    "MAX_EMBEDDING_BATCH_SIZE",
    "AliyunEmbedding",
    "EmbeddingConfigurationError",
    "EmbeddingProvider",
    "EmbeddingServiceError",
    "create_default_embedding",
    "get_embedding",
    "get_embeddings_batch",
]
