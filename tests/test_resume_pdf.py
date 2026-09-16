"""Structured resume schema, safe HTML, and PDF renderer tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from PyPDF2 import PdfReader

from pbl_jobs_finder.modules.resume_pdf import (
    ResumeBasics,
    ResumeDocument,
    ResumeDocumentError,
    ResumeExperience,
    ResumePDFError,
    create_resume_pdf,
    default_avatar_data_uri,
    document_to_markdown,
    parse_resume_document,
    prepare_photo_data_uri,
    render_resume_html,
)


def _document_payload() -> dict:
    return {
        "basics": {
            "name": "张三",
            "headline": "Python 后端工程师",
            "phone": "13800138000",
            "email": "zhangsan@example.com",
            "location": "杭州",
            "website": "example.com/profile",
            "summary": "五年后端开发经验。",
        },
        "skills": ["Python", "FastAPI"],
        "experience": [
            {
                "company": "示例科技",
                "role": "后端工程师",
                "start_date": "2022.01",
                "end_date": "至今",
                "highlights": ["负责订单系统开发，结果：[请补充真实结果]"],
            }
        ],
        "projects": [],
        "education": [],
        "certificates": [],
    }


class ResumeDocumentTests(unittest.TestCase):
    def test_model_response_is_parsed_and_normalized(self) -> None:
        payload = _document_payload()
        payload["skills"] = [" Python ", "Python", "FastAPI"]
        document = parse_resume_document(
            f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
        )
        self.assertEqual(document.basics.name, "张三")
        self.assertEqual(document.skills, ["Python", "FastAPI"])

    def test_unknown_fields_and_empty_documents_are_rejected(self) -> None:
        payload = _document_payload()
        payload["raw_html"] = "<script>alert(1)</script>"
        with self.assertRaises(ResumeDocumentError):
            parse_resume_document(json.dumps(payload, ensure_ascii=False))

        with self.assertRaises(ResumeDocumentError):
            parse_resume_document('{"basics":{"name":"个人简历"}}')

    def test_html_escapes_all_model_text_and_has_print_rules(self) -> None:
        document = ResumeDocument(
            basics=ResumeBasics(
                name='<script>alert("name")</script>',
                headline="Python & API",
                email='a@example.com" onclick="alert(1)',
                summary="擅长 <b>服务治理</b>",
            ),
            experience=[
                ResumeExperience(
                    company="A < B",
                    role="工程师",
                    highlights=["修复 </li><script>alert(1)</script> 问题"],
                )
            ],
        )
        html = render_resume_html(document)
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("@page { size: A4", html)
        self.assertIn("break-inside: avoid", html)
        self.assertNotIn("http://fonts", html)
        self.assertIn('class="resume-photo"', html)
        self.assertIn("data:image/svg+xml;base64,", html)

    def test_contact_fields_have_explicit_labels(self) -> None:
        document = parse_resume_document(
            json.dumps(_document_payload(), ensure_ascii=False)
        )
        html = render_resume_html(document)

        self.assertIn('<span class="contact-label">手机：</span>', html)
        self.assertIn('<span class="contact-label">邮箱：</span>', html)
        self.assertIn('<span class="contact-label">所在地：</span>', html)
        self.assertIn('<span class="contact-label">个人主页：</span>', html)
        self.assertNotIn("13800138000</a><a", html)

    def test_photo_upload_is_embedded_and_invalid_content_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            photo = Path(temp_dir) / "portrait.png"
            Image.new("RGB", (30, 40), "white").save(photo, format="PNG")
            uri = prepare_photo_data_uri(photo)
            self.assertTrue(uri.startswith("data:image/jpeg;base64,"))

            invalid = Path(temp_dir) / "fake.png"
            invalid.write_text("not an image", encoding="utf-8")
            with self.assertRaisesRegex(ResumePDFError, "JPEG、PNG 或 WebP"):
                prepare_photo_data_uri(invalid)

        self.assertTrue(default_avatar_data_uri().startswith("data:image/svg+xml;base64,"))

    def test_generated_document_can_be_returned_as_editable_markdown(self) -> None:
        payload = _document_payload()
        payload["additional_sections"] = [
            {"title": "补充信息", "highlights": ["获得校级一等奖"]}
        ]
        document = parse_resume_document(json.dumps(payload, ensure_ascii=False))
        markdown = document_to_markdown(document)
        self.assertIn("# 张三", markdown)
        self.assertIn("## 补充信息", markdown)
        self.assertIn("获得校级一等奖", markdown)

    def test_pdf_writer_validates_output_and_uses_safe_filename(self) -> None:
        document = parse_resume_document(
            json.dumps(_document_payload(), ensure_ascii=False)
        )
        rendered_html = ""

        def renderer(html: str, destination: Path) -> None:
            nonlocal rendered_html
            rendered_html = html
            destination.write_bytes(b"%PDF-1.4\n%%EOF")

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = create_resume_pdf(
                document,
                "Python/后端:*工程师",
                temp_dir,
                record_id=12,
                renderer=renderer,
            )
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.suffix, ".pdf")
            self.assertNotIn("/", destination.name)
            self.assertIn("张三", rendered_html)

    def test_invalid_renderer_output_is_rejected_without_partial_file(self) -> None:
        document = parse_resume_document(
            json.dumps(_document_payload(), ensure_ascii=False)
        )

        def renderer(_: str, destination: Path) -> None:
            destination.write_text("not a pdf", encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ResumePDFError):
                create_resume_pdf(document, "产品经理", temp_dir, renderer=renderer)
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    @unittest.skipUnless(
        os.getenv("RUN_PLAYWRIGHT_PDF_TESTS") == "1",
        "set RUN_PLAYWRIGHT_PDF_TESTS=1 for the Chromium integration test",
    )
    def test_playwright_generates_readable_chinese_pdf(self) -> None:
        payload = _document_payload()
        payload["experience"] = payload["experience"] * 12
        document = parse_resume_document(json.dumps(payload, ensure_ascii=False))
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = create_resume_pdf(document, "Python 后端工程师", temp_dir)
            reader = PdfReader(destination)
            self.assertGreaterEqual(len(reader.pages), 2)
            extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
            self.assertIn("Python", extracted)
            self.assertIn("订单系统", extracted)
            self.assertIn("手机：", extracted)
            self.assertIn("邮箱：", extracted)


if __name__ == "__main__":
    unittest.main()
