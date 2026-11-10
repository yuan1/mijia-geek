"""规则节点图 schema —— 与网关真实要求对齐的归一化与校验。

本模块集中处理「文档写法」与「网关真实结构」之间的差异。
这些差异是通过逆向官方极客版编辑器前端（`192.168.1.100` 的
`checkWebRule` / `checkWebNode`）实测确认的，**不是猜测**：

===================================  ==========================================
网关要求                              说明
===================================  ==========================================
连线 ``["n2.trigger"]``                是 ``"目标节点.输入端口"`` 字符串，
                                      不是 ``["n2", "trigger"]`` 数组
节点 ``cfg.version``                   必须是整数（迁移计数，新节点从 0 开始），
                                      缺失会报 ``Invalid cfg.version``
节点 ``inputs``                        必须声明端口，不能是空对象：
                                      deviceOutput → ``{"trigger": null}``，
                                      delay → ``{"input": null}``
``props.dtype``                        是 ``int/float/boolean/string``，
                      **不是** miot 的 ``uint8``/``bool``/``float32``
``props.operator``                     ``=`` ``!=`` ``>`` ``<`` ``>=`` ``<=``
                                      ``between`` ``include``，不是 ``eq``/``ne``
触发条件用 ``v1``（+ ``preload``）     不是 ``value``
执行输出用 ``value``                    不是 ``params``
delay 用 ``props.timeout``（**毫秒**）  不是 ``seconds``，另需 ``cfg.unit/value``
===================================  ==========================================

调用 :func:`normalize_graph` 可以让「文档写法」被自动修正为网关可接受的结构，
调用 :func:`validate_graph` 可在写入前拿到具体、可读的错误信息。
"""
from __future__ import annotations

import math
from typing import Any

# 网关支持的节点类型（来自官方前端 `Pr` 表）
NODE_TYPES: frozenset[str] = frozenset({
    "alarmClock", "condition", "counter", "delay", "deviceGet", "deviceInput",
    "deviceGetSetVar", "deviceInputSetVar", "deviceOutput", "eventSequence",
    "logicAnd", "logicNot", "logicOr", "loop", "modeSwitch", "nop", "onLoad",
    "onlyNTimes", "register", "signalOr", "statusLast", "timeRange",
    "varChange", "varGet", "varSetNumber", "varSetString",
})

# 有事件输入的「源」节点类型
SOURCE_TYPES: frozenset[str] = frozenset({
    "deviceInput", "deviceInputSetVar", "alarmClock", "onLoad",
    "varChange", "signalOr",
})

# 各类型必须声明的输入端口（未被连线指向时也需要存在）
INPUT_PORTS: dict[str, tuple[str, ...]] = {
    "deviceInput": (),
    "deviceInputSetVar": (),
    "onLoad": (),
    "alarmClock": (),
    "deviceOutput": ("trigger",),
    "deviceGet": ("input",),
    "deviceGetSetVar": ("input",),
    "delay": ("input",),
    "condition": ("trigger",),
    "logicAnd": ("input1", "input2"),
    "logicOr": ("input1", "input2"),
    "logicNot": ("input",),
    "counter": ("input",),
    "statusLast": ("trigger",),
    "eventSequence": ("input1", "input2"),
    "varGet": ("input",),
    "varSetNumber": ("input",),
    "varSetString": ("input",),
}

# 执行类终端节点
SINK_TYPES: frozenset[str] = frozenset({
    "deviceOutput", "deviceGetSetVar", "varSetNumber", "varSetString",
})

# dtype 归一：miot 规范类型 -> 网关内部类型
_DTYPE_MAP: dict[str, str] = {
    "bool": "boolean",
    "boolean": "boolean",
    "int": "int",
    "uint8": "int", "uint16": "int", "uint32": "int", "uint64": "int",
    "int8": "int", "int16": "int", "int32": "int", "int64": "int",
    "float": "float", "float32": "float", "double": "float",
    "string": "string",
}
DTYPES: tuple[str, ...] = ("int", "float", "boolean", "string")

