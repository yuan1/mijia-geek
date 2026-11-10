"""MCP 服务工具注册、工具调用与规则 lint 测试。"""
from __future__ import annotations

import asyncio

import pytest

import mcp_server


class FakeClient:
    def __init__(self):
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"path": path, "params": params}

    async def post(self, path, body=None, params=None):
        self.calls.append(("POST", path, body))
        return {"path": path}

    async def put(self, path, body=None):
        self.calls.append(("PUT", path, body))
        return {"path": path}

    async def delete(self, path, params=None):
        self.calls.append(("DELETE", path, params))
        return {"path": path}


def tool_names(server):
    return {tool.name for tool in asyncio.run(server.list_tools())}


def test_mcp_tool_sets():
    client = FakeClient()
    read_only = mcp_server.build_server(
        client, allow_write=False, allow_passcode=False)
    names = tool_names(read_only)
    assert "bridge_status" in names
    assert "save_rule" not in names
    assert "connect_gateway" not in names

    writable = mcp_server.build_server(
        client, allow_write=True, allow_passcode=False)
    names = tool_names(writable)
    assert {"save_rule", "toggle_rule", "delete_rule"} <= names
    assert "connect_gateway" not in names

    # 登录码工具需要同时开启写入与 passcode 开关
    passcode_only = mcp_server.build_server(
        client, allow_write=False, allow_passcode=True)
    names = tool_names(passcode_only)
    assert "connect_gateway" not in names
    assert "save_rule" not in names

    with_passcode = mcp_server.build_server(
        client, allow_write=True, allow_passcode=True)
    assert "connect_gateway" in tool_names(with_passcode)


def test_mcp_read_tools_delegate_to_bridge():
    client = FakeClient()
    server = mcp_server.build_server(
        client, allow_write=False, allow_passcode=False)

    result = asyncio.run(server.call_tool("bridge_status", {}))
    assert result.is_error is False
    assert client.calls[-1] == ("GET", "/api/status", None)

    asyncio.run(server.call_tool("list_variables", {"scope": "rule_r1"}))
    assert client.calls[-1] == ("GET", "/api/variables", {"scope": "rule_r1"})

    asyncio.run(server.call_tool("get_variable", {"var_id": "v1"}))
    assert client.calls[-1] == ("GET", "/api/variable/v1", {"scope": "global"})

    asyncio.run(server.call_tool("get_logs_all", {"pages": 4}))
    assert client.calls[-1] == ("GET", "/api/logs/all", {"pages": 4})


def test_mcp_connect_gateway_requires_write_and_passcode():
    client = FakeClient()
    server = mcp_server.build_server(
        client, allow_write=True, allow_passcode=True)

    asyncio.run(server.call_tool("connect_gateway", {"passcode": "123456"}))
    assert client.calls[-1] == ("POST", "/api/connect",
                                {"passcode": "123456"})


def test_mcp_save_rule_rejects_invalid_graph_before_writing():
    client = FakeClient()
    server = mcp_server.build_server(
        client, allow_write=True, allow_passcode=False)

    with pytest.raises(Exception):
        asyncio.run(server.call_tool("save_rule", {
            "rule_id": "r1",
            "nodes": [{"id": "n1", "type": "deviceOutput", "inputs": {}}],
        }))
    assert client.calls == []


def test_mcp_save_rule_writes_cfg_and_reads_back():
    client = FakeClient()
    server = mcp_server.build_server(
        client, allow_write=True, allow_passcode=False)
    nodes = _led_nodes()

    asyncio.run(server.call_tool("save_rule", {
        "rule_id": "r1", "nodes": nodes, "name": "新规则",
    }))

    put_calls = [call for call in client.calls if call[0] == "PUT"]
    method, path, body = put_calls[0]
    assert (method, path) == ("PUT", "/api/rule/r1")
    assert body["cfg"]["id"] == "r1"
    assert body["cfg"]["enable"] is False       # 新规则默认停用
    assert body["cfg"]["userData"]["name"] == "新规则"
    assert body["cfg"]["uiType"] == "graph"
    assert client.calls[-1] == ("GET", "/api/rule/r1", None)
    # 写入体必须是网关可接受的形状：连线为 "目标.端口" 字符串
    assert body["nodes"][0]["outputs"]["output"] == ["n2.trigger"]


