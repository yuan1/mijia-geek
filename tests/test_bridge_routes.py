"""Bridge REST 路由集成测试（使用 TestClient + 假网关）。"""
from __future__ import annotations


from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


class FakeGateway:
    connected = True

    def __init__(self):
        self.calls = []

    async def get_dev_list(self):
        return {"d1": {"name": "灯", "online": True}}

    async def get_graph_list(self):
        return [{"id": "r1", "name": "规则1", "enabled": True,
                 "updateTime": 1}]

    async def set_graph(self, rule_id, nodes, cfg):
        self.calls.append(("set_graph", rule_id, nodes, cfg))

    async def change_graph_config(self, rule_id, enable, user_data):
        self.calls.append(("toggle", rule_id, enable, user_data))

    async def delete_graph(self, rule_id):
        self.calls.append(("delete", rule_id))

    async def get_graph(self, rule_id):
        return {"nodes": []}

    async def get_log(self, page=0):
        return f"line{page}"

    async def get_var_list(self, scope="global"):
        return {"v1": {"type": "number", "value": 1}}

    async def get_var_value(self, scope, var_id):
        return 26

    async def get_var_scope_list(self):
        return ["global"]

    async def set_var_value(self, scope, var_id, value):
        self.calls.append(("set_var", scope, var_id, value))

    async def create_var(self, scope, var_id, var_type, value, name):
        self.calls.append(("create_var", scope, var_id, var_type, value, name))

    async def delete_var(self, scope, var_id):
        self.calls.append(("delete_var", scope, var_id))


def test_rest_routes_with_fake_gateway():
    fake = FakeGateway()
    main.state.gateway = fake
    main.state.connected = True
    main.state.devices = {"d1": {"name": "灯", "online": True}}
    main.state.rules = [{"id": "r1", "name": "规则1", "enabled": True,
                         "updateTime": 1}]

    with TestClient(main.app) as client:
        assert client.get("/api/devices").json()["count"] == 1
        assert client.get("/api/rules").json()["count"] == 1
        assert client.get("/api/rule/r1").json() == {"nodes": []}

        resp = client.put("/api/rule/r2",
                          json={"nodes": [], "cfg": {"enable": False}})
        assert resp.status_code == 200

        assert client.post("/api/rule/r1/toggle",
                           json={"enable": False}).status_code == 200
        assert client.delete("/api/rule/r1").status_code == 200

        assert client.get("/api/logs/all?pages=2").json()["log"] == \
            "line0\nline1"
        assert client.get("/api/variable/scopes").json()["scopes"] == \
            ["global"]
        assert client.get("/api/variable/v1").json()["value"] == 26
        assert client.put("/api/variable/v1",
                          json={"value": 27}).status_code == 200
        assert client.post("/api/variables",
                           json={"var_id": "v2",
                                 "var_type": "number"}).status_code == 200
        assert client.delete("/api/variable/v1").status_code == 200


def test_rest_auth_and_disconnected():
    main.state.gateway = None
    main.state.connected = False
    with TestClient(main.app) as client:
        assert client.get("/api/rules").status_code == 503
        assert client.get("/api/health").status_code == 200

        main.API_TOKEN = "secret"
        try:
            assert client.get("/api/status").status_code == 401
            assert client.get("/api/status",
                              headers={"X-API-Token": "secret"}).status_code == 200
        finally:
            main.API_TOKEN = ""


def test_sse_ready_event():
    main.state.connected = False
    with TestClient(main.app) as client:
        with client.stream("GET", "/api/events") as resp:
            assert resp.status_code == 200
            lines = resp.iter_lines()
            assert next(lines) == "retry: 5000"
            assert next(lines) == ""
            ready = next(lines)
            assert ready.startswith("data: ")
            assert '"kind": "ready"' in ready


