# 更新日志与踩坑记录

记录开发过程中遇到的所有问题及解决方案，供后续维护参考。

---

## 部署实测记录（2026-11-10）

> 本章节记录在真实环境（HAOS 2026.8.3 + 冬瓜定制版 + 小米中枢网关）
> 实际部署时遇到的问题，**这些问题在官方文档里没有，都是现场踩坑总结**。

### 🕳️ 坑 1：alpine 基础镜像构建失败（阻塞性）

**现象**：

```
ERROR: ResolutionImpossible

The conflict is caused by:
    jsonschema 4.26.0 depends on rpds-py>=0.25.0
    jsonschema 4.25.1 depends on rpds-py>=0.7.1
    ...
```

**根因**：依赖链 `mcp` → `jsonschema` → `rpds-py`。
`rpds-py` 是 Rust 项目，**alpine 上没有预编译 wheel**，pip 尝试源码编译失败后
回退到旧版本，与其他包版本约束冲突，最终无解。

**解决**：Dockerfile 基础镜像从 alpine 换成 Debian：

```dockerfile
# ❌ 失败
ARG BUILD_FROM=python:3.12-alpine

# ✅ 成功
ARG BUILD_FROM=python:3.12-slim-bookworm
FROM ${BUILD_FROM}

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*
```

Debian 系有 `manylinux` 预编译 wheel，直接安装：

```
Downloading websockets-15.0.1-cp312-cp312-manylinux_2_5_x86_64...whl
Downloading cryptography-50.0.2-cp311-abi3-manylinux_2_34_x86_64.whl
Downloading pydantic_core-2.50.4-cp312-cp312-manylinux2014_x86_64.whl
```

**体积对比**：alpine 失败 → slim-bookworm 成功，最终镜像 **197 MB**。

---

### 🕳️ 坑 2：`127.0.0.1:8099` 连不上（阻塞性）⭐

**现象**：HA 集成配置成 `http://127.0.0.1:8099`，一切请求都失败，
报 `BridgeUnavailable: Bridge 报告网关未连接`。

**根因**：addon 跑在 Docker **bridge 网络**，不是 host 网络。
在 HA 容器内部，`127.0.0.1` 指向 **HA 自己**，不是 bridge 容器。

**实测三种地址**：

| 地址 | 从 HA 容器内访问 | 结论 |
|---|---|---|
| `http://127.0.0.1:8099` | ❌ FAIL | HA 自己，没有 8099 服务 |
| `http://172.30.0.2:8099` | ✅ ok | 容器 IP，但重启可能变 |
| `http://local-mijia-geek-bridge:8099` | ✅ ok | **容器名别名，推荐** |

**解决**：HA 集成里填 `http://local-mijia-geek-bridge:8099`。

> 💡 **口诀**：`127.0.0.1` 只在 `--network host` 模式下有效；
> bridge 模式必须用容器名或容器 IP。

**验证命令**：

```bash
docker exec homeassistant curl -s --max-time 4 \
  http://local-mijia-geek-bridge:8099/api/health
# 期望: {"ok":true}
```

---

### 🕳️ 坑 3：两个 bridge 容器抢端口（隐蔽性极高）⭐

**现象**：用户在 Web UI 明明看到「已连接」，但 HA 一直报
`Bridge 报告网关未连接`，`/api/status` 返回 `connected: false`。

**根因**：同时存在两个容器：

```
app_local_mijia_geek_bridge   Up 9 minutes   8098-8099/tcp   ← Supervisor 管的（用户连的这个）
mijia-geek-bridge             Up 28 minutes                  ← 之前手动跑的（抢占了 8099）
```

手动那个用了 `--network host`，独占 8099；Supervisor 那个是 bridge 网络，
端口映射到 8099 但被占，所以 Web UI 访问到的是「手动容器」。
用户以为连上了，实际连的和我查的不是同一个实例。

**解决**：

