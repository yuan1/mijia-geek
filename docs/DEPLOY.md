# 部署指南（实测验证版）

本指南记录 **2026-11-10 在真实环境（HAOS 2026.8.3 + Xiaomi 中枢网关）的完整部署过程**，包含所有踩过的坑。

---

## 一、实测环境

| 项目 | 值 |
|---|---|
| HAOS 主机 | `192.168.1.10` |
| SSH | 端口 `22222`，用户 `root`（RSA 密钥） |
| HA 版本 | 2026.8.3 |
| HAOS | 18.2 |
| 架构 | amd64 |
| 中枢网关 | `192.168.1.100` |
| 网关极客版 Web | `http://192.168.1.100:8086/` |
| Bridge 版本 | **2.3.0** |
| 部署方式 | **Supervisor Addon（仓库方式，推荐）** |

---

## 二、部署方式对比

| 方式 | 网络模式 | HA 集成填什么地址 | 结论 |
|---|---|---|---|
| **Supervisor Addon** | Docker `bridge` | `http://local-mijia-geek-bridge:8099` | ✅ **推荐** |
| 手动 `docker run --network host` | `host` | `http://127.0.0.1:8099` | ⚠️ 可行但会报「不支持的软件」 |

> ⚠️ **两者不能同时运行**，会抢占 8099 端口导致混乱（实测踩过这个坑）。

---

## 三、方式 A：Supervisor Addon（推荐）

> **两种子方式，选其一：**
>
> | | A-1 仓库方式（推荐） | A-2 本地 addon |
> |---|---|---|
> | 更新 | 商店里点「更新」即可 | 每次手动拷源码 + 重建 |
> | 前提 | 仓库已推送到 GitHub | 能访问宿主机 `/mnt/data` |
> | 适合 | 长期使用、多设备 | 开发调试 |
>
> 下面先讲 A-1，A-2 见 3.2。

### 3.0 方式 A-1：把仓库添加为「附加组件仓库」⭐

本仓库根目录含 `repository.yaml`，可直接被 HA 识别为 Add-on 仓库。

**一次性操作：**

1. HA → **设置 → 加载项 → 附加组件商店**
2. 右上角 **⋮ → 仓库**
3. 填入仓库地址并添加：

   ```
   https://github.com/yuan1/mijia-geek
   ```

4. 点「添加」后关闭，回到商店，**刷新页面**
5. 在列表中找到 **Mijia Geek Bridge**，点进去 → **安装**

**之后如何更新：**

```
设置 → 加载项 → Mijia Geek Bridge → 有新版本时会出现「更新」按钮 → 点更新
```

> HA 通过 `bridge/config.yaml` 的 `version` 字段判断是否有更新。
> 所以**每次发布必须递增 version**（`x.y.z` 字符串，不是数字），
> 否则商店不会提示更新。

**发布新版本的完整流程：**

```bash
# 1. 改版本号（两处必须一致，CI 会检查）
#    bridge/config.yaml          version: "2.3.1"
#    ha_integration/mijia_geek/manifest.json   "version": "2.3.1"

# 2. 本地校验元数据
python scripts/check_addon.py

# 3. 提交并推送
git add -A && git commit -m "chore: bump to 2.3.1" && git push

# 4. HA 里：加载项商店 → 右上角 ⋮ → 检查更新 → 点「更新」
```

> 首次安装需要 Supervisor 拉取仓库并**编译镜像**（python 3.12 + 依赖），
> 视网络与机器性能约 3–10 分钟。日志可在 Add-on 页面「日志」标签查看。

### 3.1 前置：确认 local addon 是否被支持（仅 A-2 方式需要）



### 3.1 前置：确认 local addon 是否被支持

⚠️ **冬瓜定制版 HAOS 的 Supervisor 不加载 `apps/local` 仓库**，需先验证：

```bash
ssh -p 22222 root@192.168.1.10

# 检查 Supervisor 启动日志中是否加载了 local
docker logs hassio_supervisor 2>&1 | grep -c 'apps/local'
```

- 返回 `0` → **本地 addon 不可用**，需要走方式 B，或用第三方仓库
- 返回 `>0` → 可以继续

**验证方法 2**（更直接）：

```bash
ha apps 2>&1 | grep -i 'mijia'
# 有输出 = addon 已被识别
```

### 3.2 放置代码

