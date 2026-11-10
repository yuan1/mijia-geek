# 协议实现文档

本文档记录小米中枢网关「自动化极客版」的逆向协议细节，供二次开发参考。

---

## 一、连接链路

```
┌────────┐                              ┌──────────┐
│ 客户端  │                              │ 中枢网关  │
└───┬────┘                              └────┬─────┘
    │                                        │
    │ 1. WebSocket 连接                       │
    │   ws://<IP>/centrallinkws/             │
    ├───────────────────────────────────────►│
    │                                        │
    │ 2. 协议协商                             │
    │   帧: [1] + JSON(["passcode"])         │
    ├───────────────────────────────────────►│
    │                                        │
    │   帧: [2] + JSON({"protocol":"passcode"})│
    │◄───────────────────────────────────────┤
    │                                        │
    │ 3. ECJPAKE 第一轮                       │
    │   帧: [32] + 330字节                   │
    ├───────────────────────────────────────►│
    │   帧: [32] + 330字节                   │
    │◄───────────────────────────────────────┤
    │                                        │
    │ 4. ECJPAKE 第二轮                       │
    │   帧: [33] + 165字节                   │
    ├───────────────────────────────────────►│
    │   帧: [33] + 168字节（含3字节前缀）      │
    │◄───────────────────────────────────────┤
    │                                        │
    │ 5. 会话密钥交换                          │
    │   帧: [3] + 加密的24字节(nonce+key)     │
    ├───────────────────────────────────────►│
    │   帧: [3] + 加密的24字节                │
    │◄───────────────────────────────────────┤
    │                                        │
    │ 6. 加密通信                             │
    │   帧: [5] + AES-GCM(压缩的JSON-RPC)     │
    ├───────────────────────────────────────►│
```

### 1.1 网关地址解析

与参考实现一致，允许路径前缀并显式写出默认端口：

```
http://host[:port][/prefix]   -> ws://host:80/prefix/centrallinkws/
https://host[:port][/prefix]  -> wss://host:443/prefix/centrallinkws/
```

代码位置：`bridge/app/protocol/gateway.py::MijiaGateway.ws_url()`（`urllib.parse.urlsplit`，兼容 IPv6）。

---

## 二、帧类型

```python
class DType:
    PROTOCOL_LIST = 1          # 客户端发送支持的协议列表
    SELECTED_PROTOCOL = 2      # 服务器返回选中的协议
    SESSION_KEY_EXCHANGE = 3   # 会话密钥交换
    ERROR = 4                  # 错误
    DATA = 5                   # 加密数据
    SERVER_PUB_KEY = 16        # 服务器公钥（未使用）
    ECJPAKE_ROUND_ONE = 32     # ECJPAKE 第一轮
    ECJPAKE_ROUND_TWO = 33     # ECJPAKE 第二轮
```

**格式**：`1 字节类型 + payload`

---

## 三、ECJPAKE 实现

### 3.1 椭圆曲线参数

使用 **secp256k1**（比特币同款曲线）：

```python
P  = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N  = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
A  = 0
B  = 7
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
G  = (GX, GY)
```

### 3.2 点编码

**标准格式（66 字节）**：
```
[0x41, 0x04] + X(32字节BE) + Y(32字节BE)
```

**哈希用格式（69 字节）**：
```
uint32BE(65) + [0x04] + X(32) + Y(32)
```

### 3.3 ZKP 零知识证明

**哈希计算**：

```python
def zkp_hash(g, v, pub, name):
    name_bytes = name.encode()
    data = (encode_point_for_hash(g)      # 69 bytes
            + encode_point_for_hash(v)     # 69 bytes
            + encode_point_for_hash(pub)   # 69 bytes
            + b"\x00\x00\x00\x00"          # 4 bytes 占位
            + name_bytes)
    # ⚠️ 关键：在偏移 207 处写入 name 长度
    data = data[:207] + struct.pack(">I", len(name_bytes)) + data[211:]
    return int.from_bytes(sha256(data).digest(), "big") % N
```

> **坑点**：`name` 长度必须在偏移 207 写入，而不是顺序拼接。这是参考 JS 实现发现的。

**ZKP 生成**：

```python
def gen_zkp(g, pub, priv, name):
    k = random_int() % N or 1
    v = point_mul(k, g)
    c = zkp_hash(g, v, pub, name)
    return v, (k - c * priv) % N
```