```bash
# 确认有几个
 docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -i mijia

# 删掉手动创建的，只留 Supervisor 管理的
docker rm -f mijia-geek-bridge
docker restart app_local_mijia_geek_bridge
```

**教训**：部署前先清理旧容器，`docker ps` 确认只有一个实例。

---

### 🕳️ 坑 4：冬瓜定制版 Supervisor 不加载 local addon

**现象**：代码放到 `/mnt/data/supervisor/apps/local/` 后，
`ha apps` 列表里完全看不到。

**排查结果**：

| 检查项 | 结果 |
|---|---|
| `store.json` 含 `"local"` | ✅ 有 |
| 目录结构 / config.yaml | ✅ 正确，YAML 可解析 |
| Supervisor 源码有 `RepositoryLocal` | ✅ 有 |
| 启动日志的 `Loading app` | ❌ 只有 git/core，**无 local** |
| 日志中 `apps/local` 出现次数 | **0** |
| `ha apps` 的 repository 种类 | `['7eca76cc', 'core', 'ddc1b768']` — 无 local |

**尝试过的方案**（全部失败）：

1. `ha addons reload` — 只刷新 git 仓库
2. `touch config.yaml` 改 mtime — `update()` 返回 True 但没触发加载
3. 重启 Supervisor — 仍不加载
4. `ha store remove local` — 报错「不能移除内置仓库」
5. 做成 Git 仓库 + `ha store add file://...` — 报错「需要 URL」
6. 起 git daemon + `ha store add git://127.0.0.1/...` — Supervisor 容器内 127.0.0.1 连不上宿主机

**结论**：该 HAOS 版本需改用**手动 Docker 部署**，或换支持 local addon 的官方 HAOS。

---

### 🕳️ 坑 5：HA 报「系统不受支持 - 不支持的软件」

**原因**：手动 `docker run` 的容器不带 `io.hass.*` 标签，HA 判定为外部软件。

**解决**：启动时加 4 个标签：

```bash
--label io.hass.type=app
--label io.hass.name=MijiaGeekBridge
--label io.hass.version=2.2.0
--label io.hass.arch=amd64
```

> 注意：即使加了标签，手动容器仍可能在「系统」页显示警告，
> 因为 Supervisor 没接管它。要彻底消除需用 addon 方式部署。

---

### 🕳️ 坑 6：登录码一次性，bridge 重启即失效

**现象**：重启 bridge 容器后，HA 集成全部报「网关未连接」。

**查状态**：

```json
{"connected": false, "has_passcode": false, ...}
```

**根因**（协议层限制）：

- 小米登录码**只能用一次**（成功或失败都作废）
- ECJPAKE 前向保密 → 会话密钥绑定 TCP 连接
- 断开即失效，**无法持久化**

**应对**：

1. Bridge 重启后，去 Web UI 或 `POST /api/connect` 重新输码
2. **但 HA 重启不影响 Bridge**（容器独立运行，连接保持）

这是设计上的取舍：安全（不落盘）vs 便利（需重输）。

---

### ✅ 最终成功的配置

| 项目 | 值 |
|---|---|
| 部署方式 | Supervisor Addon |
| 容器名 | `app_local_mijia_geek_bridge` |
| 网络模式 | bridge（IP `172.30.0.2`）|
| HA 集成 Bridge URL | `http://local-mijia-geek-bridge:8099` |
| 网关地址 | `http://192.168.1.100` |
| 基础镜像 | `python:3.12-slim-bookworm` |
| 镜像体积 | 197 MB |
| 构建耗时 | 约 3-5 分钟 |

---

## 2.3.0 规则 schema 校正 + 写入前归一化（2026-11-10）

> 一句话：**旧文档里的规则示例写不进网关。** 本次按真机把结构改对，
> 并把「自动修正 + 写入前校验」内置到 Bridge 与 MCP。

### 🕳️ 坑 7：规则节点 schema 与文档不符（阻塞性）⭐⭐

**现象**：用 `PUT /api/rule/{id}` 提交按文档写的节点图，网关返回：