# operator 归一：常见别名 -> 网关写法
_OPERATOR_MAP: dict[str, str] = {
    "eq": "=", "equal": "=", "==": "=", "=": "=",
    "ne": "!=", "notEqual": "!=", "unequal": "!=", "!=": "!=",
    "gt": ">", "more": ">", ">": ">",
    "lt": "<", "less": "<", "<": "<",
    "ge": ">=", "gte": ">=", "moreEqual": ">=", ">=": ">=",
    "le": "<=", "lte": "<=", "lessEqual": "<=", "<=": "<=",
    "between": "between",
    "include": "include", "in": "include",
}
INT_OPERATORS: tuple[str, ...] = (">=", "<=", "=", "!=", ">", "<", "between", "include")
TIME_UNITS: tuple[str, ...] = ("ms", "s", "min", "hour")


def normalize_dtype(value: Any) -> str:
    """把 miot 规范类型（uint8/bool/...）转成网关内部类型。"""
    return _DTYPE_MAP.get(str(value).lower(), str(value))


def normalize_operator(value: Any, dtype: str | None = None) -> str:
    """把 eq/ne/gt/... 等别名转成网关写法（= != > ...）。"""
    mapped = _OPERATOR_MAP.get(str(value), str(value))
    return mapped


def _parse_target(value: Any) -> tuple[str, str] | None:
    """接受 ``"n2.trigger"`` 或 ``["n2", "trigger"]``，统一成元组。"""
    if isinstance(value, str) and "." in value:
        target, port = value.split(".", 1)
        if target and port:
            return target, port
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return str(value[0]), str(value[1])
    return None


def normalize_graph(graph: dict) -> tuple[dict, list[str]]:
    """把「文档写法」的节点图就地修正为网关可接受的结构。

    返回 ``(graph, changes)``，``changes`` 记录做过的修正，便于日志与排错。
    本函数**幂等**：对已合规的图再调用不会有任何变化。
    """
    changes: list[str] = []
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return graph, changes

    # 1) 连线统一成 "目标.端口" 字符串
    for node in nodes:
        if not isinstance(node, dict):
            continue
        outputs = node.get("outputs")
        if not isinstance(outputs, dict):
            continue
        for port, targets in list(outputs.items()):
            if not isinstance(targets, list):
                continue
            fixed: list[str] = []
            for target in targets:
                parsed = _parse_target(target)
                if parsed is None:
                    fixed.append(target)
                    continue
                joined = f"{parsed[0]}.{parsed[1]}"
                if joined != target:
                    changes.append(f"{node.get('id')}: 连线 {target!r} → {joined!r}")
                fixed.append(joined)
            outputs[port] = fixed

    # 2) 补 cfg.version
    for node in nodes:
        if not isinstance(node, dict):
            continue
        cfg = node.get("cfg")
        if not isinstance(cfg, dict):
            continue
        if not isinstance(cfg.get("version"), int) or isinstance(cfg.get("version"), bool):
            cfg["version"] = 0
            changes.append(f"{node.get('id')}: 补 cfg.version=0")

    # 3) 补必需的输入端口声明
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type")
        required = INPUT_PORTS.get(node_type, ())
        if not required:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            inputs = {}
            node["inputs"] = inputs
        for port in required:
            if port not in inputs:
                inputs[port] = None
                changes.append(f"{node.get('id')}: 补输入端口 {port}")

    # 3.5) 端口名纠正：旧文档一律写 "trigger"，但各类型端口不同
    #      （delay 是 input、logicAnd 是 input1/input2）。
    #      仅当目标类型只有一个必需输入端口、且原端口未被声明时才纠正，
    #      避免掩盖真正的连线错误。
    by_id = {node.get("id"): node for node in nodes if isinstance(node, dict)}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        outputs = node.get("outputs")
        if not isinstance(outputs, dict):
            continue
        for port, targets in list(outputs.items()):
            if not isinstance(targets, list):
                continue
            for index, target in enumerate(targets):
                parsed = _parse_target(target)
                if parsed is None:
                    continue
                target_id, target_port = parsed
                target_node = by_id.get(target_id)
                if not isinstance(target_node, dict):
                    continue
                declared = target_node.get("inputs")
                if not isinstance(declared, dict) or target_port in declared:
                    continue
                required = INPUT_PORTS.get(target_node.get("type"), ())
                if len(required) == 1:
                    corrected = f"{target_id}.{required[0]}"
                    targets[index] = corrected
                    changes.append(
                        f"{node.get('id')}: 端口 {target!r} → {corrected!r}"
                        f"（{target_node.get('type')} 的输入端口是 {required[0]!r}）")

    # 4) props 字段归一
    for node in nodes:
        if not isinstance(node, dict):
            continue
        props = node.get("props")
        if not isinstance(props, dict):
            continue
        node_type = node.get("type")
        if node_type in ("deviceInput", "deviceInputSetVar", "deviceGet",
                         "deviceGetSetVar", "deviceOutput"):
            if "dtype" in props:
                new_dtype = normalize_dtype(props["dtype"])
                if new_dtype != props["dtype"]:
                    changes.append(f"{node.get('id')}: dtype {props['dtype']} → {new_dtype}")
                    props["dtype"] = new_dtype
            if "operator" in props:
                new_op = normalize_operator(props["operator"])
                if new_op != props["operator"]:
                    changes.append(f"{node.get('id')}: operator {props['operator']} → {new_op}")
                    props["operator"] = new_op
        # 触发条件：value -> v1
        if node_type in ("deviceInput", "deviceInputSetVar") and "piid" in props:
            if "v1" not in props and "value" in props:
                props["v1"] = props.pop("value")
                changes.append(f"{node.get('id')}: value → v1")
            if "preload" not in props:
                props["preload"] = False
                changes.append(f"{node.get('id')}: 补 preload=False")
        # 执行动作：params -> value
        if node_type == "deviceOutput" and "piid" in props:
            if "value" not in props and "params" in props:
                params = props.pop("params")
                props["value"] = params[0] if isinstance(params, list) and params else params
                changes.append(f"{node.get('id')}: params → value")
        # delay: seconds -> timeout(ms)
        if node_type == "delay":
            if "timeout" not in props:
                if "seconds" in props:
                    props["timeout"] = int(float(props.pop("seconds")) * 1000)
                    changes.append(f"{node.get('id')}: seconds → timeout(ms)")
                elif "ms" in props:
                    props["timeout"] = int(props.pop("ms"))
                    changes.append(f"{node.get('id')}: ms → timeout")
            cfg = node.get("cfg")
            if isinstance(cfg, dict):
                if not isinstance(cfg.get("unit"), str):
                    cfg["unit"] = "ms"
                    changes.append(f"{node.get('id')}: 补 cfg.unit=ms")
                if not isinstance(cfg.get("value"), (int, float)):
                    cfg["value"] = props.get("timeout", 0)
                    changes.append(f"{node.get('id')}: 补 cfg.value")

    return graph, changes