**ZKP 验证**：

```python
def verify_zkp(g, pub, v, r, name):
    c = zkp_hash(g, v, pub, name)
    return point_add(point_mul(c, pub), point_mul(r, g)) == v
```

**ZKP 编码（99 字节）**：
```
encode_point(v)(66) + [0x20] + r(32字节BE)
```

### 3.4 第一轮

**发送（330 字节）**：
```python
x1 = random_int() % N
x2 = random_int() % N
g_x1 = point_mul(x1, G)
g_x2 = point_mul(x2, G)
v1, r1 = gen_zkp(G, g_x1, x1, "client")
v2, r2 = gen_zkp(G, g_x2, x2, "client")

data = encode_point(g_x1) + encode_zkp(v1, r1)      # 状态 66+99=165
     + encode_point(g_x2) + encode_zkp(v2, r2)      # 再 165，共 330
```

**接收**：解析出 `g_x3`、`g_x4`，用 `"server"` 作为 name 验证 ZKP。

### 3.5 第二轮

**发送（165 字节）**：
```python
g_xa = g_x1 + g_x3 + g_x4          # 点加法
y = random_16bytes * N + secret_int
x2_s = (x2 * y) % N
g_s = point_mul(x2_s, g_xa)
v, r = gen_zkp(g_xa, g_s, x2_s, "client")

data = encode_point(g_s) + encode_zkp(v, r)   # 66 + 99 = 165
```

**接收**：
```python
# ⚠️ 客户端需要跳过 3 字节前缀
offset = 3 if role == "client" else 0

g_xb = g_x1 + g_x2 + g_x3
g_s_peer = decode_point(data[offset:offset+66])
v, r = decode_zkp(data[offset+66:offset+165])
verify_zkp(g_xb, g_s_peer, v, r, "server")

# 计算共享密钥
y = random_16bytes * N + secret_int
m = (x2 * y) % N
tmp = g_s_peer - point_mul(m, g_x4)
shared_point = point_mul(x2, tmp)
shared_key = sha256(shared_point[0].to_bytes(32, "big")).digest()
```

> **坑点**：第二轮的服务器响应有 3 字节前缀，客户端必须跳过。

---

## 四、会话密钥交换

ECJPAKE 的共享密钥**只用于加密会话密钥**，不直接用于通信。

### 4.1 用共享密钥建临时 Cipher

```python
shared_cipher = Cipher(shared_key[0:16], shared_key[16:24])
```

### 4.2 交换 24 字节（16字节key + 8字节nonce）

```python
my_key_nonce = os.urandom(24)
encrypted = shared_cipher.encrypt(my_key_nonce)
send_frame(DType.SESSION_KEY_EXCHANGE, encrypted)

# 接收服务器的
resp = recv_frame()
server_key_nonce = shared_cipher.decrypt(resp[1:])
```

### 4.3 建立最终 Cipher

```python
cipher_out = Cipher(my_key_nonce[0:16], my_key_nonce[16:24])       # 发送用
cipher_in  = Cipher(server_key_nonce[0:16], server_key_nonce[16:24]) # 接收用
```

> **坑点**：客户端用**自己的** key 发送，用**服务器的** key 接收。

### 4.4 顺序兼容

服务器可能先发也可能后发，需要双重兼容：

```python
try:
    resp = await wait_for(recv(), timeout=3.0)
    if resp[0] == SESSION_KEY_EXCHANGE:
        # 服务器先发
        server_key_nonce = shared_cipher.decrypt(resp[1:])
        send(encrypt(my_key_nonce))
except TimeoutError:
    # 客户端先发
    send(encrypt(my_key_nonce))
    resp = await recv()
    server_key_nonce = shared_cipher.decrypt(resp[1:])
```

---

## 五、AES-128-GCM 分帧

### 5.1 IV 构造

```python
def make_iv(nonce: bytes, counter: int) -> bytes:
    """nonce(8字节) + counter(4字节小端) = 12字节 IV"""
    return nonce + struct.pack("<I", counter)
```

### 5.2 加密

```python
counter = enc_counter
enc_counter += 1
ct = AESGCM(key).encrypt(make_iv(nonce, counter), plaintext, None)
# ct 末尾自带 16 字节 tag
frame = struct.pack("<I", counter) + ct
```