```
-10002: Invalid node parameter:n1,Invalid connection: output->n2,trigger
```

哪怕把 `did/siid/piid` 反复核对了也一样失败。

**定位方式**：官方极客版编辑器就跑在网关本机（`http://<网关>/`）。
把它前端 JS 拉下来，找到它提交前的自校验函数
`Dr.checkWebRule` → `Pr[type].checkWebNode`，即是网关的真实 schema。
（校验器在 `gateway.*.js`，节点模板表在 `ai-config-v5.*.js`。）

**8 处差异**（全部实测确认）：

| # | 字段 | 文档写法 | 网关要求 |
|---|---|---|---|
| 1 | 连线 | `[["n2","trigger"]]` | `["n2.trigger"]`（**字符串**） |
| 2 | 节点 `cfg` | `{urn,pos}` | 必须含 `version`（整数，新节点 0） |
| 3 | `inputs` | `{}` | 必须声明端口（否则报 `Not property nor event` 类错误） |
| 4 | `dtype` | `uint8`/`bool` | `int`/`float`/`boolean`/`string` |
| 5 | `operator` | `eq`/`ne` | `=`/`!=`/`>`/`<`/`>=`/`<=`/`between`/`include` |
| 6 | 触发值 | `value` | `v1`（+`preload`） |
| 7 | 输出值 | `params:[true]` | `value: true` |
| 8 | 延时 | `{seconds:120}` | `props.timeout:120000`（毫秒）+ `cfg.unit/value` |

另外还有两个容易忽略的点：

- **`delay` 的输入端口是 `input`，不是 `trigger`**；`logicAnd/Or` 是 `input1/input2`；
  `condition` 是 `trigger`+`condition`。
- **节点 `id` 只允许字母、数字与 `|`**，`node_1`、`node-1` 都会被拒。

**解决**：新增 `bridge/app/schema.py`：

- `normalize_graph()`：把上表「文档写法」自动改成网关写法，**幂等**，
  并返回改动清单（会写进日志，便于排查「我明明写的 A 怎么变成 B」）。
- `validate_graph()`：按网关 schema 逐项校验（含 `dtype`/`operator` 组合、
  `between` 需 `v2`、端口声明、可达性、执行节点上游来源、ID 字符集），
  给出可直接读的错误信息。

接入点：

- `PUT /api/rule/{id}`：先归一化再校验，不通过直接 `400` + 具体原因，
  不再把必然失败的请求发给网关。
- MCP `save_rule`：走同一套归一化 + 校验，返回里新增 `normalized` 字段。
- `lint_nodes()`：改为复用 `validate_graph`，MCP 与 REST 提示一致。

**验证**：把最初失败的「文档写法」图喂给 `normalize_graph()`，修正 25 处后
与真机写入成功后读回的规则**逐字段完全一致**；`tests/test_schema.py`
新增 37 条用例（含上述 8 个坑的回归），全仓 88 项测试通过。

### 文档同步

- `docs/PROTOCOL.md` 第九章重写为「实测校正版」，含端口对照表与 ID 限制。
- `skills/mijia-geek-bridge/SKILL.md` 的节点结构章节重写。
- Bridge Web UI 的「复制 AI 提示词」同步修正（这份提示词正是最初写错的源头）。
- 修正文档里的 `timer` → 正确类型是 `alarmClock`。

---

## 2.2.0 MCP 服务（2026-11-10）

- 新增 `bridge/app/mcp_server.py`：
  - 基于 `mcp>=2.0` 的 `MCPServer`；
  - 封装 Bridge REST，支持 stdio / SSE / Streamable HTTP；
  - 默认只读工具；`MCP_ALLOW_WRITE=1` 才注册写入工具；
  - `connect_gateway` 仅在 stdio 且同时开启 `MCP_ALLOW_WRITE` + `MCP_ALLOW_PASSCODE` 时注册；
  - MCP HTTP 必须配置 Bearer Token（容器内使用 `API_TOKEN`）；
  - `save_rule` 保存前读取现有规则并合并 `userData`/`transform`，保存后读回；
  - 新规则默认 `enable=false`，更新时保留原有启用状态。