```bash
# 从本地机器上传
scp -P 22222 -r mijia-geek/bridge root@192.168.1.10:/mnt/data/supervisor/apps/local/mijia_geek_bridge
```

目录结构必须是：

```
/mnt/data/supervisor/apps/local/mijia_geek_bridge/
├── config.yaml
├── Dockerfile
├── run.sh
├── requirements.txt
└── app/
    ├── main.py
    ├── ...
```

### 3.3 在 HA 界面安装

1. **设置 → 加载项 → 右下角「加载项商店」**
2. 右上角 **⋮ → 检查更新**（或重启 Supervisor：`ha supervisor restart`）
3. 在列表中找到 **Mijia Geek Bridge**
4. 点击 → **安装**（Supervisor 会自动构建镜像，约 3-5 分钟）
5. 安装完成后 **配置** 页填写：

| 配置项 | 建议值 | 说明 |
|---|---|---|
| `gateway_url` | `http://192.168.1.100` | 网关局域网 IP |
| `gateway_web_url` | 留空（自动探测） | 或填 `http://192.168.1.100:8086/` |
| `api_token` | **自建一个长随机串** | ⚠️ 必须设置，否则无鉴权 |
| `scan_interval` | `60` | 数据刷新秒数 |
| `mcp_http_port` | `0` | 不启用 MCP HTTP |
| `mcp_allow_write` | `false` | 是否允许 MCP 写操作 |
| `mcp_allow_passcode` | `false` | 是否允许 MCP 输码 |

6. **启动** addon

### 3.4 确认容器网络地址 ⭐ 关键

addon 默认跑在 Docker **bridge 网络**，不是 host！

```bash
# 查看容器 IP
docker inspect app_local_mijia_geek_bridge \
  --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
# 输出示例: 172.30.0.2
```

**从 HA 容器内测试连通性**（这一步决定集成能否配上）：

```bash
docker exec homeassistant curl -s --max-time 4 \
  http://local-mijia-geek-bridge:8099/api/health
# 期望: {"ok":true}
```

**三种地址的实测结果**：

| 地址 | 结果 | 说明 |
|---|---|---|
| `http://local-mijia-geek-bridge:8099` | ✅ **可用** | Docker 容器名别名，**推荐** |
| `http://172.30.0.2:8099` | ✅ 可用 | 容器 IP，但可能变化 |
| `http://127.0.0.1:8099` | ❌ **FAIL** | 在 HA 容器内指向 HA 自己 |

> 💡 **口诀**：`127.0.0.1` 只在 `--network host` 模式下有效；bridge 模式必须用容器名或容器 IP。

### 3.5 配置 HA 集成

1. **设置 → 设备与服务 → 添加集成 → 搜「米家极客版」**
2. 填写：

| 字段 | 值 |
|---|---|
| **Bridge URL** | `http://local-mijia-geek-bridge:8099` |
| **API Token** | 你在 addon 配置里设的那个 |

3. 提交后应该显示「成功」

### 3.6 输入登录码连接网关

**方式 1（Web UI，推荐）**
```
http://192.168.1.10:8099
```
首次打开会提示输入 API Token → 输入 → 再输 6 位登录码

**方式 2（API）**
```bash
curl -X POST http://192.168.1.10:8099/api/connect \
  -H 'Authorization: Bearer <你的API_TOKEN>' \
  -H 'Content-Type: application/json' \
  -d '{"passcode":"123456"}'
```

### 3.7 验证成功

```bash
curl -s -H 'Authorization: Bearer <TOKEN>' \
  http://192.168.1.10:8099/api/status | python3 -m json.tool
```

**期望输出**：
```json
{
  "connected": true,
  "gateway_url": "http://192.168.1.100",
  "device_count": 100,
  "online_count": 40,
  "rule_count": 0,
  "has_passcode": true,
  ...
}
```

HA 里应该出现：
- `sensor.mi_jia_ji_ke_ban_*` 系列传感器
- `binary_sensor.*连接*`
- 规则开关（如果有规则）
- `button.*` 刷新/重连按钮

---

## 四、方式 B：手动 Docker（备选）

适用于 local addon 不被支持的 HAOS 版本。

### 4.1 构建

```bash
ssh -p 22222 root@192.168.1.10
cd /mnt/data/supervisor/apps/local/mijia_geek_bridge
docker build -t mijia-geek-bridge:2.2.0 .
```

### 4.2 启动（host 网络）

