"""Small, testable wrapper around the configured chat model."""

from __future__ import annotations

from typing import Protocol

from pbl_jobs_finder.config import get_settings


class LLMConfigurationError(RuntimeError):
    """The application has no usable model configuration."""


class LLMServiceError(RuntimeError):
    """The configured model could not complete a request."""


class ChatClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the assistant's text response."""


class ZhipuChatClient:
    """Lazy ZhipuAI client so importing the app never requires a live key."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            from zhipuai import ZhipuAI

            client = ZhipuAI(
                api_key=self.api_key,
                timeout=self.timeout_seconds,
                max_retries=self.max_retries,
            )
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=4096,
                timeout=self.timeout_seconds,
            )
            content = response.choices[0].message.content
        except Exception as exc:
            error_name = type(exc).__name__
            if error_name == "APITimeoutError":
                message = "AI 服务响应超时，请稍后重试"
            elif error_name == "APIAuthenticationError":
                message = "AI 服务配置无效，请联系管理员"
            elif error_name in {
                "APIReachLimitError",
                "APIServerFlowExceedError",
                "APIInternalError",
            }:
                message = "AI 服务繁忙，请稍后重试"
            else:
                message = "AI 服务暂时不可用，请稍后重试"
            raise LLMServiceError(message) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMServiceError("AI 服务返回了空结果，请重试")
        return content.strip()


def create_default_chat_client() -> ChatClient:
    settings = get_settings()
    if not settings.zhipu_api_key:
        raise LLMConfigurationError("未配置 ZHIPU_API_KEY，暂时无法开始诊断")
    return ZhipuChatClient(
        api_key=settings.zhipu_api_key,
        model=settings.zhipu_model,
        timeout_seconds=settings.zhipu_timeout_seconds,
        max_retries=settings.zhipu_max_retries,
    )


__all__ = [
    "ChatClient",
    "LLMConfigurationError",
    "LLMServiceError",
    "ZhipuChatClient",
    "create_default_chat_client",
]
