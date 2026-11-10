# 米家极客版桥接（Mijia Geek Bridge）

通过局域网直连小米中枢网关的「自动化极客版」，提供 REST API、SSE 推送和 Web 配置界面。

**核心价值**：
- 网关长连接由 Bridge 独占维护，HA 重启 / 重载不影响会话；
- HA 集成已重构为 Bridge 的轻量 REST 客户端，不再重复实现协议。

---

## 目录结构

```
bridge/                         # 独立 Docker / Addon 服务
├── config.yaml                 # HA Addon 配置
├── Dockerfile
├── run.sh
├── requirements.txt
└── app/
    ├── main.py                 # FastAPI 入口（lifespan / 鉴权 / 路由挂载）
    ├── config.py               # 环境变量配置
    ├── state.py                # 运行时状态 + SSE 广播
    ├── service.py              # 连接 / 轮询 / 刷新业务逻辑
    ├── api/                    # REST 路由（status/rules/variables/logs/events）
    ├── protocol/               # 网关协议实现（ECJPAKE + AES-GCM + JSON-RPC）
    ├── schema.py               # 规则节点 schema 归一化 + 写入前校验
    ├── mcp_server.py           # MCP 服务（stdio / streamable-http）
    └── static/                 # Web 配置界面
        ├── index.html
        ├── app.js              # 规则只读预览 / 官方编辑器 / AI 提示词
        └── style.css

ha_integration/mijia_geek/      # HA 自定义集成（Bridge REST 客户端）
├── __init__.py                 # setup/unload/迁移/SSE 监听
├── bridge.py                   # aiohttp REST + SSE 客户端
├── coordinator.py              # 轮询与推送事件协调器
├── services.py                 # HA 服务映射
├── entity.py                   # 实体基类（设备信息/unique_id）
├── config_flow.py              # Bridge 地址 + API Token
├── const.py
├── sensor.py / switch.py / button.py / text.py
├── manifest.json
├── services.yaml
├── strings.json
└── translations/

tests/                          # 协议 / REST / MCP / HA 客户端回归测试
skills/                         # AI Skills（Bridge REST 工作流）
docs/                           # 使用 / 部署 / 协议 / 更新日志
```

## Web UI 功能

- **设备**：设备列表、筛选、在线状态。
- **规则与只读预览**：列出规则，点击「预览」调用 `GET /api/rule/{id}`，
  绘制节点/连线并做基础结构校验；不提供保存入口。
- **日志**：按页读取。
- **官方编辑器**：通过 iframe 嵌入 `gateway_web_url`（默认从网关地址推导 `:8086`），
  仍由官方页面负责编辑/保存；提供新窗口打开作为兜底。
- **AI Skills**：展示 Bridge OpenAPI、AI 提示词和 MIT 参考项目入口。

> 官方编辑器是独立的 HTTP 页面。若 Bridge 通过 HTTPS ingress 访问，
> 浏览器可能因混合内容阻止 iframe，此时请使用「新窗口打开」。
> 官方编辑器仍需要单独输入网关登录码。

---

## 快速部署