- Bridge 启动脚本支持可选 `MCP_HTTP_PORT`，监督 MCP/uvicorn 子进程并转发信号。
- Addon 新增 `api_token`、`gateway_web_url`、`mcp_*` 选项。
- `requirements.txt` 增加 `mcp>=2.0,<3` 与 `httpx`。
- Web UI「AI Skills」标签补充 MCP 使用说明。
- `skills/mijia-geek-bridge/SKILL.md` 增加 MCP 工具与安全说明。
- 文档新增 MCP 部署、客户端配置、安全默认值。
- HA SSE 事件流正常结束也等待 5 秒再重连，避免 Bridge 重启时忙循环。
- 测试不再使用 `importorskip` 静默跳过；新增 MCP 工具调用、HA Bridge
  客户端/coordinator、REST 状态/鉴权/错误映射/SSE 广播回归测试。
- 版本升至 2.2.0。

---

## 2.1.0 只读预览 / 官方编辑器 / AI Skills（2026-11-10）

- Bridge Web UI 拆分静态资源：`static/index.html`、`static/app.js`、`static/style.css`。
- 新增「规则与只读预览」：
  - 调用 `GET /api/rule/{id}` 绘制节点/连线；
  - 前端基础校验：重复节点 ID、连线目标/端口、condition 输入、执行节点可达性；
  - 只读，不提供保存入口。
- 新增「官方编辑器」标签页：
  - iframe 加载 `GATEWAY_WEB_URL`（默认从 `GATEWAY_URL` 推导 `:8086`）；
  - 支持新窗口打开，规避混合内容 / `X-Frame-Options`；
  - Addon 新增 `gateway_web_url` 选项。
- 新增「AI Skills」标签页：
  - Bridge OpenAPI / SSE / 官方编辑器入口；
  - 可复制 AI 规则生成提示词；
  - 列出 MIT 参考项目。
- 新增 `skills/mijia-geek-bridge/SKILL.md`：Bridge REST 只读审计、规则生成、校验、保存/读回工作流。
- `/api/status` 增加 `gateway_web_url`。
- 版本升至 2.1.0。

---

## 2.0.0 Bridge + HA 合并重构（2026-11-10）

### 2.0.1 架构

- **Bridge 成为唯一网关连接持有者**：HA 集成不再直接连接网关。
- `ha_integration/mijia_geek/` 重构为 Bridge REST + SSE 客户端：
  - `bridge.py`：aiohttp REST/SSE 客户端；
  - `coordinator.py`：轮询 `/api/status`、`/api/devices`、`/api/rules`、`/api/variables`；
  - `services.py`：HA 服务映射到 Bridge；
  - `entity.py`：共享设备信息/unique_id；
  - 删除 HA 侧重复的 `gateway.py`。
- Bridge 代码按职责拆分：
  - `app/main.py`：FastAPI 入口；
  - `app/config.py` / `state.py` / `service.py`；
  - `app/api/`：REST 路由；
  - `app/protocol/`：协议实现。
- 目录重命名：`mijia-geek-bridge/` → `bridge/`，`ha-integration/` → `ha_integration/`。

### 2.0.2 新增 Bridge API

- `PUT /api/rule/{id}`：保存规则（`setGraph`）。
- `GET /api/logs/all?pages=N`：多页日志。
- `GET /api/variable/scopes`。
- `GET /api/variable/{id}`：读取单个变量。
- `PUT /api/variable/{id}`：写变量。
- `POST /api/variables`：创建变量。
- `DELETE /api/variable/{id}`：删除变量。
- `GET /api/events`：SSE 推送流；`/api/status` 增加 `push_seq`。
- 保留旧 `POST /api/variable` 兼容接口。

### 2.0.3 破坏性变更

