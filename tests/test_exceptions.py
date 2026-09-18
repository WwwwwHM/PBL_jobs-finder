from __future__ import annotations

import re
import unittest

from pbl_jobs_finder import PBLJobsFinderError
from pbl_jobs_finder.exceptions import (
    AuthenticationError,
    EmbeddingConfigurationError,
    EmbeddingServiceError,
    InterviewAccessError,
    InterviewUnavailableError,
    InterviewValidationError,
    LLMConfigurationError,
    LLMServiceError,
    QuotaExceededError,
    ResumeAccessError,
    ResumeDocumentError,
    ResumeExportError,
    ResumeOCRError,
    ResumeParseError,
    ResumePDFError,
    ResumeResponseError,
    ResumeValidationError,
)

VALUE_ERRORS = (
    AuthenticationError,
    InterviewValidationError,
    QuotaExceededError,
    ResumeDocumentError,
    ResumeParseError,
    ResumeValidationError,
)
RUNTIME_ERRORS = (
    EmbeddingConfigurationError,
    EmbeddingServiceError,
    InterviewUnavailableError,
    LLMConfigurationError,
    LLMServiceError,
    ResumeExportError,
    ResumeOCRError,
    ResumePDFError,
    ResumeResponseError,
)
ACCESS_ERRORS = (InterviewAccessError, ResumeAccessError)
ALL_CUSTOM_ERRORS = VALUE_ERRORS + RUNTIME_ERRORS + ACCESS_ERRORS


class ExceptionHierarchyTests(unittest.TestCase):
    def test_custom_exceptions_share_application_base(self) -> None:
        for exception_type in ALL_CUSTOM_ERRORS:
            with self.subTest(exception_type=exception_type.__name__):
                self.assertTrue(issubclass(exception_type, PBLJobsFinderError))

    def test_custom_exceptions_have_unique_codes_and_descriptions(self) -> None:
        exception_types = (PBLJobsFinderError,) + ALL_CUSTOM_ERRORS
        codes = [exception_type.code for exception_type in exception_types]

        self.assertTrue(all(re.fullmatch(r"\d{5}", code) for code in codes))
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(
            all(exception_type.description.strip() for exception_type in exception_types)
        )

    def test_exception_exposes_fixed_debug_fields(self) -> None:
        error = ResumeParseError("PDF 文件已损坏")

        self.assertEqual(error.code, "13005")
        self.assertEqual(error.description, "上传的简历解析失败")
        self.assertEqual(error.message, "PDF 文件已损坏")
        self.assertEqual(error.args, ("PDF 文件已损坏",))
        self.assertEqual(str(error), error.message)
        self.assertEqual(
            error.to_dict(),
            {
                "code": "13005",
                "description": "上传的简历解析失败",
                "message": "PDF 文件已损坏",
            },
        )
        self.assertEqual(
            repr(error),
            "ResumeParseError(code='13005', description='上传的简历解析失败', "
            "message='PDF 文件已损坏')",
        )

    def test_exception_uses_description_as_default_message(self) -> None:
        error = ResumeParseError()

        self.assertEqual(error.message, error.description)
        self.assertEqual(str(error), error.description)

    def test_subclass_rejects_an_invalid_error_contract(self) -> None:
        with self.assertRaisesRegex(TypeError, "five-digit string"):

            class InvalidCodeError(PBLJobsFinderError):
                code = "1234"
                description = "无效错误码"

    def test_validation_exceptions_remain_value_errors(self) -> None:
        for exception_type in VALUE_ERRORS:
            with self.subTest(exception_type=exception_type.__name__):
                self.assertTrue(issubclass(exception_type, ValueError))

    def test_service_exceptions_remain_runtime_errors(self) -> None:
        for exception_type in RUNTIME_ERRORS:
            with self.subTest(exception_type=exception_type.__name__):
                self.assertTrue(issubclass(exception_type, RuntimeError))

    def test_access_exceptions_remain_permission_errors(self) -> None:
        for exception_type in ACCESS_ERRORS:
            with self.subTest(exception_type=exception_type.__name__):
                self.assertTrue(issubclass(exception_type, PermissionError))

    def test_feature_modules_keep_legacy_exception_imports(self) -> None:
        from pbl_jobs_finder.modules.interview_agent import (
            InterviewValidationError as legacy_interview,
        )
        from pbl_jobs_finder.modules.quota import (
            AuthenticationError as legacy_authentication,
        )
        from pbl_jobs_finder.modules.resume_diagnosis import (
            ResumeResponseError as legacy_resume,
        )
        from pbl_jobs_finder.utils.llm_client import LLMServiceError as legacy_llm

        self.assertIs(legacy_interview, InterviewValidationError)
        self.assertIs(legacy_authentication, AuthenticationError)
        self.assertIs(legacy_resume, ResumeResponseError)
        self.assertIs(legacy_llm, LLMServiceError)


if __name__ == "__main__":
    unittest.main()
