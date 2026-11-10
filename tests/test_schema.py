"""规则 schema 归一化与校验的回归测试。

这些用例对应**实测确认**的网关要求（逆向官方极客版编辑器的
`checkWebRule`/`checkWebNode` 得到），用于防止文档写法回潮：
连线漂回数组、cfg 丢 version、dtype 用 uint8、operator 用 eq、
delay 用 seconds、deviceOutput 用 params —— 这些都会被网关以
``-10002`` 直接拒绝。
"""
from __future__ import annotations

import copy

import pytest

from schema import (
    normalize_dtype,
    normalize_graph,
    normalize_operator,
    validate_graph,
)

LIGHT_URN = "urn:miot-spec-v2:device:light:0000A001:demo:1"
SENSOR_URN = "urn:miot-spec-v2:device:occupancy-sensor:0000A0BF:demo:1"


def _doc_style_graph() -> dict:
    """完全按旧文档写的图（网关会拒绝），用于验证归一化能力。"""
    return {
        "nodes": [
            {"id": "n1", "type": "deviceInput",
             "cfg": {"urn": SENSOR_URN,
                     "pos": {"x": 0, "y": 0, "width": 200, "height": 100}},
             "props": {"did": "s1", "siid": 2, "piid": 1, "dtype": "uint8",
                       "operator": "eq", "value": 1},
             "inputs": {}, "outputs": {"output": [["n2", "trigger"]]}},
            {"id": "n2", "type": "deviceOutput",
             "cfg": {"urn": LIGHT_URN,
                     "pos": {"x": 300, "y": 0, "width": 200, "height": 100}},
             "props": {"did": "l1", "siid": 2, "piid": 1, "params": [True]},
             "inputs": {}, "outputs": {"output": []}},
            {"id": "n3", "type": "deviceInput",
             "cfg": {"urn": SENSOR_URN,
                     "pos": {"x": 0, "y": 200, "width": 200, "height": 100}},
             "props": {"did": "s1", "siid": 2, "piid": 1, "dtype": "uint8",
                       "operator": "eq", "value": 0},
             "inputs": {}, "outputs": {"output": [["n4", "trigger"]]}},
            {"id": "n4", "type": "delay",
             "cfg": {"urn": "", "pos": {"x": 300, "y": 200,
                                        "width": 200, "height": 100}},
             "props": {"seconds": 120},
             "inputs": {}, "outputs": {"output": [["n5", "trigger"]]}},
            {"id": "n5", "type": "deviceOutput",
             "cfg": {"urn": LIGHT_URN,
                     "pos": {"x": 600, "y": 200, "width": 200, "height": 100}},
             "props": {"did": "l1", "siid": 2, "piid": 1, "params": [False]},
             "inputs": {}, "outputs": {"output": []}},
        ]
    }


def _compliant_graph() -> dict:
    """完全合规的图，作为「校验器不该误报」的基线。"""
    graph, _ = normalize_graph(_doc_style_graph())
    return graph


# ---------------------------------------------------------------- 归一化


def test_normalize_rewrites_documented_shape_to_gateway_shape():
    graph, changes = normalize_graph(_doc_style_graph())
    nodes = {node["id"]: node for node in graph["nodes"]}

    # 连线从数组变成 "目标.端口" 字符串
    assert nodes["n1"]["outputs"]["output"] == ["n2.trigger"]
    # 且端口名会被纠正：delay 的输入是 input 而不是 trigger
    assert nodes["n3"]["outputs"]["output"] == ["n4.input"]

    # 节点补 cfg.version
    assert all(node["cfg"]["version"] == 0 for node in graph["nodes"])

    # inputs 声明端口
    assert nodes["n2"]["inputs"] == {"trigger": None}
    assert nodes["n4"]["inputs"] == {"input": None}

    # dtype / operator 归一
    assert nodes["n1"]["props"]["dtype"] == "int"
    assert nodes["n1"]["props"]["operator"] == "="

    # 触发值从 value 变 v1，并补 preload
    assert nodes["n1"]["props"]["v1"] == 1
    assert "value" not in nodes["n1"]["props"]
    assert nodes["n1"]["props"]["preload"] is False

    # 执行值从 params 变 value
    assert nodes["n2"]["props"]["value"] is True
    assert "params" not in nodes["n2"]["props"]

    # delay 从 seconds 变 timeout(ms)
    assert nodes["n4"]["props"]["timeout"] == 120000
    assert nodes["n4"]["cfg"]["unit"] == "ms"

    assert changes, "归一化应记录下修正项"


def test_normalize_is_idempotent():
    graph = _compliant_graph()
    again, changes = normalize_graph(copy.deepcopy(graph))
    assert changes == []
    assert again == graph


def test_normalize_graph_after_fix_validates_clean():
    errors = validate_graph(_compliant_graph())
    assert errors == [], errors


def test_validate_rejects_raw_documented_graph():
    """未归一化的「文档写法」必须被拦下，而不是放行给网关。"""
    errors = validate_graph(_doc_style_graph())
    assert errors
    joined = "; ".join(errors)
    assert "cfg.version" in joined
    assert "数组形式会被网关拒绝" in joined
    assert "uint8" in joined