def test_ui_assets_and_editor_url():
    with TestClient(main.app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "static/app.js" in index.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/style.css").status_code == 200
        status = client.get("/api/status").json()
        assert "gateway_web_url" in status
        assert status["gateway_web_url"].startswith("http")


def test_status_payload_counts_and_editor_url(monkeypatch):
    monkeypatch.setattr(main.state, "gateway", FakeGateway())
    monkeypatch.setattr(main.state, "connected", True)
    monkeypatch.setattr(main.state, "gateway_url", "http://192.168.1.10:8099")
    monkeypatch.setattr(main.state, "devices", {
        "d1": {"name": "灯", "online": True},
        "d2": {"name": "插座", "online": False},
    })
    monkeypatch.setattr(main.state, "rules", [
        {"id": "r1", "enabled": True},
        {"id": "r2", "enabled": False},
    ])
    monkeypatch.setattr(main.state, "push_seq", 5)

    with TestClient(main.app) as client:
        status = client.get("/api/status").json()

    assert status["connected"] is True
    assert status["device_count"] == 2
    assert status["online_count"] == 1
    assert status["rule_count"] == 2
    assert status["enabled_count"] == 1
    assert status["push_seq"] == 5
    assert status["gateway_web_url"] == "http://192.168.1.10:8086/"


def test_connect_route_validation_and_result(monkeypatch):
    async def fail_connect(code, gateway_url=None):
        return False, "登录码已过期"

    monkeypatch.setattr(main.service, "connect", fail_connect)
    with TestClient(main.app) as client:
        assert client.post("/api/connect",
                           json={"passcode": "abc"}).status_code == 400
        assert client.post("/api/connect", json={
            "passcode": "123456", "gateway_url": "ftp://gw",
        }).status_code == 400
        main.state.last_connect_at = 0.0
        response = client.post("/api/connect", json={"passcode": "123456"})
        assert response.status_code == 502
        assert "过期" in response.json()["detail"]

    async def ok_connect(code, gateway_url=None):
        return True, ""

    monkeypatch.setattr(main.service, "connect", ok_connect)
    monkeypatch.setattr(main.state, "devices", {"d1": {"online": True}})
    monkeypatch.setattr(main.state, "rules", [{"id": "r1"}])
    main.state.last_connect_at = 0.0
    with TestClient(main.app) as client:
        response = client.post("/api/connect", json={"passcode": "123456"})
    assert response.status_code == 200
    assert response.json()["device_count"] == 1
    assert response.json()["rule_count"] == 1


def test_refresh_route_updates_state(monkeypatch):
    fake = FakeGateway()
    monkeypatch.setattr(main.state, "gateway", fake)
    monkeypatch.setattr(main.state, "connected", True)
    monkeypatch.setattr(main.state, "devices", {})
    monkeypatch.setattr(main.state, "rules", [])

    with TestClient(main.app) as client:
        response = client.post("/api/refresh")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "device_count": 1,
                               "rule_count": 1}


def test_logs_single_page_and_legacy_variable(monkeypatch):
    fake = FakeGateway()
    monkeypatch.setattr(main.state, "gateway", fake)
    monkeypatch.setattr(main.state, "connected", True)

    with TestClient(main.app) as client:
        assert client.get("/api/logs?page=1").json() == {
            "page": 1, "log": "line1"}
        response = client.post("/api/variable",
                               json={"var_id": "v9", "value": 8})
        assert response.status_code == 200

    assert fake.calls[-1] == ("set_var", "global", "v9", 8)


def test_route_error_mapping(monkeypatch):
    class BrokenGateway(FakeGateway):
        async def get_graph(self, rule_id):
            raise RuntimeError("graph boom")

        async def get_dev_list(self):
            raise RuntimeError("devices boom")

    monkeypatch.setattr(main.state, "gateway", BrokenGateway())
    monkeypatch.setattr(main.state, "connected", True)

    with TestClient(main.app) as client:
        detail = client.get("/api/rule/r1")
        assert detail.status_code == 502
        assert "graph boom" in detail.json()["detail"]

        refresh = client.post("/api/refresh")
        assert refresh.status_code == 502
        assert "devices boom" in refresh.json()["detail"]


def test_bearer_token_auth_and_health_bypass(monkeypatch):
    monkeypatch.setattr(main, "API_TOKEN", "secret")
    monkeypatch.setattr(main.state, "gateway", None)
    monkeypatch.setattr(main.state, "connected", False)

    with TestClient(main.app) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/health").status_code == 200
        assert client.get(
            "/api/status",
            headers={"Authorization": "Bearer secret"},
        ).status_code == 200


def test_state_broadcast_increments_seq():
    queue = main.state.subscribe()
    try:
        seq = main.state.publish_push({"kind": "push", "summary": "按钮"})
        event = queue.get_nowait()
    finally:
        main.state.unsubscribe(queue)

    assert event["seq"] == seq
    assert event["kind"] == "push"
    assert main.state.last_push["summary"] == "按钮"
