"""Global success and failure message results.

Message results are ordinary return values for expected operation outcomes.
They are deliberately separate from the exception hierarchy, which represents
errors that interrupt normal control flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Self, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class MessageResult(Generic[T]):
    """A serializable success or failure result with optional payload data."""

    success: bool
    message: str
    data: T | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise TypeError("success must be a boolean")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be a non-empty string")

    def to_dict(self) -> dict[str, object]:
        """Return the stable representation used at application boundaries."""

        return {
            "success": self.success,
            "message": self.message,
            "data": self.data,
        }


class Message:
    """Factory for consistent successful and failed operation results."""

    def __new__(cls) -> Self:
        raise TypeError("Message is a utility class and cannot be instantiated")

    @staticmethod
    def success(
        message: str = "操作成功",
        *,
        data: T | None = None,
    ) -> MessageResult[T]:
        return MessageResult(success=True, message=message, data=data)

    @staticmethod
    def failure(
        message: str = "操作失败",
        *,
        data: T | None = None,
    ) -> MessageResult[T]:
        return MessageResult(success=False, message=message, data=data)


__all__ = ["Message", "MessageResult"]
