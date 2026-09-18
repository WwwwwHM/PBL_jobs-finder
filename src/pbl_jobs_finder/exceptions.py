"""Application-specific exception hierarchy.

Import custom exceptions from this module in new code. Feature modules continue
to re-export their existing exception names for backward compatibility.
"""

from __future__ import annotations

from typing import ClassVar


class PBLJobsFinderError(Exception):
    """Base class with a stable code, description, and runtime message."""

    code: ClassVar[str] = "10000"
    description: ClassVar[str] = "应用程序错误"
    _registered_codes: ClassVar[set[str]] = {code}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        code = cls.__dict__.get("code")
        description = cls.__dict__.get("description")
        if not isinstance(code, str) or len(code) != 5 or not code.isascii() or not code.isdigit():
            raise TypeError(f"{cls.__name__}.code must be a five-digit string")
        if not isinstance(description, str) or not description.strip():
            raise TypeError(f"{cls.__name__}.description must be a non-empty string")
        if code in PBLJobsFinderError._registered_codes:
            raise TypeError(f"duplicate application error code: {code}")
        PBLJobsFinderError._registered_codes.add(code)

    def __init__(self, message: str | None = None) -> None:
        self.message = self.description if message is None else str(message)
        super().__init__(self.message)

    def to_dict(self) -> dict[str, str]:
        """Return the stable fields used by logs and API error responses."""

        return {
            "code": self.code,
            "description": self.description,
            "message": self.message,
        }

    def __repr__(self) -> str:
        fields = ", ".join(f"{key}={value!r}" for key, value in self.to_dict().items())
        return f"{type(self).__name__}({fields})"


class AuthenticationError(PBLJobsFinderError, ValueError):
    """The supplied token does not identify an active user."""

    code = "11001"
    description = "身份认证失败"


class QuotaExceededError(PBLJobsFinderError, ValueError):
    """The user's daily quota has been exhausted."""

    code = "11002"
    description = "每日调用配额已用尽"


class InterviewValidationError(PBLJobsFinderError, ValueError):
    """Interview inputs or a generated question failed validation."""

    code = "12001"
    description = "模拟面试数据校验失败"


class InterviewUnavailableError(PBLJobsFinderError, RuntimeError):
    """Interview infrastructure is not ready to create a session."""

    code = "12002"
    description = "模拟面试服务不可用"


class InterviewAccessError(PBLJobsFinderError, PermissionError):
    """The authenticated user does not own the requested interview session."""

    code = "12003"
    description = "无权访问模拟面试会话"


class ResumeValidationError(PBLJobsFinderError, ValueError):
    """Resume diagnosis inputs are incomplete or outside supported limits."""

    code = "13001"
    description = "简历诊断数据校验失败"


class ResumeResponseError(PBLJobsFinderError, RuntimeError):
    """The model returned a response that cannot be safely persisted."""

    code = "13002"
    description = "简历模型响应无效"


class ResumeAccessError(PBLJobsFinderError, PermissionError):
    """The authenticated user does not own the requested resume record."""

    code = "13003"
    description = "无权访问简历记录"


class ResumeExportError(PBLJobsFinderError, RuntimeError):
    """The optimized resume could not be written as a Word document."""

    code = "13004"
    description = "Word 简历导出失败"


class ResumeParseError(PBLJobsFinderError, ValueError):
    """An uploaded resume cannot be safely used for diagnosis."""

    code = "13005"
    description = "上传的简历解析失败"


class ResumeOCRError(PBLJobsFinderError, RuntimeError):
    """An image PDF could not be converted into usable resume text."""

    code = "13006"
    description = "图片型简历文字识别失败"


class ResumeDocumentError(PBLJobsFinderError, ValueError):
    """The model response cannot be represented by the resume schema."""

    code = "13007"
    description = "简历文档结构无效"


class ResumePDFError(PBLJobsFinderError, RuntimeError):
    """The validated resume could not be rendered as a PDF."""

    code = "13008"
    description = "PDF 简历生成失败"


class EmbeddingConfigurationError(PBLJobsFinderError, RuntimeError):
    """The application has no usable embedding configuration."""

    code = "14001"
    description = "Embedding 服务配置无效"


class EmbeddingServiceError(PBLJobsFinderError, RuntimeError):
    """The embedding provider failed or returned an invalid response."""

    code = "14002"
    description = "Embedding 服务调用失败"


class LLMConfigurationError(PBLJobsFinderError, RuntimeError):
    """The application has no usable model configuration."""

    code = "15001"
    description = "大语言模型服务配置无效"


class LLMServiceError(PBLJobsFinderError, RuntimeError):
    """The configured model could not complete a request."""

    code = "15002"
    description = "大语言模型服务调用失败"


__all__ = [
    "AuthenticationError",
    "EmbeddingConfigurationError",
    "EmbeddingServiceError",
    "InterviewAccessError",
    "InterviewUnavailableError",
    "InterviewValidationError",
    "LLMConfigurationError",
    "LLMServiceError",
    "PBLJobsFinderError",
    "QuotaExceededError",
    "ResumeAccessError",
    "ResumeDocumentError",
    "ResumeExportError",
    "ResumeOCRError",
    "ResumePDFError",
    "ResumeParseError",
    "ResumeResponseError",
    "ResumeValidationError",
]
