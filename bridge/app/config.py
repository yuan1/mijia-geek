"""桥接服务配置（环境变量）。"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

_LOGGER = logging.getLogger("mijia_bridge")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"

API_TOKEN = (os.environ.get("API_TOKEN") or "").strip()

DEFAULT_GATEWAY_URL = "http://192.168.1.100"
DEFAULT_EDITOR_PORT = 8086
ENV_GATEWAY_URL = (os.environ.get("GATEWAY_URL") or "").strip()
GATEWAY_URL = (ENV_GATEWAY_URL or DEFAULT_GATEWAY_URL).rstrip("/")


def _derive_editor_url(gateway_url: str) -> str:
    """官方极客版编辑器通常在同一主机的 8086 端口。"""
    try:
        parts = urlsplit(gateway_url)
        host = parts.hostname
        if not host:
            return ""
        scheme = parts.scheme or "http"
        if ":" in host:
            host = f"[{host}]"
        return f"{scheme}://{host}:{DEFAULT_EDITOR_PORT}/"
    except Exception:
        return ""


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        return ""
    return url.rstrip("/") + "/"


ENV_GATEWAY_WEB_URL = _normalize_url(
    (os.environ.get("GATEWAY_WEB_URL") or "").strip()
)


def editor_url_for(gateway_url: str) -> str:
    """显式配置优先；否则按当前网关地址推导。"""
    return ENV_GATEWAY_WEB_URL or _derive_editor_url(gateway_url)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        _LOGGER.warning("环境变量 %s=%r 不是整数，使用默认值 %s", name, raw, default)
        value = default
    return max(minimum, min(maximum, value))


SCAN_INTERVAL = _env_int("SCAN_INTERVAL", 60, 15, 3600)