```bash
docker run -d --name mijia-geek-bridge \
  --restart unless-stopped \
  --label io.hass.type=app \
  --label io.hass.name=MijiaGeekBridge \
  --label io.hass.version=2.2.0 \
  --label io.hass.arch=amd64 \
  --network host \
  -e GATEWAY_URL=http://192.168.1.100 \
  -e SCAN_INTERVAL=60 \
  -e API_TOKEN=your-long-random-token \
  -e DATA_DIR=/data \
  -v /mnt/data/mijia_geek_bridge:/data \
  mijia-geek-bridge:2.2.0
```

**这种方式下集成填 `http://127.0.0.1:8099`**。

### 4.3 代价

- ⚠️ HA 会报「系统不受支持 - 不支持的软件」（容器不由 Supervisor 管理）
- ⚠️ 不在「加载项」列表显示，也不在侧边栏
- ✅ 但功能正常

---

## 五、Dockerfile 踩坑记录

### 5.1 alpine 基础镜像构建失败 ⭐

**现象**：
```
ERROR: ResolutionImpossible
The conflict is caused by:
    jsonschema 4.26.0 depends on rpds-py>=0.25.0
    ...
```

**原因**：`mcp` 依赖 `jsonschema` → 依赖 `rpds-py`（Rust 项目），**alpine 上没有预编译 wheel**，需从源码编译，pip 回退到旧版后与其他包冲突。

**解决**：改用 Debian 系基础镜像：

```dockerfile
ARG BUILD_FROM=python:3.12-slim-bookworm
FROM ${BUILD_FROM}

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*
```

**镜像体积对比**：
| 基础镜像 | 最终体积 | 结果 |
|---|---|---|
| `python:3.12-alpine` | — | ❌ 构建失败 |
| `python:3.12-slim-bookworm` | 197 MB | ✅ 成功 |

### 5.2 构建耗时长

`cryptography`、`pydantic-core` 等包较大，首次构建约 **3-5 分钟**。建议后台运行并轮询：

```bash
cd /mnt/data/supervisor/apps/local/mijia_geek_bridge
nohup docker build -t mijia-geek-bridge:2.2.0 . > /tmp/build.log 2>&1 &
# 之后 tail -f /tmp/build.log 查看进度
```

---

## 六、故障排查

### 6.1 `Bridge 报告网关未连接` ⭐ 最常见

**完整报错**：
```
custom_components.mijia_geek.bridge.BridgeUnavailable: Bridge 报告网关未连接
```

**这不是 HA 集成的问题，是 Bridge → 网关没连上。**

**排查顺序**：

```bash
# 1. 检查 bridge 自己的状态
curl -s -H 'Authorization: Bearer <TOKEN>' \
  http://192.168.1.10:8099/api/status
# 看 "connected" 和 "has_passcode" 字段

# 2. 若 has_passcode: false → 从未输过码
#    去 Web UI 输码，或调 /api/connect

# 3. 若 connected: false 但 has_passcode: true → 连接掉了
#    重新输码（一次性，无法重连）
```

**根因**：登录码一次性且不持久化，bridge 重启后连接失效。

### 6.2 有多个 bridge 容器冲突 ⭐

**现象**：你明明连上了，但 HA 一直报未连接。

**排查**：
```bash
docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -i mijia
```

**可能看到**：
```
app_local_mijia_geek_bridge   Up 9 minutes   8098-8099/tcp   ← Supervisor 管的
mijia-geek-bridge             Up 28 minutes                  ← 手动跑的（冲突源）
```

**解决**：删掉手动创建的那个
```bash
docker rm -f mijia-geek-bridge
docker restart app_local_mijia_geek_bridge
```

### 6.3 `127.0.0.1:8099` 连不上 ⭐

**原因**：addon 在 bridge 网络，`127.0.0.1` 指向 HA 自己。

**解决**：改用容器名
```bash
docker exec homeassistant curl -s http://local-mijia-geek-bridge:8099/api/health
```

### 6.4 「系统不受支持 - 不支持的软件」

**原因**：容器缺 `io.hass.*` 标签（仅方式 B 会遇到）

**解决**：加 4 个标签重建：
```bash
--label io.hass.type=app
--label io.hass.name=MijiaGeekBridge
--label io.hass.version=2.2.0
--label io.hass.arch=amd64
```

### 6.5 登录码一直失败 `RoundOne 异常: 4`

**原因**：登录码已过期 / 已被使用 / 被后台重试消耗

