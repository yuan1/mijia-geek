#!/usr/bin/env python3
"""mijia-geek-bridge MCP 服务。

通过 Bridge REST API 暴露只读/受控写入工具，供 Claude Desktop、Codex、
Cursor、Cline 等 MCP 客户端使用。

默认只读；设置 MCP_ALLOW_WRITE=1 才注册写入工具。
默认不暴露登录码工具；设置 MCP_ALLOW_PASSCODE=1 才注册 connect_gateway。

运行方式：
  python mcp_server.py                         # stdio
  python mcp_server.py --transport streamable-http --host 127.0.0.1 --port 8098
"""
from __future__ import annotations

import argparse
import copy
import hmac
import logging
import os
import sys
from typing import Any

import httpx
import uvicorn
from mcp.server.mcpserver import MCPServer

from schema import normalize_graph, normalize_dtype, normalize_operator

_LOGGER = logging.getLogger("mijia_mcp")

DEFAULT_BRIDGE_URL = "http://127.0.0.1:8099"
DEFAULT_TOKEN = (os.environ.get("BRIDGE_API_TOKEN")
                 or os.environ.get("API_TOKEN") or "").strip()


class BridgeError(RuntimeError):
    """Bridge REST 调用失败。"""


class BearerAuthMiddleware:
    """为 MCP HTTP 传输增加最小 Bearer Token 鉴权。"""

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self.token:
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        supplied = headers.get(b"authorization", b"").decode("utf-8", "replace")
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:]
        if not hmac.compare_digest(supplied.encode(), self.token.encode()):
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": b'{"detail":"unauthorized"}',
            })
            return
        await self.app(scope, receive, send)


class BridgeClient:
    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = (token or "").strip()

    async def _request(self, method: str, path: str, *,
                       json_body: Any = None,
                       params: dict | None = None,
                       timeout: float = 30.0) -> Any:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["X-API-Token"] = self.token
        kwargs: dict[str, Any] = {"headers": headers, "params": params}
        if json_body is not None:
            kwargs["json"] = json_body
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=timeout) as client:
                resp = await client.request(method, path, **kwargs)
        except httpx.HTTPError as err:
            raise BridgeError(f"无法连接 Bridge: {err}") from err
        if resp.status_code == 401:
            raise BridgeError("Bridge API Token 无效")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise BridgeError(f"HTTP {resp.status_code}: {detail}")
        if resp.status_code == 204:
            return None
        if "application/json" in resp.headers.get("content-type", ""):
            return resp.json()
        return resp.text

    async def get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, body: Any = None,
                   params: dict | None = None) -> Any:
        return await self._request("POST", path, json_body=body, params=params)

    async def put(self, path: str, body: Any = None) -> Any:
        return await self._request("PUT", path, json_body=body)

    async def delete(self, path: str,
                     params: dict | None = None) -> Any:
        return await self._request("DELETE", path, params=params)


def _parse_target(value: Any) -> tuple[str, str] | None:
    if isinstance(value, list) and len(value) >= 2:
        return str(value[0]), str(value[1])
    if isinstance(value, str) and "." in value:
        target, port = value.split(".", 1)
        if target and port:
            return target, port
    return None


def lint_nodes(nodes: Any) -> list[str]:
    """MCP 写入前的基础图结构校验（与网关 schema 对齐）。"""
    errors: list[str] = []
    if not isinstance(nodes, list) or not nodes:
        return ["nodes 必须是非空数组"]

    # 调用同一套 schema 校验器，保证 MCP 与 REST 两侧提示一致
    try:
        from schema import validate_graph as _validate_graph
    except ImportError:  # pragma: no cover - 仅独立运行且缺少模块时
        _validate_graph = None
    if _validate_graph is not None:
        errors.extend(_validate_graph({"nodes": nodes, "cfg": {}}))
    by_id: dict[str, dict] = {}
    incoming: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"nodes[{index}] 必须是对象")
            continue
        node_id = str(node.get("id") or "")
        if not node_id:
            errors.append(f"nodes[{index}] 缺少 id")
            continue
        if node_id in by_id:
            errors.append(f"节点 ID 重复: {node_id}")
        by_id[node_id] = node
        if not node.get("type"):
            errors.append(f"节点 {node_id} 缺少 type")
    for node in by_id.values():
        for targets in (node.get("outputs") or {}).values():
            if not isinstance(targets, list):
                continue
            for target in targets:
                parsed = _parse_target(target)
                if not parsed:
                    errors.append(f"{node['id']} 存在非法连线: {target!r}")
                    continue
                target_id, port = parsed
                target_node = by_id.get(target_id)
                if target_node is None:
                    errors.append(f"{node['id']} 指向不存在的节点 {target_id}")
                    continue
                if port not in (target_node.get("inputs") or {}):
                    errors.append(
                        f"{node['id']} 指向 {target_id} 未声明的输入端口 {port}")
                incoming.add(f"{target_id}.{port}")
    sinks = {"deviceOutput", "deviceGetSetVar",
             "varSetNumber", "varSetString"}
    for node_id, node in by_id.items():
        if node.get("type") == "condition":
            if f"{node_id}.trigger" not in incoming:
                errors.append(f"condition {node_id} 缺少 trigger 来源")
            if f"{node_id}.condition" not in incoming:
                errors.append(f"condition {node_id} 缺少 condition 来源")
        if node.get("type") in sinks and \
                f"{node_id}.trigger" not in incoming and \
                f"{node_id}.input" not in incoming:
            errors.append(f"执行节点 {node_id} 没有上游来源")
    return errors


