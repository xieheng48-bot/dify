from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from typing import Callable
from urllib.parse import urlsplit


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Config:
    tokens: dict[str, str] = field(default_factory=lambda: {
        "demo-alice": "alice", "demo-bob": "bob"
    })
    now: Callable[[], datetime] = utc_now
    dify_api_base: str = ""
    dify_api_key: str = ""
    dify_tool_token: str = "demo-dify-tools"
    dify_tool_base_url: str = "http://127.0.0.1:8011"

    def __post_init__(self):
        url = urlsplit(self.dify_tool_base_url)
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or any(char.isspace() for char in self.dify_tool_base_url)):
            raise ValueError("DIFY_TOOL_BASE_URL 必须是无凭据、查询参数或片段的 http/https 服务地址")

    @classmethod
    def from_env(cls) -> "Config":
        raw = os.getenv("DEMO_USER_TOKENS")
        tokens = json.loads(raw) if raw else cls().tokens
        if not isinstance(tokens, dict) or not tokens or not all(
            isinstance(k, str) and k and isinstance(v, str) and v
            for k, v in tokens.items()
        ):
            raise ValueError("DEMO_USER_TOKENS 必须是非空 token -> user JSON 对象")
        base = os.getenv("DIFY_API_BASE", "").rstrip("/")
        key = os.getenv("DIFY_API_KEY", "")
        if bool(base) != bool(key):
            raise ValueError("DIFY_API_BASE 和 DIFY_API_KEY 必须同时设置")
        tool_token = os.getenv("DIFY_TOOL_TOKEN", "demo-dify-tools")
        if not tool_token or tool_token in tokens:
            raise ValueError("DIFY_TOOL_TOKEN 必须非空且与用户凭据不同")
        tool_base = os.getenv("DIFY_TOOL_BASE_URL", "http://127.0.0.1:8011").rstrip("/")
        return cls(tokens=tokens, dify_api_base=base, dify_api_key=key,
                   dify_tool_token=tool_token, dify_tool_base_url=tool_base)