def validate_graph(graph: dict, specs: dict[str, dict] | None = None) -> list[str]:
    """按网关真实 schema 校验节点图，返回错误列表（空列表表示通过）。

    ``specs`` 可选，形如 ``{did: {"urn":..., "spec": {"2": {"properties": {...}}}}}``。
    提供时才会校验 did/urn/siid/piid/aiid 与读写权限。
    """
    errors: list[str] = []
    nodes = graph.get("nodes")

    if not isinstance(nodes, list) or not nodes:
        return ["nodes 必须是非空数组"]
    # 顶层 cfg 可选：MCP 的 lint 只传 nodes；传入时才校验
    cfg = graph.get("cfg")
    if cfg is not None and not isinstance(cfg, dict):
        return ["cfg 必须是对象"]

    by_id: dict[str, dict] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"nodes[{index}] 必须是对象")
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            errors.append(f"nodes[{index}] 缺少字符串 id")
            continue
        # 网关只允许字母、数字与 `|`
        if any(not (ch.isalnum() or ch == "|") for ch in node_id):
            errors.append(f"{node_id}: id 含非法字符（仅允许字母数字与 |）")
        if node_id in by_id:
            errors.append(f"节点 ID 重复: {node_id}")
        by_id[node_id] = node

    for node_id, node in by_id.items():
        node_type = node.get("type")
        if node_type not in NODE_TYPES:
            errors.append(f"{node_id}: 不支持的节点类型 {node_type!r}")
            continue
        for field in ("props", "inputs", "outputs", "cfg"):
            if not isinstance(node.get(field), dict):
                errors.append(f"{node_id}: {field} 必须是对象")
        node_cfg = node.get("cfg") if isinstance(node.get("cfg"), dict) else {}
        if not isinstance(node_cfg.get("version"), int) or isinstance(node_cfg.get("version"), bool):
            errors.append(f"{node_id}: cfg.version 必须是整数")

        # 连线
        outputs = node.get("outputs") if isinstance(node.get("outputs"), dict) else {}
        for port, targets in outputs.items():
            if not isinstance(targets, list):
                errors.append(f"{node_id}: outputs.{port} 必须是数组")
                continue
            for target in targets:
                parsed = _parse_target(target)
                if parsed is None:
                    errors.append(
                        f"{node_id}: 连线必须是 \"目标节点.输入端口\" 字符串，实际 {target!r}")
                    continue
                if not isinstance(target, str):
                    errors.append(f"{node_id}: 连线必须是字符串 \"{parsed[0]}.{parsed[1]}\"，"
                                  f"实际 {target!r}（数组形式会被网关拒绝）")
                target_id, target_port = parsed
                target_node = by_id.get(target_id)
                if target_node is None:
                    errors.append(f"{node_id}: 连线指向不存在的节点 {target_id}")
                    continue
                declared = target_node.get("inputs")
                if not isinstance(declared, dict) or target_port not in declared:
                    errors.append(
                        f"{node_id}: 目标 {target_id}({target_node.get('type')}) "
                        f"未在 inputs 中声明端口 {target_port!r}")

        # 必需输入端口
        required = INPUT_PORTS.get(node_type, ())
        declared_inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        for port in required:
            if port not in declared_inputs:
                errors.append(f"{node_id}({node_type}) 缺少必需输入端口 {port!r}")

        _validate_props(node_id, node, errors, specs or {})

    # 可达性
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for node_id, node in by_id.items():
        outputs = node.get("outputs") if isinstance(node.get("outputs"), dict) else {}
        for targets in outputs.values():
            if not isinstance(targets, list):
                continue
            for target in targets:
                parsed = _parse_target(target)
                if parsed and parsed[0] in by_id:
                    adjacency[node_id].append(parsed[0])

    sources = [node_id for node_id, node in by_id.items()
               if node.get("type") in SOURCE_TYPES]
    if not sources:
        errors.append("缺少事件源节点（deviceInput/onLoad/alarmClock 等）")
    reachable: set[str] = set(sources)
    stack = list(sources)
    while stack:
        current = stack.pop()
        for nxt in adjacency.get(current, []):
            if nxt not in reachable:
                reachable.add(nxt)
                stack.append(nxt)
    unreachable = [node_id for node_id in by_id if node_id not in reachable]
    if unreachable:
        errors.append(f"存在不可达节点: {unreachable}")

    for node_id, node in by_id.items():
        if node.get("type") not in SINK_TYPES:
            continue
        outputs = node.get("outputs") if isinstance(node.get("outputs"), dict) else {}
        has_out = any(isinstance(v, list) and v for v in outputs.values())
        incoming = any(node_id in targets for targets in adjacency.values())
        if not has_out and not incoming:
            errors.append(f"执行节点 {node_id} 没有上游来源")

    return errors