def _build_cfg(rule_id: str, cfg: dict | None, name: str,
               enable: bool | None, existing: dict | None = None) -> dict:
    existing = existing or {}
    existing_cfg = existing.get("cfg") if isinstance(existing.get("cfg"), dict) else {}
    existing_user = existing.get("userData")
    if not isinstance(existing_user, dict):
        existing_user = existing_cfg.get("userData") if isinstance(existing_cfg.get("userData"), dict) else {}

    result = dict(cfg or {})
    result["id"] = rule_id
    result.setdefault("uiType", "graph")
    if enable is not None:
        result["enable"] = bool(enable)
    elif "enable" in result:
        result["enable"] = bool(result["enable"])
    else:
        result["enable"] = bool(existing.get("enabled",
                                             existing.get("enable", False)))

    caller_user = result.get("userData")
    user_data = dict(existing_user)
    if isinstance(caller_user, dict):
        user_data.update(caller_user)
    user_data["name"] = name or existing.get("name") or user_data.get("name") or rule_id
    user_data.setdefault("lastUpdateTime", existing.get("updateTime", 0))
    if not isinstance(user_data.get("transform"), dict):
        user_data["transform"] = {"x": 0, "y": 0, "scale": 1, "rotate": 0}
    result["userData"] = user_data
    return result


async def _existing_rule(client: BridgeClient, rule_id: str) -> dict:
    try:
        data = await client.get("/api/rules")
    except BridgeError:
        return {}
    for rule in data.get("rules", []) if isinstance(data, dict) else []:
        if isinstance(rule, dict) and str(rule.get("id")) == rule_id:
            return rule
    return {}