- HA 集成 config flow `VERSION = 3`：旧 v2 直连条目迁移后需要重新配置 Bridge 地址 + API Token。
- HA 实体 unique_id / device identifier 改为基于 Bridge URL；旧实体需要通过集成重配后重新生成。
- `ha_integration/mijia_geek/gateway.py`、`tools/sync_gateway.py` 删除。
- HA 集成的 `manifest.json` 不再声明 `websockets` / `cryptography`。

---

## 1.0.3 代码审查修复（2026-11-10）

### 0.1 登录码生命周期

- 登录码一次性、用完即废：**不再持久化到 `state.json` / config entry**，重启后必须重新输入。
  旧版 config entry 会在启动时自动迁移（v1 → v2）并删除已保存的登录码。
- 桥接服务不再在启动时自动用旧码连接；HA 集成也不再因保存码而触发 reload（此前 reload 会立刻关闭刚建立的连接）。
- 新连接成功后才关闭旧连接，输错码不会破坏现有可用会话。

### 0.2 可靠性

- `MijiaGateway.connect()` 握手失败时回收 WebSocket / 接收任务。
- `call()` 超时或发送失败时清理 `pending` Future，避免长期泄漏。
- 桥接轮询单次 RPC 失败不再直接把连接标记为断开；先做健康检查。
- 参数长度、曲线点范围、ZKP 标记均增加校验。

### 0.3 安全

- 日志中不再出现完整登录码（仅长度 / 掩码）；移除遗留的 `MJG_TRACE` 调试日志。
- HA 登录码文本实体只回显 `•` 掩码，真实码仅存在 coordinator 内存，不进入状态机/recorder。
- Web UI 所有网关数据经 HTML 转义，修复存储型 XSS。
- 桥接 REST API 支持 `API_TOKEN`（`X-API-Token` / `Bearer`）；addon 不再映射宿主机端口、不再申请未使用的 `homeassistant_api` / `hassio_api` 权限，面板改为仅管理员可见。
- `/data/state.json` 原子写入并设置 0600。

### 0.4 其他

- 修复空规则列表后新增规则不再创建 switch 实体的问题。
- 推送到达时立即刷新实体；推送计数沿 coordinator 累计。
- 启停规则时保留原有 `userData` / `transform`。
- 删除无效的 `binary_sensor.py`、重复的 `get_var_list`、未使用的依赖与导入。
- 增加 `tests/` 回归测试（`tools/sync_gateway.py` 已在 2.0.0 协议合并后移除）。

### 0.5 与参考实现（oh-my-sage）的业务逻辑对齐

- `ws_url` 按参考实现保留显式端口与路径前缀，而不是无条件丢弃。
- `getGraphList` 兼容 `result` 为数组或 `{"rules": [...]}` 两种形态（参考实现同样做了兜底）。
- `getVarValue` 解包 `{"value": ...}`，`getVarScopeList` 解包 `{"scopes": [...]}`，避免服务/事件里出现双层嵌套。
- `createVar` 的 `var_type` 限定为参考文档支持的 `number` / `string`，并按类型给 `value` 默认值（number=0，string=""）；移除未文档化的 `bool`。
- `save_rule` 强制 `cfg.id == rule_id`，避免顶层 id 与 cfg 内 id 不一致导致网关拒绝。
- 增加 AES-GCM key/nonce 长度与压缩帧最小长度校验，和参考实现保持一致。

### 0.6 文档同步

- `docs/README.md`：新增 API Token 鉴权、响应归一化表、开发与测试、401/503 排查；更新目录结构与已知限制。
- `docs/PROTOCOL.md`：补充 URL 解析规则、request future 清理、晚到响应处理、API 返回类型与依赖说明。
- `docs/DEPLOY.md`：补充 Token 请求头、REST Sensor/RESTful Command 鉴权、ingress 入口说明、升级后回归检查。
- 删除已废弃的 `binary_sensor.py` 文档条目。

---

## 一、协议层踩坑

### 1.1 ECJPAKE ZKP 哈希偏移

