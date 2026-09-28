"""Integration checks against an isolated local Redis process when installed."""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from redis import Redis
from redis.exceptions import ConnectionError

from pbl_jobs_finder.exceptions import QuotaExceededError, StateStoreError
from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.modules.auth import AuthService
from pbl_jobs_finder.modules.quota import QuotaService
from pbl_jobs_finder.modules.redis_state import RedisState


class RedisFailureTests(unittest.TestCase):
    def test_outage_is_a_stable_application_error(self):
        client = Mock()
        client.get.side_effect = ConnectionError("unavailable")
        state = RedisState(client)
        with self.assertRaises(StateStoreError):
            state.token_phone("test-token")
        quota = QuotaService(state=state, token_verifier=lambda _: {"phone": "13800138000"})
        client.eval.side_effect = [1, ConnectionError("refund unavailable")]
        with (
            self.assertLogs("pbl_jobs_finder.modules.quota", level="ERROR") as logs,
            self.assertRaisesRegex(RuntimeError, "original AI failure"),
            quota.operation("token"),
        ):
            raise RuntimeError("original AI failure")
        self.assertIn("quota.refund", logs.output[0])


@unittest.skipUnless(shutil.which("redis-server"), "redis-server is not installed")
class RedisIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.process = subprocess.Popen(
            [shutil.which("redis-server"), "--bind", "127.0.0.1", "--port", str(cls.port),
             "--save", "", "--appendonly", "yes", "--appendfsync", "always", "--dir", cls.directory.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        cls.client = Redis(host="127.0.0.1", port=cls.port, decode_responses=True,
                           socket_connect_timeout=1, socket_timeout=1)
        try:
            for _ in range(100):
                try:
                    if cls.client.ping():
                        return
                except ConnectionError:
                    time.sleep(0.05)
            raise RuntimeError("isolated Redis did not start")
        except BaseException:
            cls.process.terminate()
            cls.process.wait(timeout=10)
            cls.client.close()
            cls.directory.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.directory.cleanup()

    def setUp(self):
        self.state = RedisState(self.client, f"test:{uuid4().hex}:")

    def test_real_redis_restart_aof_and_application_process_restart(self):
        phone = "13800138000"
        self.state.send_code(phone, "123456")
        token = self.state.issue_token(phone, "persistent-test-token")
        self.state.reserve_quota(phone, "2026-09-28", 10)
        self.assertGreater(self.client.ttl(self.state._key("code", phone)), 290)
        self.assertLessEqual(self.client.ttl(self.state._key("code", phone)), 300)
        self.assertGreater(self.client.ttl(self.state._key("cooldown", phone)), 50)
        self.assertGreater(self.client.ttl(self.state._key("token", token)), 604790)
        # AOF fsync=always makes these acknowledged writes durable before kill.
        self.process.kill()
        self.process.wait(timeout=10)
        with self.assertRaises(StateStoreError):
            self.state.token_phone(token)
        cls = type(self)
        cls.process = subprocess.Popen(
            [shutil.which("redis-server"), "--bind", "127.0.0.1", "--port", str(self.port),
             "--save", "", "--appendonly", "yes", "--appendfsync", "always",
             "--dir", self.directory.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        for _ in range(100):
            try:
                if self.client.ping():
                    break
            except ConnectionError:
                time.sleep(0.05)
        self.assertEqual(self.state.token_phone(token), phone)
        self.assertEqual(self.state.quota_used(phone, "2026-09-28"), 1)
        self.assertEqual(self.state.consume_code(phone, "123456"), 1)
        environment = dict(os.environ, STATE_BACKEND="redis",
                           REDIS_URL=f"redis://127.0.0.1:{self.port}/0", REDIS_PREFIX=self.state.prefix)
        script = """
from pbl_jobs_finder.modules.redis_state import configured_redis_state
state = configured_redis_state()
assert state.token_phone('persistent-test-token') == '13800138000'
assert state.quota_used('13800138000', '2026-09-28') == 1
assert state.consume_code('13800138000', '123456') == 0
"""
        for _ in range(2):
            subprocess.run([sys.executable, "-c", script], env=environment, check=True,
                           capture_output=True, timeout=20)

    def test_token_expiry_and_concurrent_issue(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            tokens = list(executor.map(
                lambda i: self.state.issue_token("13800138000", f"candidate-{i}"), range(20)))
        self.assertEqual(len(set(tokens)), 1)
        self.client.pexpire(self.state._key("token", tokens[0]), 0)
        self.client.pexpire(self.state._key("phone", "13800138000"), 0)
        self.assertIsNone(self.state.token_phone(tokens[0]))
        self.assertEqual(self.state.issue_token("13800138000", "fresh"), "fresh")

    def test_code_cooldown_attempt_limit_expiry_and_single_use(self):
        self.assertTrue(self.state.send_code("13800138000", "123456"))
        self.assertFalse(self.state.send_code("13800138000", "654321"))
        for _ in range(5):
            self.assertEqual(self.state.consume_code("13800138000", "111111"), -1)
        self.assertEqual(self.state.consume_code("13800138000", "123456"), 0)
        self.assertTrue(self.state.send_code("13900139000", "123456"))
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(
                lambda _: self.state.consume_code("13900139000", "123456"), range(8)))
        self.assertEqual(results.count(1), 1)
        self.assertTrue(self.state.send_code("13700137000", "123456"))
        self.client.pexpire(self.state._key("code", "13700137000"), 0)
        self.assertEqual(self.state.consume_code("13700137000", "123456"), 0)

    def test_token_survives_service_restart_and_is_revoked_across_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"sqlite:///{Path(directory).as_posix()}/auth.db")
            try:
                first = AuthService(db, state=self.state)
                second = AuthService(db, state=RedisState(self.client, self.state.prefix))
                with redirect_stdout(io.StringIO()) as output:
                    self.assertTrue(first.send_verification_code("13800138000").success)
                code = output.getvalue().strip().split(": ")[-1]
                token = first.verify_login("13800138000", code).data
                self.assertEqual(second.verify_token(token)["phone"], "13800138000")
                self.assertEqual(self.state.issue_token("13800138000", "unused"), token)
                self.assertTrue(second.revoke_token(token))
                self.assertIsNone(first.verify_token(token))
            finally:
                db.dispose()

    def test_quota_concurrency_restart_failure_refund_and_midnight(self):
        now = datetime(2026, 9, 23, 15, 59, tzinfo=UTC)
        def verifier(token):
            return {"phone": "13800138000"}

        quota = QuotaService(state=self.state, daily_limit=3, token_verifier=verifier,
                             clock=lambda: now)
        other = QuotaService(state=RedisState(self.client, self.state.prefix),
                             daily_limit=3, token_verifier=verifier, clock=lambda: now)

        def consume(index):
            try:
                with (quota if index % 2 else other).operation("token"):
                    return True
            except QuotaExceededError:
                return False

        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(sum(executor.map(consume, range(20))), 3)
        self.assertEqual(other.status("token").used, 3)
        now += timedelta(days=1)
        with (
            self.assertRaisesRegex(RuntimeError, "AI timeout"),
            quota.operation("token"),
        ):
            raise RuntimeError("AI timeout")
        self.assertEqual(other.status("token").used, 0)
        with (
            self.assertRaisesRegex(RuntimeError, "late failure"),
            quota.operation("token"),
        ):
            now += timedelta(minutes=2)
            with other.operation("token"):
                pass
            raise RuntimeError("late failure")
        self.assertEqual(other.status("token").used, 1)

    def test_login_diagnosis_interview_report_history_and_logout(self):
        from pbl_jobs_finder.modules.history import HistoryService
        from pbl_jobs_finder.modules.interview_agent import InterviewService
        from pbl_jobs_finder.modules.resume_diagnosis import ResumeDiagnosisService
        from tests.test_interview_agent import (
            FakeChatClient,
            FakeVectorStore,
            InterviewServiceTests,
        )
        from tests.test_resume_diagnosis import _model_response

        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"sqlite:///{Path(directory).as_posix()}/flow.db")
            try:
                auth = AuthService(db, state=self.state)
                with redirect_stdout(io.StringIO()) as output:
                    auth.send_verification_code("13800138000")
                token = auth.verify_login("13800138000", output.getvalue().strip().split(": ")[-1]).data
                quota = QuotaService(state=self.state, token_verifier=auth.verify_token)
                diagnosis = ResumeDiagnosisService(
                    database_instance=db, quota=quota, token_verifier=auth.verify_token,
                    chat_client=FakeChatClient(_model_response()), enable_dimensions=False)
                result = diagnosis.diagnose(token=token, position="Python backend developer",
                                            pasted_text="Python engineer with five years of backend development experience.")
                client = FakeChatClient("请介绍你开发过的系统以及你负责的主要工作？")
                interview = InterviewService(
                    database_instance=db, quota=quota, token_verifier=auth.verify_token,
                    vector_store=FakeVectorStore(), chat_client=client)
                started = interview.start(token=token, position="Python backend developer",
                                          difficulty="beginner")
                question = started.question
                next_questions = [
                    "请说明你会如何设计消息消费的幂等机制，并处理重复消息？",
                    "你如何分析数据库的慢查询并选择合适的索引？",
                    "如果缓存和数据库发生不一致，你会如何处理？",
                    "请总结你解决复杂线上故障的方法论和复盘方式？",
                ]
                for number in range(5):
                    client.responses = [json.dumps({
                        "feedback": "回答清楚地说明了分析问题的步骤，建议补充具体的数据和验证方式。",
                        "needs_follow_up": False,
                        "next_question": next_questions[number] if number < 4 else "",
                    }, ensure_ascii=False)]
                    if number == 4:
                        client.responses.append(InterviewServiceTests._report_json())
                    outcome = interview.submit_answer(
                        token=token, session_id=started.session_id, expected_question=question,
                        answer="我会先通过监控明确问题范围，再分析日志和调用链，验证原因后修复并补充回归测试。")
                    question = outcome.next_question
                self.assertTrue(outcome.is_finished)
                self.assertIsNotNone(outcome.report)
                restarted_auth = AuthService(db, state=RedisState(self.client, self.state.prefix))
                history = HistoryService(db, token_verifier=restarted_auth.verify_token)
                self.assertEqual(history.get_resume_detail(token, result.record_id).score, 82)
                self.assertEqual(history.get_interview_detail(token, started.session_id).status,
                                 "completed")
                self.assertEqual(quota.status(token).used, 2)
                self.assertTrue(restarted_auth.revoke_token(token))
                self.assertIsNone(auth.verify_token(token))
            finally:
                db.dispose()
