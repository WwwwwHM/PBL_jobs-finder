"""Resume diagnosis, persistence and Word export tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from docx import Document
from PyPDF2 import PdfWriter

from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.models.repositories import (
    get_recent_resume_records,
    get_resume_record,
)
from pbl_jobs_finder.modules.quota import QuotaService
from pbl_jobs_finder.modules.resume_diagnosis import (
    ResumeAccessError,
    ResumeDiagnosisService,
    ResumeResponseError,
    ResumeValidationError,
    diagnose_resume,
)
from pbl_jobs_finder.modules.resume_ocr import (
    MAX_OCR_PAGES,
    ResumeOCRError,
    extract_text_from_image_pdf,
)
from pbl_jobs_finder.modules.resume_parser import (
    MAX_PDF_SIZE,
    ResumeParseError,
    parse_resume_pdf,
)
from pbl_jobs_finder.utils.llm_client import LLMServiceError, ZhipuChatClient


class FakeChatClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.system_prompt = ""
        self.user_prompt = ""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return self.response


class RaisingChatClient:
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise LLMServiceError("AI 服务响应超时，请稍后重试")


class APITimeoutError(Exception):
    pass


class FakeOCRPage:
    def __init__(self) -> None:
        self.closed = False

    def render(self, *, scale: float) -> SimpleNamespace:
        return SimpleNamespace(to_pil=lambda: f"page-at-{scale}")

    def close(self) -> None:
        self.closed = True


class FakeOCRDocument:
    def __init__(self, page_count: int) -> None:
        self.pages = [FakeOCRPage() for _ in range(page_count)]
        self.closed = False

    def __len__(self) -> int:
        return len(self.pages)

    def __getitem__(self, index: int) -> FakeOCRPage:
        return self.pages[index]

    def close(self) -> None:
        self.closed = True


def _write_text_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT\n/F1 12 Tf\n72 720 Td\n({escaped}) Tj\nET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, item in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii"))
        payload.extend(item)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(payload)


def _model_response() -> str:
    return json.dumps(
        {
            "score": 82,
            "missing_keywords": ["FastAPI", "自动化测试"],
            "suggestions": ["补充接口设计细节", "用真实数据说明成果"],
            "star_examples": [
                "情境/任务：维护订单服务；行动：使用 Python 开发接口；结果：响应时间降低 [请补充真实数据]。"
            ],
            "optimized_text": (
                "# 张三\n"
                "13800138000 | zhangsan@example.com\n\n"
                "## 求职目标\n"
                "Python 后端工程师\n\n"
                "## 项目经历\n"
                "### 订单服务\n"
                "- 使用 Python 开发订单接口，响应时间降低 [请补充真实数据]。"
            ),
        },
        ensure_ascii=False,
    )


class ResumeDiagnosisTests(unittest.TestCase):
    def test_diagnosis_parses_json_and_requires_truthful_complete_rewrite(self) -> None:
        client = FakeChatClient(f"```json\n{_model_response()}\n```")
        result = diagnose_resume(
            "张三，五年 Python 后端经验，负责订单服务开发和维护。",
            "Python 后端工程师",
            client=client,
        )
        self.assertEqual(result.score, 82)
        self.assertEqual(result.missing_keywords, ["FastAPI", "自动化测试"])
        self.assertIn("- 补充接口设计细节", result.suggestions)
        self.assertIn("情境/任务", result.star_examples)
        self.assertIn("## 项目经历", result.optimized_text)
        self.assertIn("不得编造数字", client.system_prompt)
        self.assertIn("至少一个STAR改写示例", client.user_prompt)
        self.assertIn("Python 后端工程师", client.user_prompt)

    def test_invalid_model_response_is_rejected(self) -> None:
        client = FakeChatClient('{"score": 101, "suggestions": [], "optimized_text": "x"}')
        with self.assertRaises(ResumeResponseError):
            diagnose_resume(
                "这是一份包含足够长度与项目经验的测试简历内容。",
                "产品经理",
                client=client,
            )

    def test_missing_star_examples_is_rejected(self) -> None:
        response = json.loads(_model_response())
        response.pop("star_examples")
        with self.assertRaisesRegex(ResumeResponseError, "诊断内容不完整"):
            diagnose_resume(
                "这是一份包含足够长度与项目经验的测试简历内容。",
                "产品经理",
                client=FakeChatClient(json.dumps(response, ensure_ascii=False)),
            )

    def test_short_resume_is_rejected_before_model_call(self) -> None:
        client = FakeChatClient(_model_response())
        with self.assertRaises(ResumeValidationError):
            diagnose_resume("太短", "产品经理", client=client)
        self.assertEqual(client.system_prompt, "")

    def test_non_pdf_upload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "resume.txt"
            path.write_text("resume", encoding="utf-8")
            with self.assertRaisesRegex(ResumeParseError, "仅支持 PDF"):
                parse_resume_pdf(path)

    def test_real_text_pdf_is_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "resume.PDF"
            _write_text_pdf(path, "Python backend engineer with five years experience")
            result = parse_resume_pdf(path)
        self.assertIn("Python backend engineer", result)

    def test_corrupt_pdf_prompts_for_pasted_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "corrupt.pdf"
            path.write_bytes(b"%PDF incomplete")
            with self.assertRaisesRegex(ResumeParseError, "解析失败"):
                parse_resume_pdf(path)

    def test_pdf_falls_back_to_pdfplumber_and_normalizes_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "resume.pdf"
            path.write_bytes(b"%PDF placeholder")
            with (
                patch(
                    "pbl_jobs_finder.modules.resume_parser._extract_with_pypdf",
                    return_value="",
                ),
                patch(
                    "pbl_jobs_finder.modules.resume_parser._extract_with_pdfplumber",
                    return_value=" 张三\u00a0 \t Python  \n\n\n 项目经历 ",
                ) as fallback,
            ):
                result = parse_resume_pdf(path)
        fallback.assert_called_once_with(path)
        self.assertEqual(result, "张三 Python\n\n项目经历")

    def test_image_pdf_falls_back_to_local_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "image-resume.pdf"
            path.write_bytes(b"%PDF placeholder")
            with (
                patch(
                    "pbl_jobs_finder.modules.resume_parser._extract_with_pypdf",
                    return_value="",
                ),
                patch(
                    "pbl_jobs_finder.modules.resume_parser._extract_with_pdfplumber",
                    return_value="",
                ),
                patch(
                    "pbl_jobs_finder.modules.resume_parser._pdf_contains_images",
                    return_value=True,
                ),
                patch(
                    "pbl_jobs_finder.modules.resume_ocr.extract_text_from_image_pdf",
                    return_value=" 张三 \n Python 开发工程师 ",
                ) as ocr,
            ):
                result = parse_resume_pdf(path)
        ocr.assert_called_once_with(path)
        self.assertEqual(result, "张三\nPython 开发工程师")

    def test_blank_pdf_prompts_for_pasted_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with path.open("wb") as stream:
                writer.write(stream)
            with self.assertRaisesRegex(ResumeParseError, "未识别到文字"):
                parse_resume_pdf(path)

    def test_encrypted_pdf_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "encrypted.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            writer.encrypt("secret")
            with path.open("wb") as stream:
                writer.write(stream)
            with self.assertRaisesRegex(ResumeParseError, "加密 PDF"):
                parse_resume_pdf(path)

    def test_oversized_pdf_is_rejected_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.pdf"
            with path.open("wb") as stream:
                stream.seek(MAX_PDF_SIZE)
                stream.write(b"0")
            with self.assertRaisesRegex(ResumeParseError, "不能超过 10 MB"):
                parse_resume_pdf(path)


class ZhipuChatClientTests(unittest.TestCase):
    def test_client_applies_timeout_retries_and_output_limit(self) -> None:
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=" result "))]
        )
        sdk_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=Mock(return_value=completion))
            )
        )
        with patch("zhipuai.ZhipuAI", return_value=sdk_client) as factory:
            result = ZhipuChatClient(
                api_key="secret",
                model="glm-test",
                timeout_seconds=12.5,
                max_retries=1,
            ).complete("system", "user")

        self.assertEqual(result, "result")
        factory.assert_called_once_with(api_key="secret", timeout=12.5, max_retries=1)
        sdk_client.chat.completions.create.assert_called_once_with(
            model="glm-test",
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            temperature=0.2,
            max_tokens=4096,
            timeout=12.5,
        )

    def test_timeout_is_translated_to_actionable_error(self) -> None:
        sdk_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=Mock(side_effect=APITimeoutError()))
            )
        )
        with (
            patch("zhipuai.ZhipuAI", return_value=sdk_client),
            self.assertRaisesRegex(LLMServiceError, "响应超时"),
        ):
            ZhipuChatClient(api_key="secret", model="glm-test").complete(
                "system", "user"
            )


class ResumeOCRTests(unittest.TestCase):
    def test_ocr_combines_recognized_lines_and_closes_resources(self) -> None:
        document = FakeOCRDocument(2)
        engine = Mock(
            side_effect=[
                ([[None, "第一页标题", 0.99], [None, "项目经历", 0.98]], None),
                ([[None, "第二页", 0.97]], None),
            ]
        )
        with patch(
            "pbl_jobs_finder.modules.resume_ocr.pdfium.PdfDocument",
            return_value=document,
        ):
            result = extract_text_from_image_pdf("resume.pdf", engine=engine)

        self.assertEqual(result, "第一页标题\n项目经历\n\n第二页")
        self.assertTrue(document.closed)
        self.assertTrue(all(page.closed for page in document.pages))
        self.assertEqual(engine.call_count, 2)

    def test_ocr_rejects_image_pdf_over_page_limit(self) -> None:
        document = FakeOCRDocument(MAX_OCR_PAGES + 1)
        with (
            patch(
                "pbl_jobs_finder.modules.resume_ocr.pdfium.PdfDocument",
                return_value=document,
            ),
            self.assertRaisesRegex(ResumeOCRError, f"最多支持 {MAX_OCR_PAGES} 页"),
        ):
            extract_text_from_image_pdf("resume.pdf", engine=Mock())
        self.assertTrue(document.closed)

    def test_ocr_engine_load_failure_is_actionable_and_closes_document(self) -> None:
        document = FakeOCRDocument(1)
        with (
            patch(
                "pbl_jobs_finder.modules.resume_ocr.pdfium.PdfDocument",
                return_value=document,
            ),
            patch(
                "pbl_jobs_finder.modules.resume_ocr._get_ocr_engine",
                side_effect=RuntimeError("model missing"),
            ),
            self.assertRaisesRegex(ResumeOCRError, "OCR 组件加载失败"),
        ):
            extract_text_from_image_pdf("resume.pdf")
        self.assertTrue(document.closed)


class ResumeDiagnosisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.root = root
        self.database = Database(f"sqlite:///{(root / 'test.db').as_posix()}")
        self.users = {
            "token-a": {"phone": "13800138000"},
            "token-b": {"phone": "13900139000"},
        }
        self.quota = QuotaService(token_verifier=self.users.get)
        self.service = ResumeDiagnosisService(
            database_instance=self.database,
            quota=self.quota,
            token_verifier=self.users.get,
            chat_client=FakeChatClient(_model_response()),
            exports_dir=root / "exports",
        )

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_dir.cleanup()

    def test_diagnose_edit_and_export_complete_word_resume(self) -> None:
        outcome = self.service.diagnose(
            token="token-a",
            position="Python 后端工程师",
            pasted_text="张三，五年 Python 后端经验，负责订单服务的开发、测试与维护。",
        )
        self.assertEqual(self.quota.status("token-a").used, 1)

        edited = outcome.diagnosis.optimized_text.replace("订单接口", "核心订单接口")
        destination = self.service.export_optimized_resume(
            token="token-a",
            record_id=outcome.record_id,
            optimized_text=edited,
        )
        self.assertTrue(destination.is_file())
        self.assertEqual(destination.suffix, ".docx")
        self.assertEqual(self.quota.status("token-a").used, 1)

        document = Document(destination)
        document_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("张三", document_text)
        self.assertIn("核心订单接口", document_text)
        self.assertEqual(document.paragraphs[0].style.name, "Title")
        self.assertIn("Heading 1", {p.style.name for p in document.paragraphs})

        with self.database.session() as session:
            stored = get_resume_record(session, outcome.record_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.optimized_text, edited)

    def test_export_requires_record_ownership(self) -> None:
        outcome = self.service.diagnose(
            token="token-a",
            position="Python 后端工程师",
            pasted_text="张三，五年 Python 后端经验，负责订单服务的开发、测试与维护。",
        )
        with self.assertRaises(ResumeAccessError):
            self.service.export_optimized_resume(
                token="token-b",
                record_id=outcome.record_id,
                optimized_text=outcome.diagnosis.optimized_text,
            )

    def test_model_failure_refunds_quota_and_does_not_create_record(self) -> None:
        failing_service = ResumeDiagnosisService(
            database_instance=self.database,
            quota=self.quota,
            token_verifier=self.users.get,
            chat_client=RaisingChatClient(),
            exports_dir=Path(self.temp_dir.name) / "exports",
        )
        with self.assertRaises(LLMServiceError):
            failing_service.diagnose(
                token="token-a",
                position="Python 后端工程师",
                pasted_text="张三，五年 Python 后端经验，负责订单服务的开发、测试与维护。",
            )

        self.assertEqual(self.quota.status("token-a").used, 0)
        self.database.initialize()
        with self.database.session() as session:
            self.assertEqual(get_recent_resume_records(session, "13800138000"), [])

    def test_pasted_text_takes_precedence_over_uploaded_file(self) -> None:
        with patch(
            "pbl_jobs_finder.modules.resume_diagnosis.parse_resume_pdf"
        ) as parser:
            outcome = self.service.diagnose(
                token="token-a",
                position="Python 后端工程师",
                uploaded_file="missing.pdf",
                pasted_text="张三，五年 Python 后端经验，负责订单服务的开发、测试与维护。",
            )
        parser.assert_not_called()
        self.assertEqual(outcome.diagnosis.score, 82)

    def test_pdf_upload_is_diagnosed_and_persisted(self) -> None:
        path = self.root / "resume.pdf"
        _write_text_pdf(path, "Python backend engineer with five years experience")

        outcome = self.service.diagnose(
            token="token-a",
            position="Python 后端工程师",
            uploaded_file=path,
        )

        self.assertEqual(outcome.diagnosis.score, 82)
        self.assertEqual(self.quota.status("token-a").used, 1)
        with self.database.session() as session:
            stored = get_resume_record(session, outcome.record_id)
            self.assertIn("Python backend engineer", stored.original_text)
            self.assertIn("STAR 改写示例", stored.suggestions)

    def test_database_failure_refunds_quota(self) -> None:
        with (
            patch(
                "pbl_jobs_finder.modules.resume_diagnosis.create_resume_record",
                side_effect=RuntimeError("database unavailable"),
            ),
            self.assertRaisesRegex(RuntimeError, "database unavailable"),
        ):
            self.service.diagnose(
                token="token-a",
                position="Python 后端工程师",
                pasted_text="张三，五年 Python 后端经验，负责订单服务的开发、测试与维护。",
            )
        self.assertEqual(self.quota.status("token-a").used, 0)

    def test_pdf_parse_or_ocr_failure_refunds_quota(self) -> None:
        path = self.root / "image-resume.pdf"
        path.write_bytes(b"%PDF placeholder")
        with (
            patch(
                "pbl_jobs_finder.modules.resume_diagnosis.parse_resume_pdf",
                side_effect=ResumeParseError("图片文字识别失败，请改用文本粘贴方式"),
            ),
            self.assertRaisesRegex(ResumeParseError, "图片文字识别失败"),
        ):
            self.service.diagnose(
                token="token-a",
                position="Python 后端工程师",
                uploaded_file=path,
            )
        self.assertEqual(self.quota.status("token-a").used, 0)


if __name__ == "__main__":
    unittest.main()