**问题**：ZKP 验证一直失败，`ZKP verification failed for g_x3`

**原因**：`name` 长度必须写入**偏移 207**，而不是顺序拼接：

```python
# ❌ 错误
data = enc(g) + enc(v) + enc(pub) + struct.pack(">I", len(name)) + name

# ✅ 正确
data = enc(g) + enc(v) + enc(pub) + b"\x00\x00\x00\x00" + name
data = data[:207] + struct.pack(">I", len(name)) + data[211:]
```

**来源**：对照 `oh-my-sage/ref/gateway_client.js` 的 `zkpHash()` 发现。

---

### 1.2 第二轮响应的 3 字节前缀

**问题**：服务器 RoundTwo 响应长度是 168 而不是 165，解析错位

**原因**：客户端必须跳过前 3 字节：

```python
offset = 3 if role == "client" else 0
g_s_peer = decode_point(data[offset:offset+66])
```

---

### 1.3 漏掉 SESSION_KEY_EXCHANGE

**问题**：ECJPAKE 握手成功后，API 调用全部超时

**原因**：ECJPAKE 的共享密钥**不能直接用于通信**，必须再交换一次会话密钥：

```
ECJPAKE 共享密钥 → 加密传输 24 字节 → 得到真正的会话密钥
```

**修复**：

```python
shared_cipher = Cipher(shared[0:16], shared[16:24])
my_key_nonce = os.urandom(24)
# 双向兼容：服务器可能先发也可能后发
try:
    resp = await wait_for(recv(), timeout=3.0)
    if resp[0] == SESSION_KEY_EXCHANGE:
        server_key_nonce = shared_cipher.decrypt(resp[1:])
        send(shared_cipher.encrypt(my_key_nonce))
except TimeoutError:
    send(shared_cipher.encrypt(my_key_nonce))
    resp = await recv()
    server_key_nonce = shared_cipher.decrypt(resp[1:])
```

---

### 1.4 Cipher 方向搞反

**问题**：会话密钥交换成功，但数据解密失败

**原因**：客户端用自己的 key 发送，用服务器的 key 接收

```python
cipher_out = Cipher(my_key_nonce[0:16], my_key_nonce[16:24])      # 发送
cipher_in  = Cipher(server_key_nonce[0:16], server_key_nonce[16:24]) # 接收
```

---

### 1.5 API 方法名错误

**问题**：所有 API 返回 `cannot read property 'func' of undefined`

**原因**：方法名写错了。正确名称是：

```python
# ❌ 错误
getDeviceList / getGraphs

# ✅ 正确
getDevList / getGraphList
```

**来源**：`oh-my-sage/ref/GUIDE.md`

---

## 二、HA 集成踩坑

### 2.1 登录码一次性

**问题**：连接成功后，HA 重启无法重连

**原因**：小米登录码**只能用一次**，无论成功还是失败都作废

**官方说明**：
> 登录码只能用一次，不管成功还是失败，用过就作废了。取一个新的再试。

**结论**：**无法持久化**，这是协议层限制。

---

### 2.2 后台自动重试消耗登录码

**问题**：用户输入的码总是失败

**原因**：`_ensure_connection()` 每 60 秒自动重连，把用户的码用光了

**日志证据**：
```
14:41:53 网关连接失败: RoundOne 异常: 4   ← 后台重试
14:41:53 网关连接失败: RoundOne 异常: 4   ← 又一次
14:43:08 登录码应用失败: RoundOne 异常: 4  ← 用户给的码已失效
```

**修复**：彻底禁用自动重试

```python
async def _ensure_connection(self):
    if self.gateway.connected:
        return True
    # 绝不自动重试 —— 会自动重试把用户即将输入的码作废
    return False
```

---

### 2.3 认证失败导致集成整个不加载

**问题**：登录码失效后，集成状态变 `setup_retry`，所有实体和服务消失

**原因**：`async_config_entry_first_refresh()` 抛异常导致 setup 失败