def build_server(client: BridgeClient, *, allow_write: bool,
                 allow_passcode: bool) -> MCPServer:
    instructions = (
        "操作米家自动化极客版 Bridge。优先只读：先列设备/规则/变量，再写入。"
        "写入前检查节点 ID、连线和端口；新规则默认 enable=false；"
        "保存后重新读取验证。官方编辑器仍由人工最终确认。"
    )
    mcp = MCPServer("mijia-geek-bridge", instructions=instructions,
                    version="2.2.0")

    # ---------- 只读工具 ----------
    @mcp.tool()
    async def bridge_status() -> dict:
        """查看 Bridge 连接状态、设备/规则统计、官方编辑器地址。"""
        return await client.get("/api/status")

    @mcp.tool()
    async def list_devices() -> dict:
        """列出网关设备（did/name/model/modelName/roomName/urn/online）。"""
        return await client.get("/api/devices")

    @mcp.tool()
    async def list_rules() -> dict:
        """列出极客版自动化规则（id/name/enabled/updateTime）。"""
        return await client.get("/api/rules")

    @mcp.tool()
    async def get_rule(rule_id: str) -> dict:
        """读取单条规则的节点图（只读）。"""
        return await client.get(f"/api/rule/{rule_id}")

    @mcp.tool()
    async def list_variables(scope: str = "global") -> dict:
        """列出变量。scope 默认为 global，也可以是 rule_xxx。"""
        return await client.get("/api/variables", {"scope": scope})

    @mcp.tool()
    async def list_variable_scopes() -> dict:
        """列出变量作用域。"""
        return await client.get("/api/variable/scopes")

    @mcp.tool()
    async def get_variable(var_id: str, scope: str = "global") -> dict:
        """读取单个变量值。"""
        return await client.get(f"/api/variable/{var_id}", {"scope": scope})

    @mcp.tool()
    async def get_logs(page: int = 0) -> dict:
        """读取单页网关执行日志。"""
        return await client.get("/api/logs", {"page": page})

    @mcp.tool()
    async def get_logs_all(pages: int = 3) -> dict:
        """连续读取多页日志（最多 20 页）。"""
        return await client.get("/api/logs/all", {"pages": pages})

    @mcp.tool()
    async def get_official_editor_url() -> dict:
        """返回官方极客版编辑器地址（用于人工编辑/确认）。"""
        status = await client.get("/api/status")
        return {"url": status.get("gateway_web_url") or "",
                "note": "官方编辑器不经过 Bridge，需要单独输入网关登录码"}

    # ---------- 写入工具（默认关闭） ----------
    if allow_write:
        if allow_passcode:
            @mcp.tool()
            async def connect_gateway(passcode: str) -> dict:
                """连接网关。登录码一次性；需同时开启 MCP_ALLOW_WRITE 与 MCP_ALLOW_PASSCODE。"""
                return await client.post("/api/connect",
                                         {"passcode": passcode})

        @mcp.tool()
        async def save_rule(rule_id: str, nodes: list, cfg: dict | None = None,
                            name: str = "", enable: bool | None = None,
                            force: bool = False) -> dict:
            """保存规则节点图；新规则默认停用，更新时保留原元数据。"""
            # 先归一化为网关真实结构（连线字符串、cfg.version、inputs 端口、
            # dtype/operator 写法），再用同一套校验器复查，避免两套规则不一致。
            graph, normalized = normalize_graph({"nodes": copy.deepcopy(nodes)})
            nodes = graph["nodes"]
            errors = lint_nodes(nodes)
            if errors and not force:
                raise BridgeError("规则校验失败: " + "; ".join(errors[:8]))
            existing = await _existing_rule(client, rule_id)
            built = _build_cfg(rule_id, cfg, name, enable, existing)
            await client.put(f"/api/rule/{rule_id}",
                             {"nodes": nodes, "cfg": built,
                              "name": built["userData"]["name"]})
            readback = await client.get(f"/api/rule/{rule_id}")
            result = {"ok": True, "rule_id": rule_id,
                      "warnings": errors, "readback": readback}
            if normalized:
                result["normalized"] = normalized
            return result

        @mcp.tool()
        async def toggle_rule(rule_id: str, enable: bool) -> dict:
            """启用或停用规则。"""
            return await client.post(f"/api/rule/{rule_id}/toggle",
                                     {"enable": enable})

        @mcp.tool()
        async def delete_rule(rule_id: str) -> dict:
            """删除规则（不可恢复，谨慎使用）。"""
            return await client.delete(f"/api/rule/{rule_id}")

        @mcp.tool()
        async def set_variable(var_id: str, value: Any,
                               scope: str = "global") -> dict:
            """设置变量值。"""
            return await client.put(f"/api/variable/{var_id}",
                                    {"scope": scope, "value": value})

        @mcp.tool()
        async def create_variable(var_id: str, var_type: str = "string",
                                  value: Any = None, scope: str = "global",
                                  name: str = "") -> dict:
            """创建变量；type 仅支持 string/number。"""
            return await client.post("/api/variables", {
                "scope": scope, "var_id": var_id, "var_type": var_type,
                "value": value, "name": name,
            })

        @mcp.tool()
        async def delete_variable(var_id: str, scope: str = "global") -> dict:
            """删除变量。"""
            return await client.delete(f"/api/variable/{var_id}",
                                       {"scope": scope})

        @mcp.tool()
        async def refresh_data() -> dict:
            """强制 Bridge 从网关刷新设备/规则缓存。"""
            return await client.post("/api/refresh")

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Mijia Geek Bridge MCP server")
    parser.add_argument("--url", default=os.environ.get("BRIDGE_URL",
                                                        DEFAULT_BRIDGE_URL),
                        help="Bridge REST 地址")
    parser.add_argument("--token", default=DEFAULT_TOKEN,
                        help="Bridge API Token")
    parser.add_argument("--transport", default="stdio",
                        choices=["stdio", "sse", "streamable-http"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--allow-write", action="store_true",
                        default=os.environ.get("MCP_ALLOW_WRITE", "").lower()
                        in ("1", "true", "yes", "on"))
    parser.add_argument("--allow-passcode", action="store_true",
                        default=os.environ.get("MCP_ALLOW_PASSCODE", "").lower()
                        in ("1", "true", "yes", "on"))
    parser.add_argument("--http-token",
                        default=(os.environ.get("MCP_HTTP_TOKEN")
                                 or DEFAULT_TOKEN),
                        help="MCP HTTP Bearer Token（HTTP 传输必填）")
    args = parser.parse_args()

    if args.transport != "stdio" and not args.http_token:
        parser.error("HTTP MCP 必须设置 --http-token / MCP_HTTP_TOKEN")
    allow_passcode = bool(args.allow_passcode and args.transport == "stdio")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stderr)
    _LOGGER.info("Bridge: %s | write=%s | passcode=%s | transport=%s",
                 args.url, args.allow_write, allow_passcode, args.transport)

    client = BridgeClient(args.url, args.token)
    server = build_server(client,
                          allow_write=args.allow_write,
                          allow_passcode=allow_passcode)
    if args.transport == "stdio":
        server.run("stdio")
        return
    if args.transport == "sse":
        app = server.sse_app()
    else:
        app = server.streamable_http_app(
            streamable_http_path="/mcp", host=args.host)
    app = BearerAuthMiddleware(app, args.http_token)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
