---
name: mijia-geek-bridge
description: >-
  通过 mijia-geek-bridge 的 REST/SSE 接口只读审计、预览和创建米家自动化极客版规则。
  适用于需要读取设备/规则/变量、生成规则节点图、保存前校验、保存后读回验证的任务。
metadata:
  author: mijia-geek-bridge
  version: "2.3.0"
  license: MIT
---

# 米家极客版 Bridge Skill

## 目标

用 AI 完成米家自动化极客版规则的**只读审计、只读预览、规则生成、保存前校验、保存后读回**。
官方网页编辑器仍是最终人工确认入口；Bridge 只提供 REST/SSE。

## 前置条件

- 已部署 `mijia-geek-bridge`，并能访问其 REST API。
- 如 Bridge 设置了 `API_TOKEN`，所有 `/api/*` 请求（除 `/api/health`）都要带：
  - `X-API-Token: <token>` 或 `Authorization: Bearer <token>`
- 登录码由人在 Bridge Web UI 或 `POST /api/connect` 输入；**不要**把登录码写进日志、提示词或代码。

## MCP 方式

Bridge 自带 `bridge/app/mcp_server.py`，可直接作为 MCP 服务：

```bash
# stdio
python3 bridge/app/mcp_server.py --url http://127.0.0.1:8099 --token <token>

# 只读；写入需显式开启
MCP_ALLOW_WRITE=1 python3 bridge/app/mcp_server.py --allow-write ...
```

MCP 只读工具：`bridge_status`、`list_devices`、`list_rules`、`get_rule`、
`list_variables`、`list_variable_scopes`、`get_variable`、`get_logs`、
`get_logs_all`、`get_official_editor_url`。

写入工具（`MCP_ALLOW_WRITE=1`）：`save_rule`、`toggle_rule`、`delete_rule`、
`set_variable`、`create_variable`、`delete_variable`、`refresh_data`。
登录码工具默认不注册；仅 stdio 且同时开启 `MCP_ALLOW_WRITE=1` 与 `MCP_ALLOW_PASSCODE=1` 时可用。

## 只读接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/status` | 连接状态、统计、`push_seq`、`gateway_web_url` |
| GET | `/api/devices` | 设备列表（含 did/name/model/modelName/roomName/urn/online） |
| GET | `/api/rules` | 规则列表（id/name/enabled/createTime/updateTime） |
| GET | `/api/rule/{id}` | 规则节点图 `{nodes: [...]}` |
| GET | `/api/logs?page=0` | 单页日志 |
| GET | `/api/logs/all?pages=3` | 多页日志 |
| GET | `/api/variables?scope=global` | 变量列表 |
| GET | `/api/variable/scopes` | 变量作用域 |
| GET | `/api/variable/{id}?scope=global` | 单个变量值 |
| GET | `/api/events` | SSE 推送流 |