> 📘 **完整部署指南见 [DEPLOY.md](DEPLOY.md)**（含实测踩坑记录）
> 🕳 **踩坑记录见 [CHANGELOG.md](CHANGELOG.md#部署实测记录2026-11-10)**

### ⚠️ 先看这条：HA 集成填什么地址？

| 部署方式 | 网络模式 | HA 集成 Bridge URL |
|---|---|---|
| **Supervisor Addon（推荐）** | Docker bridge | `http://local-mijia-geek-bridge:8099` |
| 手动 `docker run --network host` | host | `http://127.0.0.1:8099` |

> 💡 **`127.0.0.1` 只在 `--network host` 下有效**。
> addon 默认是 bridge 网络，`127.0.0.1` 会指向 HA 自己，导致
> `Bridge 报告网关未连接`。这是最常见的配置错误。

**验证连通性**：

```bash
docker exec homeassistant curl -s --max-time 4 \
  http://local-mijia-geek-bridge:8099/api/health
# 期望输出: {"ok":true}
```

---

### 方式 A：Supervisor Addon（推荐）

1. 把 `bridge/` 放到 `/mnt/data/supervisor/apps/local/mijia_geek_bridge/`
2. HA → 设置 → 加载项 → 检查更新 → 找到 **Mijia Geek Bridge** → 安装
3. 配置页填写（**`api_token` 必须自建一个长随机串**）：

| 配置项 | 建议值 |
|---|---|
| `gateway_url` | `http://192.168.1.100`（你的网关 IP）|
| `gateway_web_url` | 留空（自动推导 `:8086`）|
| `api_token` | 自建长随机串 |
| `scan_interval` | `60` |
| `mcp_http_port` | `0` |

4. 启动 addon
5. HA → 设置 → 设备与服务 → **添加集成 → 米家极客版**
   - Bridge URL: `http://local-mijia-geek-bridge:8099`
   - API Token: 同上
6. 打开 `http://192.168.1.10:8099` 输 6 位登录码

---

### 方式 B：手动 Docker（备选）

适用于不支持 local addon 的 HAOS 版本（如冬瓜定制版）。

### 1. 上传代码到 HAOS

```bash
# 在能访问 HAOS 的机器上
scp -P 22222 -r bridge root@192.168.1.10:/mnt/data/supervisor/apps/local/
```

### 2. 构建镜像

```bash
ssh -p 22222 root@192.168.1.10
cd /mnt/data/supervisor/apps/local/bridge
docker build -t mijia-geek-bridge:2.3.0 .
```

### 3. 启动容器

```bash
docker run -d --name mijia-geek-bridge \
  --restart unless-stopped \
  --label io.hass.type=app \
  --label io.hass.name=MijiaGeekBridge \
  --label io.hass.version=2.3.0 \
  --label io.hass.arch=amd64 \
  -p 8099:8099 \
  -e GATEWAY_URL=http://192.168.1.100 \
  -e SCAN_INTERVAL=60 \
  -e DATA_DIR=/data \
  -e API_TOKEN=change-me-to-a-long-random-string \
  -e GATEWAY_WEB_URL=http://192.168.1.100:8086/ \
  -v /mnt/data/mijia_geek_bridge:/data \
  mijia-geek-bridge:2.3.0
```

> ⚠️ **必须带 `io.hass.*` 标签**，否则 HAOS 会报「系统不受支持 - 不支持的软件」
> 端口对外暴露时务必设置 `API_TOKEN`；登录码一次性且不落盘，重启后需重新输入。

### 4. 访问 Web 界面

```
http://192.168.1.10:8099
```

输入米家 App 里取到的 6 位登录码即可。
若容器设置了 `API_TOKEN`，Web UI 首次请求会提示输入 Token（保存在浏览器 localStorage）。

---

## 获取登录码

**米家 App → 中枢网关 → 右上角「···」→ 自动化极客版 → 6 位数字**

> ⚠️ **登录码一次性有效**，无论成功还是失败，用过就作废。
> 取码后请立刻使用，不要先做其他测试。

---

## REST API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（无需 Token） |
| GET | `/api/status` | 连接状态 + 统计 + `push_seq` |
| POST | `/api/connect` | 输码连接 `{"passcode":"123456"}` |
| GET | `/api/devices` | 设备列表 |
| GET | `/api/rules` | 规则列表 |
| GET | `/api/rule/{id}` | 规则详情（节点+连线） |
| PUT | `/api/rule/{id}` | 保存规则 `{"nodes":[...],"cfg":{...}}` |
| POST | `/api/rule/{id}/toggle` | 启停规则 `{"enable":true}` |
| DELETE | `/api/rule/{id}` | 删除规则 |
| GET | `/api/logs?page=0` | 执行日志（单页） |
| GET | `/api/logs/all?pages=3` | 连续多页日志 |
| GET | `/api/variables?scope=global` | 变量列表 |
| GET | `/api/variable/scopes` | 变量作用域列表 |
| GET | `/api/variable/{id}?scope=global` | 读取单个变量值 |
| PUT | `/api/variable/{id}` | 写变量 `{"scope":"global","value":26}` |
| POST | `/api/variable` | 兼容旧客户端的写变量接口 |
| POST | `/api/variables` | 创建变量 |
| DELETE | `/api/variable/{id}?scope=global` | 删除变量 |
| POST | `/api/refresh` | 强制刷新数据 |
| GET | `/api/events` | SSE 推送事件流 |

### SSE 推送

`GET /api/events` 返回 `text/event-stream`，每个事件是一行 `data: {json}`：

```json
{"seq": 12, "kind": "property_changed", "device_id": "...", "summary": "..."}
{"seq": 13, "kind": "connection", "connected": true, "last_error": ""}
```

`seq` 单调递增；客户端断线重连后可用 `/api/status` 的 `push_seq` 判断是否漏事件。
`/api/events` 在设置 `API_TOKEN` 时同样需要请求头。

> **鉴权（可选）**：设置环境变量 `API_TOKEN` 后，除 `/api/health` 外所有 `/api/*` 接口都必须带
> `X-API-Token: <token>` 或 `Authorization: Bearer <token>`。未设置时保持向后兼容。
> Web UI 收到 401 会提示输入 Token。

### 调用示例

```bash
# 如果设置了 API_TOKEN
TOKEN='change-me'

# 连接（登录码一次性，不会保存；重复调用会消耗新码）
curl -X POST http://192.168.1.10:8099/api/connect \
  -H "X-API-Token: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"passcode":"123456"}'

# 查看状态
curl -H "X-API-Token: $TOKEN" http://192.168.1.10:8099/api/status

# 启停规则
curl -X POST http://192.168.1.10:8099/api/rule/rule_123/toggle \
  -H "X-API-Token: $TOKEN" \
  -H 'Content-Type: application/json' -d '{"enable":false}'
```

---

## HA 集成（Bridge REST 客户端）

`ha_integration/mijia_geek/` 已重构为 Bridge 的轻量客户端：

1. 安装/更新 Bridge（独立 Docker 或 Addon），设置 `API_TOKEN`（推荐）；
2. 把 `ha_integration/mijia_geek` 复制到 `/config/custom_components/`；
3. 重启 HA，添加集成「米家极客版」；
4. 填写 **Bridge 地址**（如 `http://192.168.1.10:8099`）和 **API Token**。

集成行为：

| 项目 | 说明 |
|---|---|
| 数据来源 | Bridge REST：`/api/status`、`/api/devices`、`/api/rules`、`/api/variables` |
| 实时推送 | 订阅 `GET /api/events`（SSE） |
| 登录码 | 由 Bridge 管理；HA 重启/重载**不会**消耗新码 |
| 实体 | 设备/在线/规则/启用/变量/日志/推送计数 + 规则 switch + 连接状态 |
| 服务 | 规则 CRUD、日志、变量 CRUD、`set_passcode`、`refresh` 等 |
| 认证 | 使用 Bridge 的 `API_TOKEN`；Token 失效时触发 reauth |

> 旧版直连集成（v2）迁移到 v3 后需要重新填写 Bridge 地址与 Token，
> 因为旧配置里保存的是网关地址，不是 Bridge 地址。

---

## 协议实现说明

小米极客版使用自研加密协议，本项目的 `bridge/app/protocol/gateway.py` 是完整的 Python 实现：

```
1. WebSocket 连接        ws://<网关IP>/centrallinkws/
2. 协议协商             发送 ["passcode"]，服务器回 selected_protocol
3. ECJPAKE 握手          secp256k1 椭圆曲线，四轮交换 + ZKP 零知识证明
4. 会话密钥交换          SESSION_KEY_EXCHANGE，用 ECJPAKE 共享密钥加密
5. 加密通信              AES-128-GCM 分帧，DEFLATE 压缩，JSON-RPC 2.0
```

### 关键实现细节

| 项目 | 说明 |
|---|---|
| 曲线 | secp256k1（纯 Python 实现点运算，无第三方依赖） |
| ZKP hash | 在偏移 207 处写入 name 长度（与官方 JS 一致） |
| RoundTwo | 客户端需跳过 3 字节前缀 |
| 会话密钥 | `client_key[0:16]` 用于发送，`server_key[0:16]` 用于接收 |
| 分帧格式 | `1字节类型 + payload`，类型见下方帧类型表 |
| AES-GCM IV | `nonce(8字节) + counter(4字节LE)` |

### 帧类型

```python
PROTOCOL_LIST = 1
SELECTED_PROTOCOL = 2
SESSION_KEY_EXCHANGE = 3
ERROR = 4
DATA = 5
ECJPAKE_ROUND_ONE = 32
ECJPAKE_ROUND_TWO = 33
```

---

## 已实现的网关 API

| API | 用途 | 状态 |
|---|---|---|
| `getDevList` | 设备列表 | ✅ |
| `getGraphList` | 规则列表 | ✅ |
| `getGraph` | 规则详情 | ✅ |
| `setGraph` | 保存规则 | ✅ |
| `deleteGraph` | 删除规则 | ✅ |
| `changeGraphConfig` | 启停/改名 | ✅ |
| `getLog` | 执行日志 | ✅ |
| `getVarScopeList` | 变量作用域 | ✅ |
| `getVarList` | 变量列表 | ✅ |
| `getVarValue` | 读变量值 | ✅ |
| `setVarValue` | 写变量值 | ✅ |
| `createVar` | 创建变量 | ✅ |
| `deleteVar` | 删除变量 | ✅ |
| `getBackupList` | 备份列表 | ✅（未在 UI 暴露） |

**未实现**（按需求跳过）：备份的创建/删除/下载/恢复相关 8 个 API

### 响应归一化

参考实现的 `result` 形态不完全统一，本项目的 `bridge/app/protocol/gateway.py` 已做归一化，调用方拿到的类型稳定：

| 方法 | 原始 result | 归一化后 |
|---|---|---|
| `getDevList` | `{"devList": {...}}` | `dict[did, device]` |
| `getGraphList` | 数组或 `{"rules": [...]}` | `list[rule]` |
| `getVarList` | `{var_id: {...}}` | `dict`（非 dict 时为空 dict） |
| `getVarValue` | `{"value": ...}` | 标量值 |
| `getVarScopeList` | `{"scopes": [...]}` | `list[str]` |
| `getLog` | 字符串 | 原样字符串，空串表示无更多日志 |

> `getGraph` 返回 `{"nodes": [...]}`；保存规则时需由调用方补充 `cfg`（HA 的 `save_rule` 会自动生成）。

### 规则节点 schema（重要）

网关对节点结构的要求与早期文档差异很大，共有 8 处必顶字段写法不同
（连线必须是 `"目标.端口"` 字符串、节点 `cfg.version`、`inputs` 声明端口、
`dtype` 用 `int/boolean/...`、`operator` 用 `=`/`>`/...、触发值用 `v1`、
输出用 `value`、延时用 `timeout` 毫秒）。

- 完整对照表与可用示例见 [`PROTOCOL.md` 第九章](./PROTOCOL.md)。
- Bridge 在写入前会调用 `bridge/app/schema.py` 自动归一化并校验，
  不通过则返回 `400` + 具体原因，不会把非法结构发给网关。
- 自测：`python -m pytest tests/test_schema.py`。

### 推送事件

网关会主动推送两类事件，服务已支持接收：

| 事件 | 说明 |
|---|---|
| `/push/property_changed` | 设备属性变化 `{did, siid, piid, value}` |
| `/push/event_triggered` | 设备事件触发 `{did, siid, eiid, arguments}` |

---

## 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `GATEWAY_URL` | `http://192.168.1.100` | 网关局域网地址（显式设置时优先于 state.json） |
| `GATEWAY_WEB_URL` | 自动推导 `http://<网关IP>:8086/` | 官方极客版编辑器地址；可在 Addon 选项中覆盖 |
| `API_TOKEN` | 空 | 可选；设置后所有 `/api/*`（除 `/api/health`）需带 `X-API-Token` 或 `Authorization: Bearer` |
| `SCAN_INTERVAL` | `60` | 数据刷新间隔（秒，15–3600） |
| `MCP_HTTP_PORT` | `0`（不启动） | 设置后 Bridge 启动时额外启动 MCP Streamable HTTP 服务 |
| `MCP_ALLOW_WRITE` | `0` | 设为 `1` 才注册 MCP 写入工具 |
| `MCP_ALLOW_PASSCODE` | `0` | 与 `MCP_ALLOW_WRITE=1` 同时设置才注册 `connect_gateway` |
| `DATA_DIR` | `/data` | 状态目录（只保存网关地址） |

> 登录码一次性，**不会**保存到 `state.json`，服务重启后需重新输入。
> `/api/connect`、`/api/status` 等接口在设置 `API_TOKEN` 后需要鉴权。
> HA Addon 选项：`gateway_url` / `gateway_web_url` / `api_token` /
> `scan_interval` / `mcp_http_port` / `mcp_allow_write` / `mcp_allow_passcode`；
> 登录码请在 Web UI 输入，不作为配置项保存。

---

## 开发与测试

```bash
pip install -r requirements-dev.txt   # 运行时依赖 + pytest / aiohttp
pytest -q
```

测试覆盖：ECJPAKE 双端握手、压缩往返、AES-GCM 往返与重放保护、
`call()` 超时后的 pending 清理、URL 解析、关键响应形态归一化、
规则 cfg 构造与变量类型校验、Bridge REST 路由/鉴权/错误映射与 SSE、
MCP 工具注册与调用、HA Bridge 客户端（REST 状态码/SSE）与 coordinator
（轮询归一化/事件/登录码）。

> 测试依赖缺失时直接失败，不再用 `importorskip` 静默跳过。
> HA 平台实体的完整测试需要 `pytest-homeassistant-custom-component`，
> 当前仓库只覆盖不依赖 HA 运行时的客户端与 coordinator 逻辑。

---

## 故障排查

### 「系统不受支持 - 不支持的软件」

**原因**：容器缺少 `io.hass.*` 标签
**解决**：按上面第 3 步的完整 `docker run` 命令重新创建，确保带 4 个标签

### 连接失败 `RoundOne 异常: 4`

**原因**：登录码已过期或被消耗
**解决**：重新取码。注意登录码一次性，失败也会作废

### HA 集成提示「无法连接 Bridge」

**检查顺序**：
1. Bridge 容器/Addon 是否运行；
2. 配置里的 Bridge 地址是否可达（独立 Docker 用 `http://IP:8099`）；
3. 若 Bridge 设置了 `API_TOKEN`，Token 是否一致；
4. 401 → Token 错误；连接拒绝 → 地址/端口错误。

### 数据一直为 0

**检查顺序**：
1. `curl http://192.168.1.10:8099/api/status` 看 `connected` 字段
2. 若 `false`，看 `last_error` 字段
3. 看容器日志：`docker logs mijia-geek-bridge`

### 接口返回 401 `{"detail":"unauthorized"}`

**原因**：服务设置了 `API_TOKEN`，但请求没带 Token。
**解决**：加 `-H "X-API-Token: <token>"`，或在 Web UI 提示框输入 Token。

### 接口返回 503 `网关未连接`

**原因**：没有活动会话，或底层 WebSocket 已断开。
**解决**：重新取一个 6 位登录码，通过 Web UI 或 `POST /api/connect` 连接。
登录码一次性，不要用旧码重试。

---

## MCP 服务

`bridge/app/mcp_server.py` 把 Bridge REST 封装为 MCP 工具，支持：

- **stdio**：适合 Claude Desktop / Codex / Cursor 等本地客户端；
- **streamable-http**：HTTP 客户端，路径 `/mcp`；
- 默认只读；`MCP_ALLOW_WRITE=1` 才注册写入工具；
- 默认不暴露登录码工具；仅 stdio 且同时设置 `MCP_ALLOW_WRITE=1` 与 `MCP_ALLOW_PASSCODE=1` 才注册 `connect_gateway`。

### stdio

```json
{
  "mcpServers": {
    "mijia-geek-bridge": {
      "command": "python3",
      "args": ["/path/to/bridge/app/mcp_server.py"],
      "env": {
        "BRIDGE_URL": "http://127.0.0.1:8099",
        "BRIDGE_API_TOKEN": "change-me",
        "MCP_ALLOW_WRITE": "0"
      }
    }
  }
}
```

Docker 内也可用：

```bash
docker exec -i mijia-geek-bridge python /app/mcp_server.py \
  --url http://127.0.0.1:8099 --token change-me
```

### Streamable HTTP

```bash
python3 bridge/app/mcp_server.py \
  --transport streamable-http --host 127.0.0.1 --port 8098 \
  --url http://127.0.0.1:8099 --token change-me --http-token change-me --allow-write
```

客户端地址：`http://127.0.0.1:8098/mcp`。

或在容器启动时设置：

```bash
-e API_TOKEN=change-me -e MCP_HTTP_PORT=8098 -e MCP_ALLOW_WRITE=0
-p 127.0.0.1:8098:8098
```

Addon 选项：

| 选项 | 默认 | 说明 |
|---|---|---|
| `mcp_http_port` | `0` | `0` 不启动；设为 `8098` 启动 MCP HTTP |
| `mcp_allow_write` | `false` | 是否注册写入工具 |
| `mcp_allow_passcode` | `false` | 是否注册 `connect_gateway` |

> Addon 默认不启动 MCP（`mcp_http_port: 0`）。启用后 `run.sh` 在容器内绑定
> `0.0.0.0:${mcp_http_port}`，并要求 `API_TOKEN` 作为 MCP HTTP Bearer Token；
> 手动运行 `mcp_server.py` 时默认绑定 `127.0.0.1:8098`。
> MCP HTTP 必须带 `Authorization: Bearer <API_TOKEN>`，不要直接暴露公网。
> 写入工具默认关闭。

### 工具列表

只读：`bridge_status`、`list_devices`、`list_rules`、`get_rule`、
`list_variables`、`list_variable_scopes`、`get_variable`、`get_logs`、
`get_logs_all`、`get_official_editor_url`。

写入（需 `MCP_ALLOW_WRITE=1`）：`save_rule`、`toggle_rule`、`delete_rule`、
`set_variable`、`create_variable`、`delete_variable`、`refresh_data`。

登录码（需 `MCP_ALLOW_WRITE=1` + `MCP_ALLOW_PASSCODE=1`）：`connect_gateway`。

---

## AI Skills

`skills/mijia-geek-bridge/SKILL.md` 提供 AI Agent 工作流：

- 只读审计设备/规则/变量/日志；
- 生成规则节点图并通过 `PUT /api/rule/{id}` 保存；
- 保存前做节点/端口/可达性校验，保存后读回验证；
- 新规则默认停用；
- 官方编辑器负责人工最终确认。

使用方式：把 `skills/mijia-geek-bridge` 复制到 `~/.codex/skills/`，或把 `SKILL.md` 贴进其他 Agent 的系统提示。

---

## 已知限制

1. **登录码无法持久化** —— ECJPAKE 前向保密，会话密钥绑定 TCP 连接，断开即失效。Bridge 重启后需要新码；HA 重启不受影响。
2. **Bridge 是单一连接点** —— Bridge 不可用时，HA 实体也不可用；这是“唯一连接持有者”的代价。
3. **网关重启后需要新码** —— 会话彻底失效。
4. **本地 Addon 机制受限** —— 本环境（冬瓜定制版 Supervisor）不加载 `/data/apps/local`；其他环境可按 Addon 方式部署，或使用 Docker。
5. **日志分页方向依赖网关** —— 参考文档只说明 `getLog {num}` 的页码语义；当前把第 0 页最后一行当作“最近日志”，与文档示例的升序一致。若真机倒序，需要调整。
6. **规则画布布局可能被重置** —— `getGraphList` 不返回 `userData`；启停规则时项目会尽量保留已有的 `transform`，否则发送默认值。
7. **旧版 HA 直连配置需要重配** —— v2 → v3 迁移后，需要填写 Bridge 地址与 API Token；旧配置里没有 Bridge 地址。
8. **Bridge 目前是单网关** —— 多网关需要运行多个 Bridge 实例，HA 侧对应多个 config entry（服务调用暂不支持定向）。
9. **未实现参考项目的 MIOT 能力发现/自动规则生成** —— 本项目只负责网关协议与 CRUD，规则构建由调用方完成。
10. **官方编辑器 iframe 受浏览器安全策略影响** —— HTTPS ingress + HTTP 编辑器会被混合内容阻止；网关若发送 `X-Frame-Options`，iframe 也会空白。此时请用「新窗口打开」。
11. **只读预览的校验是前端基础检查** —— 检查节点 ID、端口、连线和可达性，不替代官方编辑器的保存校验。
12. **规则详情不包含 cfg/布局** —— `getGraph` 只返回 nodes；预览使用节点内的 `cfg.pos`（若有），否则自动布局。

---

## 参考

协议实现参考开源项目 [oh-my-sage](https://github.com/allocnode/oh-my-sage)
（原版为 Node.js 实现，本项目为独立 Python 实现）