**修复**：首次刷新失败也继续，用空数据兜底

```python
try:
    await coordinator.async_config_entry_first_refresh()
except Exception as err:
    _LOGGER.warning("首次数据刷新失败（不影响实体创建）: %s", err)
    coordinator.async_set_updated_data(copy.deepcopy(EMPTY_DATA))
```

---

### 2.4 缺少 services.yaml

**问题**：日志报 `Failed to load services.yaml for integration: mijia_geek`

**原因**：自定义集成要提供服务定义文件

**修复**：创建 `services.yaml`，包含所有服务的参数说明和选择器

---

### 2.5 `CONF_SCAN_INTERVAL` 未定义

**问题**：`cannot import name 'CONF_SCAN_INTERVAL' from 'custom_components.mijia_geek.const'`

**原因**：`config_flow.py` 导入了这个常量，但 `const.py` 里没定义

**连带影响**：`config_flow` 导入失败 → **重新认证按钮无法工作**（点了没反应）

**修复**：在 `const.py` 加上

```python
CONF_SCAN_INTERVAL = "scan_interval"
```

---

### 2.6 Text 实体 `native_min` 陷阱

**问题**：登录码输入框显示 `unavailable`，日志报

```
ValueError: Entity ... provides state which is too short (minimum length 6)
```

**原因**：`native_min = 6` 但初始值是空字符串

**修复**：

```python
_attr_native_min = 0   # 不能设 6
_attr_native_max = 6
```

---

### 2.7 `CoordinatorEntity` 导致 Text 不可用

**问题**：登录码实体一直 `unavailable`

**原因**：继承 `CoordinatorEntity` 后，`available` 取决于 `last_update_success`，网关未连接时为 False

**修复**：Text 实体不继承 CoordinatorEntity

```python
class PasscodeText(TextEntity):   # 不继承 CoordinatorEntity
    @property
    def available(self) -> bool:
        return True   # 始终可用
```

---

### 2.8 重新认证按钮无效

**问题**：点「重新认证」按钮没反应

**原因**：HA 的 config flow `source=reauth` **只能从前端对话框触发**，程序化调用无法进入 reauth 分支

**证据**：通过 REST API 触发，返回的 `step_id` 是 `user` 而不是 `reauth_confirm`

**结论**：放弃按钮方案，改用 `set_passcode` 服务直接重连

---

### 2.9 `set_passcode` 服务返回 success 但没执行

**问题**：服务返回 `success: True`，但日志零输出

**原因**：HA 的 `call_service` 只要 handler 不抛未捕获异常就返回 success。而 handler 里的 `raise ValueError` 被 HA 转成 warning 吞掉

**修复**：改用 `_LOGGER.error` 打点，确保错误可见

---

## 三、部署踩坑

### 3.1 HAOS Docker 强制 hook

**问题**：`docker run` 启动容器失败

```
OCI runtime create failed: error running createRuntime hook #0: exit status 99
Error: argument 1 to ifthen(...) is not supported (got: null)
[260:26] app_type=ifthen("io.hass.type" in labels.keys(),labels["io.hass.type"],"")
```

**原因**：HAOS 的 Docker 有强制 hook，要求容器必须带 `io.hass.type` 标签

**修复**：启动时加标签

```bash
docker run -d --name mijia-geek-bridge \
  --label io.hass.type=app \
  --label io.hass.name=MijiaGeekBridge \
  --label io.hass.version=2.0.0 \
  --label io.hass.arch=amd64 \
  ...
```

---

### 3.2 「系统不受支持 - 不支持的软件」

**问题**：HA 界面弹出警告

**原因**：手动 `docker run` 的容器不带 Supervisor 管理标签，被判定为「外部软件」

**解决方案**：
- 短期：接受警告（功能正常），或
- 加 `io.hass.*` 标签减少部分警告，或
- 做成正式 addon（需要 Supervisor 支持 local 仓库）

---

### 3.3 本地 Addon 不被识别（未解决）