## 写入接口

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/connect` | 输入一次性登录码 `{passcode}` |
| PUT | `/api/rule/{id}` | 保存规则 `{nodes, cfg, name}` |
| POST | `/api/rule/{id}/toggle` | 启停 `{enable}` |
| DELETE | `/api/rule/{id}` | 删除规则 |
| PUT | `/api/variable/{id}` | 写变量 `{scope, value}` |
| POST | `/api/variables` | 创建变量 |
| DELETE | `/api/variable/{id}?scope=global` | 删除变量 |
| POST | `/api/refresh` | 强制刷新缓存 |

## 规则节点结构

> ⚠️ **本节是唯一正确的结构参考。** 早期版本的示例（数组连线、`dtype:"bool"`、
> `operator:"eq"`、`value` 作触发值、`params` 作输出、`seconds` 作延时）**全部
> 与网关实际要求不符**，照抄会被 `-10002` 拒绝。下列结构已在真机写入成功。

一份完整可用的最小规则：

```json
{
  "rule_id": "ai_demo_20261110",
  "nodes": [
    {
      "id": "n1",
      "type": "deviceInput",
      "cfg": {
        "urn": "urn:miot-spec-v2:device:occupancy-sensor:0000A0BF:linp-hb01:3:0000C824",
        "version": 0,
        "pos": {"x": 80, "y": 120, "width": 250, "height": 110}
      },
      "props": {
        "did": "1000000001",
        "siid": 2,
        "piid": 1,
        "dtype": "int",
        "operator": "=",
        "v1": 1,
        "preload": false
      },
      "inputs": {},
      "outputs": {"output": ["n2.trigger"]}
    },
    {
      "id": "n2",
      "type": "deviceOutput",
      "cfg": {
        "urn": "urn:miot-spec-v2:device:light:0000A001:seomsh-wy0a01:1:0000C802",
        "version": 0,
        "pos": {"x": 430, "y": 120, "width": 250, "height": 110}
      },
      "props": {"did": "1000000002", "siid": 2, "piid": 1, "value": true},
      "inputs": {"trigger": null},
      "outputs": {"output": []}
    }
  ],
  "cfg": {
    "id": "ai_demo_20261110",
    "enable": false,
    "uiType": "graph",
    "userData": {
      "name": "示例规则",
      "lastUpdateTime": 1710000000000,
      "transform": {"x": 0, "y": 0, "scale": 1, "rotate": 0}
    }
  }
}
```

### 八条硬性要求

| 字段 | ❌ 写了会被拒 | ✅ 正确写法 |
|---|---|---|
| 连线 | `[["n2", "trigger"]]` | `["n2.trigger"]`（`"目标.端口"` **字符串**） |
| 节点 `cfg` | 只有 `urn`/`pos` | 必须含 `cfg.version: 0` |
| `inputs` | `{}` | 必须声明：`deviceOutput`→`{"trigger":null}`，`delay`→`{"input":null}` |
| `dtype` | `uint8` / `bool` / `float32` | `int` / `float` / `boolean` / `string` |
| `operator` | `eq` / `ne` / `gt` | `=` `!=` `>` `<` `>=` `<=` `between` `include` |
| 触发值 | `value: 1` | `v1: 1`（另需 `preload: false`） |
| 输出值 | `params: [true]` | `value: true` |
| 延时 | `{"seconds": 120}` | `{"timeout": 120000}`（**毫秒**）+ `cfg.unit/value` |

### 端口对照（连线右侧必须匹配目标 `inputs` 的键）

| 节点类型 | 输入端口 |
|---|---|
| `deviceInput` / `deviceInputSetVar` / `onLoad` / `alarmClock` | 无 |
| `deviceOutput` | `trigger` |
| `delay` / `deviceGet` / `deviceGetSetVar` / `varGet` / `logicNot` / `counter` | `input` |
| `condition` | `trigger` + `condition` |
| `logicAnd` / `logicOr` / `eventSequence` | `input1` + `input2` |
| `statusLast` | `trigger` |

> 最易错：**`delay` 的输入端口是 `input` 而不是 `trigger`。**

### 节点 ID 限制

只允许字母、数字与 `|`：`n1` ✅、`node|1` ✅、`node_1` ❌、`node-1` ❌。

### operator 与 dtype 的合法组合（网关强校验）

- `int`：`>=` `<=` `=` `!=` `>` `<` `between` `include`，`v1` 必须整数
- `float`：仅 `>` `<` `between`，`v1` 必须数字
- `boolean` / `string`：仅 `=`

### 节点类型清单（25 种，`timer` 不是合法类型）

- 触发：`deviceInput`、`deviceInputSetVar`、`alarmClock`、`onLoad`、`varChange`、`signalOr`
- 条件/逻辑：`condition`、`deviceGet`、`varGet`、`logicAnd`、`logicOr`、`logicNot`、`timeRange`、`statusLast`、`counter`、`onlyNTimes`、`eventSequence`、`modeSwitch`、`register`
- 动作：`deviceOutput`、`deviceGetSetVar`、`varSetNumber`、`varSetString`、`delay`、`loop`
- 注释：`nop`

> Bridge 会在写入前用 `bridge/app/schema.py` 自动归一化上述差异并校验，
> 失败时返回 `400` 与具体原因。**但不要依赖它兜底**——按上表直接写对。

## 安全流程（必须遵守）

1. **先只读**：`GET /api/devices`、`/api/rules`、`/api/rule/{id}`、`/api/variables`。
2. **确认设备能力**：通过设备 `urn` 获取 MIOT Spec（service/property/action 的 siid/piid/aiid）。
3. **生成节点图**：节点 ID 唯一；连线使用 `targetNode.targetPort`；至少一个事件源和至少一个执行节点。
4. **基础校验**：
   - 重复节点 ID；
   - 连线目标节点/输入端口存在；
   - `condition.trigger` / `condition.condition` 有来源；
   - 执行节点有上游事件来源且从事件源可达；
   - 变量引用存在。
5. **新规则默认 `enable: false`**，除非用户明确要求启用。
6. **保存**：`PUT /api/rule/{id}`，建议规则 ID 使用 `rule_<毫秒时间戳>`。
7. **读回验证**：保存后再次 `GET /api/rule/{id}`，对比节点/连线。
8. **不要**覆盖或删除未知规则；需要修改时先导出原规则 JSON。
9. 官方编辑器地址可从 `/api/status.gateway_web_url` 获取，最终由人工在官方页面确认。

## 只读预览

Bridge Web UI 已提供“规则与只读预览”标签页：

- 规则列表来自 `GET /api/rules`；
- 点击“预览”调用 `GET /api/rule/{id}`；
- 前端绘制节点/连线，并做基础结构校验；
- 只读，不提供保存入口；
- 官方编辑器通过 iframe 嵌入（`gateway_web_url`），仍由人工编辑。

## 可复用开源项目（MIT）

- [oh-my-sage](https://github.com/allocnode/oh-my-sage)：协议、AI Skill、规则文本格式。
- [mijia-geek-assistant-fnnas](https://github.com/609293964/mijia-geek-assistant-fnnas)：RulePlan / compiler / node-schema / graph-lint / GraphPanel。
- [mijia-geek-automation-skills](https://github.com/lixinyu02/mijia-geek-automation-skills)：规则导出/解析/安全流程。
- [mijiaflow](https://github.com/xmx-emm/mijiaflow)：变更计划、备份、回滚。

复制 MIT 代码时请保留版权声明与许可证。

## 示例

```bash
BRIDGE=http://192.168.1.10:8099
TOKEN='change-me'

curl -H "X-API-Token: $TOKEN" "$BRIDGE/api/devices"
curl -H "X-API-Token: $TOKEN" "$BRIDGE/api/rules"
curl -H "X-API-Token: $TOKEN" "$BRIDGE/api/rule/rule_123456"

# 保存（新规则建议 enable=false）
curl -X PUT "$BRIDGE/api/rule/rule_1750000000000" \
  -H "X-API-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"nodes":[...],"cfg":{"id":"rule_1750000000000","enable":false,"uiType":"graph","userData":{"name":"测试规则","lastUpdateTime":0,"transform":{"x":0,"y":0,"scale":1,"rotate":0}}}}'
```