def _validate_props(node_id: str, node: dict, errors: list[str],
                    specs: dict[str, dict]) -> None:
    """校验 props 与设备能力（可选）。"""
    node_type = node.get("type")
    props = node.get("props") if isinstance(node.get("props"), dict) else {}

    if node_type == "delay":
        timeout = props.get("timeout")
        if not isinstance(timeout, int) or isinstance(timeout, bool):
            errors.append(f"{node_id}: delay 需要整数 props.timeout（毫秒）")
        node_cfg = node.get("cfg") if isinstance(node.get("cfg"), dict) else {}
        if node_cfg.get("unit") not in TIME_UNITS:
            errors.append(f"{node_id}: delay 的 cfg.unit 必须是 {TIME_UNITS} 之一")
        return

    if node_type not in ("deviceInput", "deviceInputSetVar", "deviceGet",
                         "deviceGetSetVar", "deviceOutput"):
        return

    did = props.get("did")
    if not isinstance(did, str):
        errors.append(f"{node_id}: props.did 必须是字符串")
        return
    siid = props.get("siid")
    if not isinstance(siid, int) or isinstance(siid, bool):
        errors.append(f"{node_id}: props.siid 必须是整数")
        return

    if node_type in ("deviceInput", "deviceInputSetVar") and "piid" in props:
        dtype = props.get("dtype")
        if dtype not in DTYPES:
            errors.append(f"{node_id}: dtype 必须是 {DTYPES} 之一，"
                          f"实际 {dtype!r}（不能用 miot 的 uint8/bool）")
        if "operator" not in props:
            errors.append(f"{node_id}: 属性触发缺少 operator")
        elif dtype == "int" and props.get("operator") not in INT_OPERATORS:
            errors.append(f"{node_id}: int 类型的 operator 必须是 {INT_OPERATORS} 之一，"
                          f"实际 {props.get('operator')!r}")
        if "v1" not in props:
            errors.append(f"{node_id}: 属性触发缺少 v1（不是 value）")
        if props.get("operator") == "between" and "v2" not in props:
            errors.append(f"{node_id}: operator=between 需要同时给出 v2")

    if node_type == "deviceOutput" and "piid" in props:
        if "value" not in props and "scope" not in props:
            errors.append(f"{node_id}: deviceOutput 需要 value（或变量引用 scope）")

    if not specs:
        return
    spec = specs.get(did)
    if spec is None:
        errors.append(f"{node_id}: did={did} 不在设备列表中")
        return
    node_cfg = node.get("cfg") if isinstance(node.get("cfg"), dict) else {}
    urn = node_cfg.get("urn")
    if urn and urn != spec.get("urn"):
        errors.append(f"{node_id}: cfg.urn 与 did={did} 的实际 urn 不一致")
    capabilities = spec.get("spec") or {}
    if not capabilities:
        # 没有该设备的 miot-spec 能力表时跳过能力校验（只校验 did 与 urn）
        return
    service = capabilities.get(str(siid))
    if service is None:
        errors.append(f"{node_id}: siid={siid} 在该设备上不存在")
        return
    if "piid" in props:
        prop = (service.get("properties") or {}).get(str(props["piid"]))
        if prop is None:
            errors.append(f"{node_id}: piid={props['piid']} 在 siid={siid} 下不存在")
            return
        access = prop.get("access") or []
        if node_type in ("deviceInput", "deviceInputSetVar") and not ({"read", "notify"} & set(access)):
            errors.append(f"{node_id}: piid={props['piid']} 不可读且不推送，无法作为触发")
        if node_type == "deviceOutput" and "write" not in access:
            errors.append(f"{node_id}: piid={props['piid']} 不可写")
        value_range = prop.get("range")
        v1 = props.get("v1")
        if (node_type in ("deviceInput", "deviceInputSetVar")
                and isinstance(value_range, list) and len(value_range) >= 2
                and isinstance(v1, int) and not isinstance(v1, bool)
                and not (value_range[0] <= v1 <= value_range[1])):
            errors.append(f"{node_id}: v1={v1} 超出取值范围 {value_range[:2]}")
        out_value = props.get("value")
        if (node_type == "deviceOutput"
                and isinstance(value_range, list) and len(value_range) >= 2
                and isinstance(out_value, (int, float)) and not isinstance(out_value, bool)
                and not math.isnan(out_value)
                and not (value_range[0] <= out_value <= value_range[1])):
            errors.append(f"{node_id}: value={out_value} 超出取值范围 {value_range[:2]}")
    elif "aiid" in props:
        action = (service.get("actions") or {}).get(str(props["aiid"]))
        if action is None:
            errors.append(f"{node_id}: aiid={props['aiid']} 在 siid={siid} 下不存在")
    else:
        errors.append(f"{node_id}: 必须提供 piid 或 aiid")


def graph_summary(graph: dict) -> dict:
    """给规则图一个轻量摘要，便于日志排查。"""
    nodes = graph.get("nodes") or []
    edges = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for targets in (node.get("outputs") or {}).values():
            if isinstance(targets, list):
                edges += len(targets)
    return {
        "nodes": len(nodes),
        "edges": edges,
        "types": sorted({n.get("type") for n in nodes
                         if isinstance(n, dict) and n.get("type")}),
    }