**问题**：把 addon 放到 `/mnt/data/supervisor/apps/local/mijia_geek_bridge/`，Supervisor 完全没加载

**排查过程**：

| 检查项 | 结果 |
|---|---|
| `store.json` 含 `"local"` | ✅ 有 |
| 目录结构和 config.yaml | ✅ 正确，YAML 可解析 |
| Supervisor 源码有 `RepositoryLocal` | ✅ 有 |
| 启动日志中的 `Loading app` | ❌ 只有 6 个 git/core，**无 local** |
| 日志中 `apps/local` 出现次数 | **0** |
| `ha apps` 的 repository 种类 | `['7eca76cc', 'core', 'ddc1b768']` — 无 local |

**结论**：**冬瓜定制版 Supervisor 不加载 local 仓库**

**尝试过的方案**：
1. `ha addons reload` — 只刷新 git 仓库
2. `touch config.yaml` 改 mtime — `update()` 返回 True 但没触发加载
3. 重启 Supervisor — 仍不加载
4. `ha store remove local` — 报错「不能移除内置仓库」
5. 做成 Git 仓库 + `ha store add file://...` — 报错「需要 URL」
6. 起 git daemon + `ha store add git://127.0.0.1/...` — **Supervisor 容器内 127.0.0.1 连不上宿主机**

**最终方案**：放弃 addon 机制，用手动 `docker run`

---

## 四、其他尝试记录

### 4.1 Floorplan 插件不可用

**问题**：ha-floorplan 卡片显示空白

**排查**：
- 图片 `/local/floorplan3d.svg` HTTP 200 可访问
- 最简配置（无 rules/stylesheet）也空白
- `picture-elements` + base64 内嵌正常

**结论**：该插件在此环境不可靠，已移除

### 4.2 文件写入通道尝试

| 通道 | 结果 |
|---|---|
| REST API 上传 | ❌ 无此接口 |
| `/media` 目录 | ❌ 空且只读 |
| Supervisor API 写文件 | ❌ 只提供 addon 管理 |
| File editor ingress | ❌ 需浏览器 Cookie |
| ttyd WebSocket | ❌ 握手成功但无回传（hassos-cli 需要 PTY） |
| **MCP Server** | ✅ **可用**（`POST /api/mcp`，92 个工具）|
| **SSH (22222)** | ✅ **可用**（root + RSA 密钥）|

**最终**：SSH 通道最可靠，用于所有文件操作。

### 4.3 ttyd 连接问题分析

**现象**：WebSocket 握手成功，子协议 `tty` 协商成功，但 4 秒内零帧然后断链

**尝试**：
- Basic Auth ✅ 通过
- token 参数 ❌
- Origin 头 ❌
- 禁用压缩 ❌
- 5 种发送时序 ❌

**首帧内容**：`'1' + '/usr/sbin/hassos-cli (homeassistant)'`

**结论**：ttyd 跑的是 `hassos-cli`（HAOS 全屏菜单），需要真实 PTY 尺寸和 TERM 环境，程序化驱动困难。

---

## 五、经验总结

### 做什么是对的

1. **先验证最小闭环**：先用独立脚本（`gw_client.py`）跑通协议，再包装成服务
2. **对照参考实现**：`oh-my-sage` 的 JS 代码是权威参考，逐行对照能快速定位问题
3. **加详细日志**：协议层每一步都打点，出问题能快速定位
4. **用 SSH 而非 API 做文件操作**：HA 的 API 对文件系统限制很多
5. **容器化隔离**：服务独立于 HA，重启互不影响

### 下次注意

1. **登录码要省着用**：每次调试都想清楚要不要真的连接
2. **先读源码再动手**：Supervisor 的 local addon 机制花了很久才确认不支持
3. **验证假设**：比如「HA config flow 能程序化触发 reauth」这个假设错了，浪费了几轮
4. **注意 HA 的日志级别**：`info` 级别在默认配置下看不到，调试要用 `warning`/`error`
