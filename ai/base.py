"""Provider-agnostic AI interface. Add another free provider by implementing AIProvider."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AIError(Exception):
    pass


class AIQuotaExceeded(AIError):
    def __init__(self, retry_after: int = 60):
        super().__init__("quota exceeded")
        self.retry_after = retry_after


class AIUnavailable(AIError):
    pass


class AIRateLimited(AIError):
    def __init__(self, retry_after: int):
        super().__init__("rate limited")
        self.retry_after = retry_after


@dataclass
class ChatTurn:
    role: str      # "user" | "model"
    text: str
    image: bytes | None = None      # optional picture (e.g. a sticker); providers without vision simply ignore it
    image_mime: str = "image/webp"


class AIProvider(Protocol):
    name: str

    async def generate(self, system: str, turns: list[ChatTurn], *, temperature: float = 0.9, json_mode: bool = False) -> str: ...
