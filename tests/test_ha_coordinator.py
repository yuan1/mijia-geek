"""HA 集成 coordinator 测试（不依赖 Home Assistant 运行时）。

用最小桩替换 DataUpdateCoordinator，验证轮询数据归一化、错误映射、
SSE 事件处理与登录码流程。
"""
from __future__ import annotations

import asyncio

import pytest

from ha_env import (
    DISPATCHED,
    ConfigEntryAuthFailed,
    UpdateFailed,
    load_integration_module,
)

bridge = load_integration_module("bridge")
const = load_integration_module("const")
coordinator_mod = load_integration_module("coordinator")

MijiaGeekCoordinator = coordinator_mod.MijiaGeekCoordinator


class FakeHass:
    def __init__(self):
        self.tasks = []
        self.bus = self

    def async_create_task(self, coro):
        self.tasks.append(coro)
        coro.close()
        return None

    def async_fire(self, event_type, data=None):
        self.events = getattr(self, "events", [])
        self.events.append((event_type, data))


class FakeClient:
    def __init__(self):
        self.status_payload = {
            "connected": True,
            "last_log": "日志行",
            "last_push": {"kind": "push", "seq": 7},
            "push_seq": 7,
        }
        self.devices_payload = {"d1": {"name": "灯", "online": True}}
        self.rules_payload = [
            {"id": "r1", "name": "规则1", "enabled": True},
            {"id": "r2", "name": "规则2", "enabled": False},
        ]
        self.variables_payload = {"v1": {"type": "number", "value": 1}}
        self.status_exc = None
        self.details_exc = None
        self.connect_exc = None
        self.connect_calls = []
        self.toggle_calls = []
        self.refresh_calls = 0
        self.detail_calls = 0

    async def status(self):
        if self.status_exc:
            raise self.status_exc
        return dict(self.status_payload)

    async def devices(self):
        self.detail_calls += 1
        if self.details_exc:
            raise self.details_exc
        return dict(self.devices_payload)

    async def rules(self):
        if self.details_exc:
            raise self.details_exc
        return list(self.rules_payload)

    async def variables(self, scope="global"):
        if self.details_exc:
            raise self.details_exc
        return dict(self.variables_payload)

    async def connect(self, passcode, gateway_url=None):
        if self.connect_exc:
            raise self.connect_exc
        self.connect_calls.append(passcode)
        return {"ok": True}

    async def toggle_rule(self, rule_id, enable):
        self.toggle_calls.append((rule_id, enable))
        return {"ok": True}

    async def refresh(self):
        self.refresh_calls += 1
        return {"ok": True}


def make(client=None):
    client = client or FakeClient()
    return MijiaGeekCoordinator(
        FakeHass(), client, "http://bridge:8099", 60), client


def test_update_data_normalizes_counts_and_dispatches_new_rules():
    DISPATCHED.clear()
    coordinator, _ = make()

    data = asyncio.run(coordinator._async_update_data())

    assert data["devices"] == {"d1": {"name": "灯", "online": True}}
    assert data["device_count"] == 1
    assert data["online_count"] == 1
    assert data["rule_count"] == 2
    assert data["enabled_count"] == 1
    assert data["var_count"] == 1
    assert data["push_count"] == 7
    assert data["connected"] is True
    assert coordinator.last_log == "日志行"
    assert coordinator.last_push == {"kind": "push", "seq": 7}
    assert coordinator.known_rule_ids == {"r1", "r2"}
    assert [signal for signal, _ in DISPATCHED] == [const.SIGNAL_NEW_RULE]

    # 规则集合没有变化时不应再次广播
    asyncio.run(coordinator._async_update_data())
    assert len(DISPATCHED) == 1


def test_update_data_error_mapping():
    offline = FakeClient()
    offline.status_exc = bridge.BridgeUnavailable("down")
    coordinator, _ = make(offline)
    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())

    unauthorized = FakeClient()
    unauthorized.status_exc = bridge.BridgeAuthError("bad token")
    coordinator, _ = make(unauthorized)
    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(coordinator._async_update_data())


def test_detail_failure_keeps_cached_data_and_marks_disconnected():
    client = FakeClient()
    client.details_exc = bridge.BridgeUnavailable("just dropped")
    coordinator, _ = make(client)
    coordinator.devices = {"old": {"name": "旧设备", "online": True}}
    coordinator.rules = [{"id": "old", "enabled": True}]
    coordinator.var_list = {"old_var": {"type": "string", "value": "x"}}

    data = asyncio.run(coordinator._async_update_data())

    assert data["devices"] == {"old": {"name": "旧设备", "online": True}}
    assert data["rules"] == [{"id": "old", "enabled": True}]
    assert data["var_count"] == 1
    assert coordinator.connected is False


def test_disconnected_status_does_not_query_details():
    client = FakeClient()
    client.status_payload = {"connected": False, "push_seq": 0}
    client.details_exc = AssertionError("不应查询明细")
    coordinator, _ = make(client)
    coordinator.devices = {"cached": {"online": False}}

    data = asyncio.run(coordinator._async_update_data())

    assert client.detail_calls == 0
    assert data["devices"] == {"cached": {"online": False}}
    assert data["connected"] is False


def test_handle_event_ready_push_and_connection():
    coordinator, _ = make()

    coordinator.handle_event({"kind": "ready", "seq": 0})
    assert coordinator.listener_updates == 0

    coordinator.handle_event({"kind": "push", "seq": 9, "summary": "按钮"})
    assert coordinator.last_push["summary"] == "按钮"
    assert coordinator.push_count == 9
    assert coordinator.listener_updates == 1
    assert coordinator.hass.events[-1][0] == const.EVENT_PUSH

    # 更旧的 seq 不会把计数回退
    coordinator.handle_event({"kind": "push", "seq": 3})
    assert coordinator.push_count == 9

    coordinator.handle_event({"kind": "connection", "connected": True})
    assert coordinator.connected is True
    assert coordinator.listener_updates == 3
    assert len(coordinator.hass.tasks) == 1


def test_apply_passcode_requires_six_digits():
    coordinator, client = make()
    coordinator.pending_passcode = "123"

    assert asyncio.run(coordinator.apply_passcode()) is False
    assert client.connect_calls == []
    assert coordinator.auth_failed is False


def test_apply_passcode_success_clears_pending_code():
    coordinator, client = make()
    coordinator.pending_passcode = "123456"

    assert asyncio.run(coordinator.apply_passcode()) is True
    assert client.connect_calls == ["123456"]
    assert coordinator.pending_passcode == ""
    assert coordinator.auth_failed is False
    assert coordinator.refresh_requests == 1


def test_apply_passcode_failure_flags_auth():
    coordinator, client = make()
    client.connect_exc = bridge.BridgeError("登录码无效")
    coordinator.pending_passcode = "123456"

    assert asyncio.run(coordinator.apply_passcode()) is False
    assert coordinator.auth_failed is True
    assert coordinator.connected is False
    assert coordinator.pending_passcode == "123456"


def test_rule_and_bridge_actions_trigger_refresh():
    coordinator, client = make()

    asyncio.run(coordinator.async_set_rule_enabled("r1", False))
    assert client.toggle_calls == [("r1", False)]
    assert coordinator.refresh_requests == 1

    asyncio.run(coordinator.async_refresh_bridge())
    assert client.refresh_calls == 1
    assert coordinator.refresh_requests == 2