### 5.3 解密

```python
counter = struct.unpack("<I", frame[0:4])[0]
if counter <= dec_counter:
    raise ValueError("Replay attack")
dec_counter = counter
plaintext = AESGCM(key).decrypt(make_iv(nonce, counter), frame[4:], None)
```

> **注意**：计数器单调递增，用于防重放攻击。

---

## 六、压缩

使用 **raw DEFLATE**（无 zlib 头），前置 4 字节原始长度：

```python
def compress(data: bytes) -> bytes:
    co = zlib.compressobj(9, zlib.DEFLATED, -15)   # -15 = raw deflate
    return struct.pack("<I", len(data)) + co.compress(data) + co.flush()

def decompress(data: bytes) -> bytes:
    if len(data) < 4:
        raise ValueError("压缩数据过短")
    return zlib.decompress(data[4:], -15)
```

---

## 七、JSON-RPC 调用

### 7.1 请求格式

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "/api/getDevList",
  "params": {}
}
```

> **注意**：method 带 `/api/` 前缀。

### 7.2 完整发送流程

```python
req = {"jsonrpc": "2.0", "id": rid, "method": f"/api/{method}", "params": params}
payload = compress(json.dumps(req).encode())
encrypted = cipher_out.encrypt(payload)
fut = asyncio.get_running_loop().create_future()
pending[rid] = fut
try:
    await ws.send(bytes([DType.DATA]) + encrypted)
    resp = await asyncio.wait_for(fut, timeout=timeout)
finally:
    pending.pop(rid, None)          # 超时/发送失败都要清理，避免 future 泄漏
    if not fut.done():
        fut.cancel()
```

### 7.3 完整接收流程

```python
async for raw in ws:
    if raw[0] == DType.ERROR:
        log("网关返回错误帧")
        continue
    if raw[0] != DType.DATA:
        continue
    decrypted = cipher_in.decrypt(raw[1:])
    msg = json.loads(decompress(decrypted).decode())

    rid = msg.get("id")
    if rid is not None:
        fut = pending.pop(rid, None)
        if fut is not None and not fut.done():
            fut.set_result(msg)      # 应答
        # 超时后晚到的应答直接丢弃，不能当成推送
    else:
        handle_push(msg)             # 推送（无 id）
