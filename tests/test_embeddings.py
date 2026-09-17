"""Embedding provider request, retry, and validation tests."""

from __future__ import annotations

import unittest
from typing import Any

import requests

from pbl_jobs_finder.utils.embeddings import (
    AliyunEmbedding,
    EmbeddingServiceError,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(session: FakeSession, *, retries: int = 0) -> AliyunEmbedding:
    return AliyunEmbedding(
        api_key="provider-secret",
        model="embedding-test",
        endpoint="https://embedding.example/v1/embeddings",
        dimensions=3,
        timeout_seconds=7,
        max_retries=retries,
        session=session,
        sleeper=lambda _: None,
    )


class AliyunEmbeddingTests(unittest.TestCase):
    def test_batch_request_preserves_provider_index_order(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "data": [
                        {"index": 1, "embedding": [0, 1, 0]},
                        {"index": 0, "embedding": [1, 0, 0]},
                    ]
                },
            )
        )

        vectors = make_client(session).get_embeddings_batch([" first ", "second"])

        self.assertEqual(vectors, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        call = session.calls[0]
        self.assertEqual(call["timeout"], 7)
        self.assertEqual(call["json"]["input"], ["first", "second"])
        self.assertEqual(call["json"]["dimensions"], 3)
        self.assertEqual(call["headers"]["Authorization"], "Bearer provider-secret")

    def test_input_is_rejected_before_http_request(self) -> None:
        session = FakeSession()
        client = make_client(session)

        for texts in ([], [""], ["valid"] * 21):
            with self.subTest(count=len(texts)), self.assertRaises(ValueError):
                client.get_embeddings_batch(texts)
        self.assertEqual(session.calls, [])

    def test_transient_status_is_retried_and_auth_failure_is_actionable(self) -> None:
        transient_session = FakeSession(
            FakeResponse(429, {}),
            FakeResponse(200, {"data": [{"index": 0, "embedding": [1, 2, 3]}]}),
        )
        vector = make_client(transient_session, retries=1).get_embedding("query")
        self.assertEqual(vector, [1.0, 2.0, 3.0])
        self.assertEqual(len(transient_session.calls), 2)

        auth_session = FakeSession(FakeResponse(401, {}))
        with self.assertRaisesRegex(EmbeddingServiceError, "配置无效") as context:
            make_client(auth_session).get_embedding("query")
        self.assertNotIn("provider-secret", str(context.exception))

    def test_network_and_malformed_responses_are_translated(self) -> None:
        network_session = FakeSession(requests.Timeout("secret transport detail"))
        with self.assertRaisesRegex(EmbeddingServiceError, "连接失败"):
            make_client(network_session).get_embedding("query")

        invalid_cases = [
            {"data": []},
            {"data": [{"index": 0, "embedding": [1, 2]}]},
            {"data": [{"index": 1, "embedding": [1, 2, 3]}]},
            {"unexpected": []},
        ]
        for payload in invalid_cases:
            with self.subTest(payload=payload), self.assertRaises(
                EmbeddingServiceError
            ):
                make_client(FakeSession(FakeResponse(200, payload))).get_embedding(
                    "query"
                )


if __name__ == "__main__":
    unittest.main()
