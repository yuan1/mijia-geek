"""测试公共配置：路径、数据目录与依赖检查。

测试依赖在 ``bridge/requirements.txt``（运行时）和
``requirements-dev.txt``（pytest / aiohttp）中声明。缺少依赖时直接报错，
而不是用 ``pytest.importorskip`` 静默跳过 —— 否则 CI 会在什么都没跑的情况
下“通过”。
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "bridge" / "app"

# 让 tests/ 直接 import bridge 的顶层模块（main / api / protocol / mcp_server）
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# config.py 在导入时创建 DATA_DIR；默认的 /data 在 Windows/开发机上不可写
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="mjg-tests-"))

_REQUIRED = {
    "pytest": "pytest",
    "aiohttp": "aiohttp",
    "cryptography": "cryptography",
    "fastapi": "fastapi",
    "httpx": "httpx",
    "mcp": "mcp",
    "pydantic": "pydantic",
    "websockets": "websockets",
}

_missing = [package for module, package in _REQUIRED.items()
            if importlib.util.find_spec(module) is None]
if _missing:
    raise pytest.UsageError(
        "缺少测试依赖: " + ", ".join(_missing) +
        "\n请先执行: pip install -r requirements-dev.txt"
    )
