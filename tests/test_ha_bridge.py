"""HA 集成 Bridge 客户端测试（不导入 Home Assistant）。

覆盖 REST 状态码映射、Token 请求头、URL/路径编码与 SSE 解析。
"""
from __future__ import annotations

import asyncio

import pytest

from ha_env import load_integration_module

bridge = load_integration_module("bridge")
BridgeAuthError = bridge.BridgeAuthError
BridgeClient = bridge.BridgeClient
BridgeError = bridge.BridgeError
BridgeUnavailable = bridge.BridgeUnavailable


class FakeContent:
    def __init__(self, lines=()):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


class FakeResponse:
    def __init__(self, status=200, *, payload=None, text="",
                 content_type="application/json", lines=()):
        self.status = status
        self._payload = payload
        self._text = text
        self.content_type = content_type
        self.content = FakeContent(lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload

    async def text(self) -> str:
        return self._text


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response


def run(coro):
    return asyncio.run(coro)


def test_base_url_and_headers():
    client = BridgeClient(FakeSession(FakeResponse()), "http://bridge:8099/",
                          "  token  ")
    assert client.base_url == "http://bridge:8099"
    assert client.headers["X-API-Token"] == "token"
    assert BridgeClient(FakeSession(FakeResponse()), "http://b").headers == {
        "Accept": "application/json"}


def test_status_parses_json_and_sends_token():
    session = FakeSession(FakeResponse(payload={"connected": True}))
    client = BridgeClient(session, "http://bridge:8099", "t")
    assert run(client.status()) == {"connected": True}
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", "http://bridge:8099/api/status")
    assert kwargs["headers"]["X-API-Token"] == "t"
    assert kwargs["timeout"].total == 15.0


def test_error_status_mapping():
    client_401 = BridgeClient(
        FakeSession(FakeResponse(401, payload={"detail": "unauthorized"})),
        "http://b", "")
    with pytest.raises(BridgeAuthError):
        run(client_401.status())

    client_503 = BridgeClient(
        FakeSession(FakeResponse(503, payload={"detail": "offline"})),
        "http://b", "")
    with pytest.raises(BridgeUnavailable):
        run(client_503.status())

    client_400 = BridgeClient(
        FakeSession(FakeResponse(400, payload={"detail": "bad request"})),
        "http://b", "")
    with pytest.raises(BridgeError, match="bad request"):
        run(client_400.status())


def test_empty_and_text_responses():
    no_content = BridgeClient(FakeSession(FakeResponse(204)), "http://b")
    assert run(no_content._request("DELETE", "/api/rule/r1")) is None

    plain = BridgeClient(FakeSession(FakeResponse(
        200, text="line", content_type="text/plain")), "http://b")
    assert run(plain._request("GET", "/api/logs")) == "line"


def test_rule_ids_are_url_encoded_and_payloads_built():
    session = FakeSession(FakeResponse(payload={"ok": True}))
    client = BridgeClient(session, "http://b")
    run(client.rule_detail("rule/1"))
    assert session.calls[-1][1] == "http://b/api/rule/rule%2F1"

    run(client.save_rule("r 2", [{"id": "n1"}], {"enable": False}, "名称"))
    assert session.calls[-1][:2] == (
        "PUT", "http://b/api/rule/r%202")
    assert session.calls[-1][2]["json"] == {
        "nodes": [{"id": "n1"}], "cfg": {"enable": False}, "name": "名称"}

    run(client.toggle_rule("r1", False))
    assert session.calls[-1][2]["json"] == {"enable": False}


def test_list_unwrapping_and_defaults():
    client = BridgeClient(
        FakeSession(FakeResponse(payload={
            "devices": {"d1": {"name": "灯"}},
            "rules": [{"id": "r1"}],
            "variables": {"v1": {"type": "number"}},
        })), "http://b")
    assert run(client.devices()) == {"d1": {"name": "灯"}}
    assert run(client.rules()) == [{"id": "r1"}]
    assert run(client.variables("rule_r1")) == {"v1": {"type": "number"}}

    empty = BridgeClient(
        FakeSession(FakeResponse(payload={})), "http://b")
    assert run(empty.devices()) == {}
    assert run(empty.rules()) == []
    assert run(empty.variables()) == {}


def test_connect_includes_optional_gateway_url():
    session = FakeSession(FakeResponse(payload={"ok": True}))
    client = BridgeClient(session, "http://b")
    run(client.connect("123456"))
    assert session.calls[-1][2]["json"] == {"passcode": "123456"}
    run(client.connect("123456", "http://gw"))
    assert session.calls[-1][2]["json"] == {
        "passcode": "123456", "gateway_url": "http://gw"}


def test_events_stream_parses_sse_and_ignores_noise():
    lines = [
        b"\n",
        b": keepalive\n",
        b"event: push\n",
        b'data: {"kind": "ready", "seq": 0}\n',
        b"data: not-json\n",
        b'data: {"kind": "push", "seq": 1}\n',
        b"",
    ]
    client = BridgeClient(FakeSession(FakeResponse(lines=lines)), "http://b")

    async def collect():
        return [item async for item in client.events()]

    assert run(collect()) == [
        {"kind": "ready", "seq": 0},
        {"kind": "push", "seq": 1},
    ]


def test_events_stream_error_mapping():
    unauth = BridgeClient(FakeSession(FakeResponse(401)), "http://b")

    async def collect_unauth():
        return [item async for item in unauth.events()]

    with pytest.raises(BridgeAuthError):
        run(collect_unauth())

    broken = BridgeClient(FakeSession(FakeResponse(502)), "http://b")

    async def collect_broken():
        return [item async for item in broken.events()]

    with pytest.raises(BridgeUnavailable):
        run(collect_broken())
