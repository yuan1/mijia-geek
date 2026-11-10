"""Bridge REST 客户端（HA 集成唯一数据来源）。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)


class BridgeError(Exception):
    """Bridge 返回业务错误。"""


class BridgeAuthError(BridgeError):
    """API Token 无效。"""


class BridgeUnavailable(BridgeError):
    """Bridge 不可达 / 网关未连接。"""


class BridgeClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str,
                 token: str = "") -> None:
        self._session = session
        self.base_url = base_url.rstrip("/")
        self._token = (token or "").strip()

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["X-API-Token"] = self._token
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def _request(self, method: str, path: str, *,
                       json_body: Any = None, params: dict | None = None,
                       timeout: float = 15.0) -> Any:
        kwargs: dict[str, Any] = {
            "headers": self.headers,
            "params": params,
            "timeout": aiohttp.ClientTimeout(total=timeout),
        }
        if json_body is not None:
            kwargs["json"] = json_body
        try:
            async with self._session.request(
                method,
                self._url(path),
                **kwargs,
            ) as resp:
                if resp.status == 401:
                    raise BridgeAuthError("Bridge API Token 无效")
                if resp.status == 503:
                    raise BridgeUnavailable("Bridge 报告网关未连接")
                if resp.status >= 400:
                    detail = await self._error_detail(resp)
                    raise BridgeError(f"HTTP {resp.status}: {detail}")
                if resp.status == 204:
                    return None
                if resp.content_type == "application/json":
                    return await resp.json()
                return await resp.text()
        except BridgeError:
            raise
        except asyncio.TimeoutError as err:
            raise BridgeUnavailable("请求 Bridge 超时") from err
        except aiohttp.ClientError as err:
            raise BridgeUnavailable(f"无法连接 Bridge: {err}") from err

    @staticmethod
    async def _error_detail(resp: aiohttp.ClientResponse) -> str:
        try:
            data = await resp.json()
        except Exception:
            return (await resp.text())[:200]
        if isinstance(data, dict):
            return str(data.get("detail") or data)
        return str(data)[:200]

    # ---------- 状态 ----------
    async def status(self) -> dict:
        return await self._request("GET", "/api/status")

    async def connect(self, passcode: str,
                      gateway_url: str | None = None) -> dict:
        body: dict[str, Any] = {"passcode": passcode}
        if gateway_url:
            body["gateway_url"] = gateway_url
        return await self._request("POST", "/api/connect", json_body=body,
                                   timeout=50.0)

    async def refresh(self) -> dict:
        return await self._request("POST", "/api/refresh")

    # ---------- 设备 / 规则 ----------
    async def devices(self) -> dict:
        data = await self._request("GET", "/api/devices")
        return data.get("devices") or {}

    async def rules(self) -> list:
        data = await self._request("GET", "/api/rules")
        return data.get("rules") or []

    async def rule_detail(self, rule_id: str) -> dict:
        return await self._request("GET", f"/api/rule/{quote(rule_id, safe='')}")

    async def save_rule(self, rule_id: str, nodes: list, cfg: dict,
                        name: str = "") -> dict:
        return await self._request(
            "PUT",
            f"/api/rule/{quote(rule_id, safe='')}",
            json_body={"nodes": nodes, "cfg": cfg, "name": name},
        )

    async def toggle_rule(self, rule_id: str, enable: bool) -> dict:
        return await self._request(
            "POST",
            f"/api/rule/{quote(rule_id, safe='')}/toggle",
            json_body={"enable": enable},
        )

    async def delete_rule(self, rule_id: str) -> dict:
        return await self._request(
            "DELETE", f"/api/rule/{quote(rule_id, safe='')}")

    # ---------- 日志 ----------
    async def logs(self, page: int = 0) -> str:
        data = await self._request("GET", "/api/logs", params={"page": page})
        return data.get("log") or ""

    async def logs_all(self, pages: int = 3) -> str:
        data = await self._request("GET", "/api/logs/all",
                                   params={"pages": pages})
        return data.get("log") or ""

    # ---------- 变量 ----------
    async def variables(self, scope: str = "global") -> dict:
        data = await self._request("GET", "/api/variables",
                                   params={"scope": scope})
        return data.get("variables") or {}

    async def get_var(self, scope: str, var_id: str) -> Any:
        data = await self._request(
            "GET", f"/api/variable/{quote(var_id, safe='')}",
            params={"scope": scope})
        return data.get("value")

    async def scopes(self) -> list:
        data = await self._request("GET", "/api/variable/scopes")
        return data.get("scopes") or []

    async def set_var(self, scope: str, var_id: str, value: Any) -> dict:
        return await self._request(
            "PUT", f"/api/variable/{quote(var_id, safe='')}",
            json_body={"scope": scope, "value": value})

    async def create_var(self, scope: str, var_id: str, var_type: str,
                         value: Any, name: str = "") -> dict:
        return await self._request(
            "POST", "/api/variables",
            json_body={
                "scope": scope,
                "var_id": var_id,
                "var_type": var_type,
                "value": value,
                "name": name,
            })

    async def delete_var(self, scope: str, var_id: str) -> dict:
        return await self._request(
            "DELETE", f"/api/variable/{quote(var_id, safe='')}",
            params={"scope": scope})

    # ---------- SSE ----------
    async def events(self) -> AsyncIterator[dict]:
        timeout = aiohttp.ClientTimeout(total=None, connect=15.0,
                                        sock_read=None)
        async with self._session.get(
            self._url("/api/events"),
            headers=self.headers,
            timeout=timeout,
        ) as resp:
            if resp.status == 401:
                raise BridgeAuthError("Bridge API Token 无效")
            if resp.status >= 400:
                raise BridgeUnavailable(
                    f"Bridge 事件流失败: HTTP {resp.status}")
            while True:
                raw = await resp.content.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    _LOGGER.debug("忽略无效 SSE 数据: %s", payload[:120])
                    continue
                if isinstance(event, dict):
                    yield event