**注意**：
- 登录码**只能用一次，失败也算**
- 新版已禁用自动重试，不会再消耗
- 取码后 10 秒内使用

### 6.6 addon 安装后不出现

**排查**：
```bash
# 1. 确认文件在正确位置
ls -la /mnt/data/supervisor/apps/local/mijia_geek_bridge/

# 2. 确认 config.yaml 能被解析
docker exec hassio_supervisor python3 -c \
  "import yaml; print(yaml.safe_load(open('/data/apps/local/mijia_geek_bridge/config.yaml'))['name'])"

# 3. 检查 Supervisor 是否加载了 local
docker logs hassio_supervisor 2>&1 | grep -c 'apps/local'
```

若第 3 步返回 `0`，说明该 HAOS 版本的 Supervisor **不支持 local addon**，改用方式 B。

---

## 七、API 使用示例

### 设置 Token 变量

```bash
export TOKEN="mgk_xxxxxxxxxx"
export BRIDGE="http://192.168.1.10:8099"
```

### 常用操作

```bash
# 查看状态
curl -s -H "Authorization: Bearer $TOKEN" $BRIDGE/api/status | python3 -m json.tool

# 连接网关
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"passcode":"123456"}' $BRIDGE/api/connect

# 设备列表
curl -s -H "Authorization: Bearer $TOKEN" $BRIDGE/api/devices | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('设备数:', len(d.get('devices',{})))
for k,v in list(d.get('devices',{}).items())[:10]:
    print(f\"  {v.get('name','?'):20s} {v.get('roomName',''):8s} {'在线' if v.get('online') else '离线'}\")
"

# 规则列表
curl -s -H "Authorization: Bearer $TOKEN" $BRIDGE/api/rules

# 启停规则
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"enable":false}' $BRIDGE/api/rule/<rule_id>/toggle

# 执行日志
curl -s -H "Authorization: Bearer $TOKEN" "$BRIDGE/api/logs?page=0"

# 强制刷新
curl -X POST -H "Authorization: Bearer $TOKEN" $BRIDGE/api/refresh
```

---

## 八、维护操作

### 升级

```bash
# 1. 上传新代码
scp -P 22222 -r mijia-geek/bridge/* \
  root@192.168.1.10:/mnt/data/supervisor/apps/local/mijia_geek_bridge/

# 2. 方式 A：在 HA 界面「加载项 → 重新构建」
#    方式 B：
ssh -p 22222 root@192.168.1.10
cd /mnt/data/supervisor/apps/local/mijia_geek_bridge
docker build -t mijia-geek-bridge:2.2.0 .
docker rm -f mijia-geek-bridge
# 重新执行 docker run
```

### 查看日志

```bash
# addon 方式
docker logs -f app_local_mijia_geek_bridge

# 只看错误
docker logs --tail 100 app_local_mijia_geek_bridge 2>&1 | grep -iE 'error|fail|exception'
```

### 卸载

```bash
# 方式 A：HA 界面「加载项 → 卸载」
# 方式 B：
docker rm -f mijia-geek-bridge
docker rmi mijia-geek-bridge:2.2.0

# 清理数据（包含可能残留的码）
rm -rf /mnt/data/mijia_geek_bridge
```

---

## 九、推荐架构

```
┌──────────────────────────────────────────────┐
│  HAOS (192.168.1.10)                         │
│                                               │
│  ┌────────────────────┐   ┌───────────────┐  │
│  │ mijia-geek-bridge  │   │ Home Assistant│  │
│  │ (addon, bridge 网络)│   │               │  │
│  │  172.30.0.2:8099  │◄──┤ 集成通过       │  │
│  │                    │   │ local-mijia-  │  │
│  │ · 长连接网关        │   │ geek-bridge:  │  │
│  │ · Web UI           │   │ 8099 调用     │  │
│  │ · REST API         │   └───────────────┘  │
│  │ · MCP Server       │                      │
│  └─────────┬──────────┘                      │
└────────────┼─────────────────────────────────┘
             │ WebSocket + ECJPAKE + AES-GCM
             ▼
    ┌────────────────────┐
    │ 中枢网关           │
    │ 192.168.1.100      │
    │ 极客版 :8086       │
    └────────────────────┘
```

**设计优势**：
- Bridge 独立容器，**HA 重启不影响网关连接**
- 网关只登录一次，长期保持
- Web UI 独立可用
- REST API 可被任何系统调用
- MCP 支持接入 AI Agent