@pytest.mark.parametrize("raw,expected", [
    ("uint8", "int"), ("uint16", "int"), ("int32", "int"),
    ("bool", "boolean"), ("boolean", "boolean"),
    ("float32", "float"), ("float", "float"), ("string", "string"),
])
def test_normalize_dtype(raw, expected):
    assert normalize_dtype(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("eq", "="), ("equal", "="), ("ne", "!="), ("gt", ">"), ("lt", "<"),
    ("ge", ">="), ("le", "<="), ("moreEqual", ">="), ("lessEqual", "<="),
    ("between", "between"), ("in", "include"),
])
def test_normalize_operator(raw, expected):
    assert normalize_operator(raw) == expected


# ---------------------------------------------------------------- 校验


def test_validate_catches_duplicate_ids():
    graph = copy.deepcopy(_compliant_graph())
    graph["nodes"][1]["id"] = graph["nodes"][0]["id"]
    assert any("节点 ID 重复" in e for e in validate_graph(graph))


def test_validate_catches_unknown_type():
    graph = copy.deepcopy(_compliant_graph())
    graph["nodes"][3]["type"] = "whileLoop"
    assert any("不支持的节点类型" in e for e in validate_graph(graph))


def test_validate_catches_undeclared_input_port():
    graph = copy.deepcopy(_compliant_graph())
    graph["nodes"][1]["inputs"] = {}
    assert any("没有上游来源" in e or "未在 inputs 中声明" in e
               for e in validate_graph(graph))


def test_validate_catches_unreachable_node():
    graph = copy.deepcopy(_compliant_graph())
    graph["nodes"].append({
        "id": "n9", "type": "delay",
        "cfg": {"urn": "", "version": 0,
                "pos": {"x": 0, "y": 400, "width": 100, "height": 60}},
        "props": {"timeout": 1000}, "inputs": {"input": None},
        "outputs": {"output": []},
    })
    assert any("不可达节点" in e for e in validate_graph(graph))


def test_validate_requires_event_source():
    graph = copy.deepcopy(_compliant_graph())
    graph["nodes"] = [n for n in graph["nodes"] if n["type"] != "deviceInput"]
    assert any("缺少事件源节点" in e for e in validate_graph(graph))


def test_validate_catches_between_without_v2():
    graph = copy.deepcopy(_compliant_graph())
    props = graph["nodes"][0]["props"]
    props["operator"] = "between"
    props.pop("v2", None)
    assert any("需要同时给出 v2" in e for e in validate_graph(graph))


def test_validate_catches_bad_delay_timeout():
    graph = copy.deepcopy(_compliant_graph())
    graph["nodes"][3]["props"] = {"seconds": 120}
    assert any("timeout" in e for e in validate_graph(graph))


def test_validate_catches_illegal_node_id_characters():
    graph = copy.deepcopy(_compliant_graph())
    graph["nodes"][0]["id"] = "n-1"
    graph["nodes"][0]["outputs"]["output"] = ["n2.trigger"]
    assert any("非法字符" in e for e in validate_graph(graph))


# ---------------------------------------------------------------- 设备能力


def _specs() -> dict:
    return {
        "s1": {"urn": SENSOR_URN, "spec": {
            "2": {"properties": {
                "1": {"name": "occupancy-status", "format": "uint8",
                      "access": ["read", "notify"], "range": [0, 1, 1]},
            }, "actions": {}, "events": {}},
        }},
        "l1": {"urn": LIGHT_URN, "spec": {
            "2": {"properties": {
                "1": {"name": "on", "format": "bool",
                      "access": ["read", "write", "notify"], "range": None},
                "3": {"name": "color-temperature", "format": "uint16",
                      "access": ["read", "notify"], "range": [2700, 6500, 1]},
            }, "actions": {"1": {"name": "toggle"}}, "events": {}},
        }},
    }


def test_validate_with_specs_accepts_valid_graph():
    assert validate_graph(_compliant_graph(), _specs()) == []


def test_validate_catches_unknown_device():
    graph = copy.deepcopy(_compliant_graph())
    graph["nodes"][0]["props"]["did"] = "nope"
    assert any("不在设备列表" in e for e in validate_graph(graph, _specs()))


def test_validate_catches_unknown_piid():
    graph = copy.deepcopy(_compliant_graph())
    graph["nodes"][0]["props"]["piid"] = 99
    assert any("不存在" in e for e in validate_graph(graph, _specs()))


def test_validate_catches_write_to_readonly_property():
    graph = copy.deepcopy(_compliant_graph())
    graph["nodes"][1]["props"]["piid"] = 3   # color-temperature 只读
    assert any("不可写" in e for e in validate_graph(graph, _specs()))


def test_validate_catches_value_out_of_range():
    graph = copy.deepcopy(_compliant_graph())
    graph["nodes"][0]["props"]["v1"] = 7     # occupancy 范围 0..1
    assert any("超出取值范围" in e for e in validate_graph(graph, _specs()))


def test_validate_skips_capability_checks_without_spec():
    """没有能力表时只校验 did/urn，不应因缺少 spec 而误报。"""
    graph = copy.deepcopy(_compliant_graph())
    specs = {"s1": {"urn": SENSOR_URN, "spec": {}},
             "l1": {"urn": LIGHT_URN, "spec": {}}}
    assert validate_graph(graph, specs) == []