def _led_nodes():
    """一份符合网关真实 schema 的最小节点图（灯开/关）。"""
    return [
        {"id": "n1", "type": "deviceInput",
         "cfg": {"urn": "urn:miot-spec-v2:device:light:0000A001:demo:1", "version": 0},
         "props": {"did": "1", "siid": 2, "piid": 1, "dtype": "boolean",
                   "operator": "=", "v1": True, "preload": False},
         "inputs": {}, "outputs": {"output": ["n2.trigger"]}},
        {"id": "n2", "type": "deviceOutput",
         "cfg": {"urn": "urn:miot-spec-v2:device:light:0000A001:demo:1", "version": 0},
         "props": {"did": "1", "siid": 2, "piid": 1, "value": True},
         "inputs": {"trigger": None}, "outputs": {"output": []}},
    ]


def test_mcp_lint_nodes():
    assert mcp_server.lint_nodes(_led_nodes()) == []

    bad = [
        {"id": "n1", "type": "deviceOutput",
         "cfg": {"urn": "", "version": 0}, "props": {},
         "inputs": {"trigger": None},
         "outputs": {"output": ["missing.trigger"]}},
    ]
    errors = mcp_server.lint_nodes(bad)
    assert any("不存在的节点" in item for item in errors)
    assert any("没有上游来源" in item for item in errors)

    duplicate = [
        {"id": "n1", "type": "delay", "cfg": {"urn": "", "version": 0},
         "props": {"timeout": 1000}, "inputs": {"input": None},
         "outputs": {"output": []}},
        {"id": "n1", "type": "condition", "cfg": {"urn": "", "version": 0},
         "props": {}, "inputs": {"trigger": None, "condition": None},
         "outputs": {"met": [], "unmet": []}},
    ]
    errors = mcp_server.lint_nodes(duplicate)
    assert any("节点 ID 重复" in item for item in errors)
    assert any("缺少 trigger 来源" in item for item in errors)
    assert any("缺少 condition 来源" in item for item in errors)

    # 未知节点类型必须被拦截
    errors = mcp_server.lint_nodes([
        {"id": "n1", "type": "whileLoop", "cfg": {"urn": "", "version": 0},
         "props": {}, "inputs": {}, "outputs": {}},
    ])
    assert any("不支持的节点类型" in item for item in errors)

    assert mcp_server.lint_nodes([]) == ["nodes 必须是非空数组"]


def test_mcp_parse_target_and_build_cfg():
    assert mcp_server._parse_target(["n1", "trigger"]) == ("n1", "trigger")
    assert mcp_server._parse_target("n1.trigger") == ("n1", "trigger")
    assert mcp_server._parse_target("n1") is None
    assert mcp_server._parse_target([]) is None

    cfg = mcp_server._build_cfg("r1", None, "", False)
    assert cfg == {
        "id": "r1",
        "uiType": "graph",
        "enable": False,
        "userData": {
            "name": "r1",
            "lastUpdateTime": 0,
            "transform": {"x": 0, "y": 0, "scale": 1, "rotate": 0},
        },
    }

    cfg = mcp_server._build_cfg(
        "r2", {"enable": True, "userData": {"transform": {"x": 5}}},
        "名称", None)
    assert cfg["enable"] is True                # 显式 cfg 优先
    assert cfg["userData"]["transform"] == {"x": 5}


def test_mcp_save_rule_preserves_existing_metadata():
    class ExistingClient(FakeClient):
        async def get(self, path, params=None):
            self.calls.append(("GET", path, params))
            if path == "/api/rules":
                return {"rules": [{
                    "id": "r1", "name": "旧规则", "enabled": True,
                    "updateTime": 123,
                    "userData": {"transform": {"x": 7, "y": 8,
                                               "scale": 1, "rotate": 0}},
                }]}
            return {"nodes": []}

    client = ExistingClient()
    server = mcp_server.build_server(
        client, allow_write=True, allow_passcode=False)
    nodes = _led_nodes()
    asyncio.run(server.call_tool("save_rule", {
        "rule_id": "r1", "nodes": nodes,
    }))
    body = [call for call in client.calls if call[0] == "PUT"][0][2]
    assert body["cfg"]["enable"] is True
    assert body["cfg"]["userData"]["name"] == "旧规则"
    assert body["cfg"]["userData"]["lastUpdateTime"] == 123
    assert body["cfg"]["userData"]["transform"]["x"] == 7


def test_mcp_bearer_middleware():
    called = []

    async def app(scope, receive, send):
        called.append(scope["path"])

    middleware = mcp_server.BearerAuthMiddleware(app, "secret")
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(
        {"type": "http", "path": "/mcp", "headers": []}, None, send))
    assert sent[0]["status"] == 401
    assert called == []

    sent.clear()
    asyncio.run(middleware(
        {"type": "http", "path": "/mcp",
         "headers": [(b"authorization", b"Bearer secret")]}, None, send))
    assert called == ["/mcp"]