```

> **区分应答和推送**：应答有 `id`，推送没有。
> **返回值**：`call()` 返回 `result`，错误统一转成异常，调用方不用再判断 `error` 字段。

---

## 八、API 方法一览

> 以下“归一化后返回”指本项目 `MijiaGateway` 对外暴露的类型；原始 JSON-RPC `result` 见备注。
> 所有方法统一走 `call()`：返回 `result`，`error` 转异常。

### 设备

| 方法 | 参数 | 归一化后返回 |
|---|---|---|
| `getDevList` | `{}` | `dict[did, device]`（原始 `{devList: {...}}`） |

### 规则

| 方法 | 参数 | 说明 |
|---|---|---|
| `getGraphList` | `{}` | `list[rule]`；兼容原始数组或 `{"rules": [...]}` |
| `getGraph` | `{id}` | 规则详情 `{"nodes": [...]}` |
| `setGraph` | `{id, nodes, cfg}` | 保存规则；`cfg.id` 必须与 `id` 相同 |
| `deleteGraph` | `{id}` | 删除规则 |
| `changeGraphConfig` | `{id, enable, userData}` | 启停/改名；`userData` 含 `name/lastUpdateTime/transform` |

### 日志

| 方法 | 参数 | 说明 |
|---|---|---|
| `getLog` | `{num}` | 日志页码，从 0 开始；返回字符串，空串表示没有更多日志 |

### 变量

| 方法 | 参数 | 归一化后返回 |
|---|---|---|
| `getVarScopeList` | `{}` | `list[str]`（原始 `{"scopes": [...]}`） |
| `getVarList` | `{scope}` | `dict` |
| `getVarValue` | `{scope, id}` | 标量值（原始 `{"value": ...}`） |
| `setVarValue` | `{scope, id, value}` | `{}`；值必须匹配变量类型（number/string） |
| `createVar` | `{scope, id, type, value, userData}` | `{}`；type 仅 `number` / `string` |
| `deleteVar` | `{scope, id}` | `{}`；网关另支持 `{scope, all: true}`，本项目未封装 |

### 备份

| 方法 | 参数 | 说明 |
|---|---|---|
| `getBackupList` | `{from: "fds"}` | 云端备份列表；本项目仅实现读取，未在 UI 暴露 |

### 推送

| 方法 | 参数 |
|---|---|
| `/push/property_changed` | `{did, siid, piid, value}` |
| `/push/event_triggered` | `{did, siid, eiid, arguments}` |

---

## 九、规则节点类型

规则由节点组成，通过 `setGraph` 提交。

### deviceInput（设备触发）

```json
{
  "id": "n1",
  "type": "deviceInput",
  "cfg": {
    "urn": "urn:miot-spec-v2:device:sensor:0000A077:linp-hb01:1",
    "pos": {"x": 100, "y": 100, "width": 200, "height": 100}
  },
  "props": {
    "did": "1000000001",
    "siid": 2,
    "piid": 1,
    "dtype": "bool",
    "operator": "eq",
    "value": true
  },
  "inputs": {},
  "outputs": {"output": [["n2", "trigger"]]}
}
```

**操作符**：`eq` / `ne` / `gt` / `lt` / `ge` / `le`

**触发条件**：
- 有 `piid`：属性值满足条件时触发
- 有 `eiid`：事件发生时触发

### deviceOutput（执行操作）

**通过 aiid 调用动作**：
```json
{
  "type": "deviceOutput",
  "props": {
    "did": "1000000002",
    "siid": 2,
    "aiid": 1,
    "params": [true]
  }
}
```

**通过 piid 设置属性**：
```json
{
  "type": "deviceOutput",
  "props": {
    "did": "1000000002",
    "siid": 2,
    "piid": 1,
    "dtype": "int",
    "params": [50]
  }
}
```

### 其他节点类型

| 类型 | 用途 |
|---|---|
| `deviceGet` | 查询设备状态 |
| `condition` | 条件判断 |
| `logicAnd` / `logicOr` / `logicNot` | 逻辑运算 |
| `timeRange` | 时间段 |
| `delay` / `timer` | 延时/定时 |
| `loop` | 循环 |
| `counter` | 计数器 |
| `modeSwitch` | 模式切换 |
| `eventSequence` | 事件序列 |
| `signalOr` | 信号或 |
| `statusLast` | 状态持续 |
| `onLoad` | 启动时 |

### 规则 cfg 结构

```json
{
  "id": "rule_123456",
  "enable": true,
  "uiType": "graph",
  "userData": {
    "name": "规则名称",
    "lastUpdateTime": 1710000000000,
    "transform": {"x": 0, "y": 0, "scale": 1, "rotate": 0}
  }
}
```

---

## 十、调试技巧

### 抓包分析

```bash
# 在网关同网段抓包
tcpdump -i any -w mijia.pcap host 192.168.1.100
```

注意：数据是加密的，只能看到帧类型和长度。

### 逐步打印

在 `bridge/app/protocol/gateway.py` 的 `_log()` 里输出关键节点：

```python
self._log(f"ECJPAKE 完成: {shared.hex()[:16]}...")
self._log(f"会话密钥: client={my_kn.hex()[:8]} server={srv_kn.hex()[:8]}")
```

### 常见错误码

| 现象 | 原因 |
|---|---|
| `RoundOne 异常: 4` | 登录码无效/已用/过期 |
| 握手成功但 API 超时 | 会话密钥交换失败，检查 key/nonce 方向 |
| 解密失败 | Cipher 方向反了，或计数器错乱 |
| `cannot read property 'func' of undefined` | API 方法名错误 |

---

## 十一、参考实现

- Node.js 原版：[oh-my-sage](https://github.com/allocnode/oh-my-sage)
  - `ref/gateway_client.js` — 完整协议实现
  - `ref/GUIDE.md` — 官方 API 文档

本项目为独立 Python 实现：
- secp256k1 点运算为纯 Python（无第三方椭圆曲线库）；
- AES-128-GCM 使用 `cryptography`；
- WebSocket 使用 `websockets`。

> 协议层只保留一份实现：`bridge/app/protocol/gateway.py`。
> `ha_integration/mijia_geek/` 已重构为 Bridge REST 客户端，不再复制协议代码。
