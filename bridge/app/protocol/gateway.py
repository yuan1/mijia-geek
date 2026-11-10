#!/usr/bin/env python3
"""米家中枢网关极客版客户端 —— Python 实现
协议: WebSocket + ECJPAKE(secp256k1) + SESSION_KEY_EXCHANGE + AES-128-GCM + JSON-RPC

已验证 API 方法:
  getDevList  getGraphList  getGraph  setGraph  deleteGraph  changeGraphConfig
  getVarScopeList  getVarList  createVar  deleteVar  getVarValue  setVarValue
  getLog  getBackupList  createBackup  loadBackup  deleteBackup
"""
import asyncio
import hashlib
import json
import os
import struct
import zlib
from typing import Optional
from urllib.parse import urlsplit

import websockets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ==================== secp256k1 ====================
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
G = (GX, GY)


def _inv(a: int, m: int) -> int:
    return pow(a, m - 2, m)


def point_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if y1 != y2:
            return None
        if y1 == 0:
            return None  # 切线垂直，结果为无穷远点
        lam = (3 * x1 * x1) * _inv(2 * y1, P) % P
    else:
        lam = (y2 - y1) * _inv(x2 - x1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return (x3, (lam * (x1 - x3) - y1) % P)


def point_mul(k: int, pt):
    r = None
    a = pt
    while k:
        if k & 1:
            r = point_add(r, a)
        a = point_add(a, a)
        k >>= 1
    return r


def point_neg(pt):
    return None if pt is None else (pt[0], (-pt[1]) % P)


def encode_point(pt) -> bytes:
    x, y = pt
    return bytes([65, 4]) + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def encode_point_for_hash(pt) -> bytes:
    x, y = pt
    return struct.pack(">I", 65) + bytes([4]) + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def decode_point(d: bytes):
    if len(d) < 66:
        raise ValueError(f"Point too short: {len(d)}")
    if d[0] != 65 or d[1] != 4:
        raise ValueError(f"Invalid point: {d[:2].hex()}")
    x = int.from_bytes(d[2:34], "big")
    y = int.from_bytes(d[34:66], "big")
    if x >= P or y >= P:
        raise ValueError("Point coordinates out of range")
    if (y * y - (x * x * x + 7)) % P != 0:
        raise ValueError("Point is not on secp256k1")
    return (x, y)


def encode_zkp(v, r: int) -> bytes:
    return encode_point(v) + bytes([32]) + (r % N).to_bytes(32, "big")


def decode_zkp(d: bytes):
    if len(d) < 99:
        raise ValueError(f"ZKP too short: {len(d)}")
    if d[66] != 32:
        raise ValueError(f"Invalid ZKP marker: {d[66]}")
    r = int.from_bytes(d[67:99], "big")
    if r >= N:
        raise ValueError("ZKP scalar out of range")
    return decode_point(d[0:66]), r


# ==================== ECJPAKE ====================
class ECJPAKE:
    def __init__(self, secret: str, role: str = "client"):
        self.role = role
        self.peer_role = "server" if role == "client" else "client"
        self.secret = secret.encode()
        self.x1 = self.x2 = None
        self.g_x1 = self.g_x2 = self.g_x3 = self.g_x4 = None

    def _h(self, g, v, pub, name: str) -> int:
        nb = name.encode()
        data = (encode_point_for_hash(g) + encode_point_for_hash(v)
                + encode_point_for_hash(pub) + b"\x00\x00\x00\x00" + nb)
        data = data[:207] + struct.pack(">I", len(nb)) + data[211:]
        return int.from_bytes(hashlib.sha256(data).digest(), "big") % N

    def _gen(self, g, pub, priv, name):
        k = int.from_bytes(os.urandom(32), "big") % N or 1
        v = point_mul(k, g)
        c = self._h(g, v, pub, name)
        return v, (k - c * priv) % N

    def _verify(self, g, pub, v, r, name) -> bool:
        c = self._h(g, v, pub, name)
        return point_add(point_mul(c, pub), point_mul(r, g)) == v

    def round_one(self) -> bytes:
        self.x1 = int.from_bytes(os.urandom(32), "big") % N
        self.x2 = int.from_bytes(os.urandom(32), "big") % N
        self.g_x1 = point_mul(self.x1, G)
        self.g_x2 = point_mul(self.x2, G)
        v1, r1 = self._gen(G, self.g_x1, self.x1, self.role)
        v2, r2 = self._gen(G, self.g_x2, self.x2, self.role)
        return (encode_point(self.g_x1) + encode_zkp(v1, r1)
                + encode_point(self.g_x2) + encode_zkp(v2, r2))

    def read_round_one(self, d: bytes):
        if len(d) < 330:
            raise ValueError(f"RoundOne 数据长度异常: {len(d)}")
        self.g_x3 = decode_point(d[0:66])
        v1, r1 = decode_zkp(d[66:165])
        self.g_x4 = decode_point(d[165:231])
        v2, r2 = decode_zkp(d[231:330])
        if not self._verify(G, self.g_x3, v1, r1, self.peer_role):
            raise ValueError("ZKP g_x3 验证失败")
        if not self._verify(G, self.g_x4, v2, r2, self.peer_role):
            raise ValueError("ZKP g_x4 验证失败")

    def round_two(self) -> bytes:
        gxa = point_add(point_add(self.g_x1, self.g_x3), self.g_x4)
        y = int.from_bytes(os.urandom(16), "big") * N + int.from_bytes(self.secret, "big")
        x2s = (self.x2 * y) % N
        gs = point_mul(x2s, gxa)
        v, r = self._gen(gxa, gs, x2s, self.role)
        return encode_point(gs) + encode_zkp(v, r)

    def read_round_two(self, d: bytes) -> bytes:
        off = 3 if self.role == "client" else 0
        if len(d) < off + 165:
            raise ValueError(f"RoundTwo 数据长度异常: {len(d)}")
        gxb = point_add(point_add(self.g_x1, self.g_x2), self.g_x3)
        gs_peer = decode_point(d[off:off + 66])
        v, r = decode_zkp(d[off + 66:off + 165])
        if not self._verify(gxb, gs_peer, v, r, self.peer_role):
            raise ValueError("ZKP round2 验证失败")
        y = int.from_bytes(os.urandom(16), "big") * N + int.from_bytes(self.secret, "big")
        m = (self.x2 * y) % N
        tmp = point_add(gs_peer, point_neg(point_mul(m, self.g_x4)))
        vpt = point_mul(self.x2, tmp)
        return hashlib.sha256(vpt[0].to_bytes(32, "big")).digest()


# ==================== AES-GCM ====================
class Cipher:
    def __init__(self, key: bytes, nonce: bytes):
        if len(key) != 16:
            raise ValueError("AES-GCM key must be 16 bytes")
        if len(nonce) != 8:
            raise ValueError("AES-GCM nonce must be 8 bytes")
        self.aes = AESGCM(key)
        self.nonce = nonce
        self.enc = 1
        self.dec = 0

    def _iv(self, c):
        return self.nonce + struct.pack("<I", c)

    def encrypt(self, data: bytes) -> bytes:
        c = self.enc
        self.enc += 1
        return struct.pack("<I", c) + self.aes.encrypt(self._iv(c), data, None)

    def decrypt(self, data: bytes) -> bytes:
        if len(data) < 20:
            raise ValueError("Data too short")
        c = struct.unpack("<I", data[0:4])[0]
        if c <= self.dec:
            raise ValueError("Replay attack")
        plaintext = self.aes.decrypt(self._iv(c), data[4:], None)
        self.dec = c
        return plaintext


def compress(d: bytes) -> bytes:
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    return struct.pack("<I", len(d)) + co.compress(d) + co.flush()


MAX_DECOMPRESSED = 32 * 1024 * 1024


def decompress(d: bytes) -> bytes:
    if len(d) < 4:
        raise ValueError("压缩数据过短")
    expected = struct.unpack("<I", d[0:4])[0]
    if expected > MAX_DECOMPRESSED:
        raise ValueError("解压后数据过大")
    decompressor = zlib.decompressobj(-15)
    out = decompressor.decompress(d[4:], MAX_DECOMPRESSED + 1)
    out += decompressor.flush()
    if len(out) > MAX_DECOMPRESSED:
        raise ValueError("解压后数据过大")
    if len(out) != expected:
        raise ValueError("解压长度与声明不一致")
    return out


class DType:
    PROTOCOL_LIST = 1
    SELECTED_PROTOCOL = 2
    SESSION_KEY_EXCHANGE = 3
    ERROR = 4
    DATA = 5
    SERVER_PUB_KEY = 16
    ECJPAKE_ROUND_ONE = 32
    ECJPAKE_ROUND_TWO = 33


# ==================== 网关客户端 ====================
class MijiaGateway:
    def __init__(self, url: str = "http://192.168.1.100", logger=None,
                 push_callback=None):
        self.url = url.rstrip("/")
        self.ws = None
        self.cipher_out: Optional[Cipher] = None
        self.cipher_in: Optional[Cipher] = None
        self.req_id = 0
        self.pending: dict[int, asyncio.Future] = {}
        self._recv_task = None
        self._connecting = False
        self.connected = False
        self._logger = logger or (lambda m: None)
        self._push_cb = push_callback
        self.dev_list: dict = {}
        self.graph_list: list = []
        self.push_count = 0
        self.last_push: dict = {}

    def _log(self, m):
        self._logger(m)

    def ws_url(self) -> str:
        """按参考实现解析网关地址（保留路径前缀与显式端口）。"""
        parts = urlsplit(self.url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(
                f"网关地址必须以 http:// 或 https:// 开头: {self.url}")
        if not parts.hostname:
            raise ValueError(f"网关地址缺少主机名: {self.url}")
        scheme = "wss" if parts.scheme == "https" else "ws"
        netloc = parts.hostname
        if ":" in netloc:  # IPv6
            netloc = f"[{netloc}]"
        port = parts.port or (443 if parts.scheme == "https" else 80)
        netloc = f"{netloc}:{port}"
        path = (parts.path or "").rstrip("/")
        return f"{scheme}://{netloc}{path}/centrallinkws/"

    async def connect(self, passcode: str, timeout: float = 20.0):
        url = self.ws_url()
        self._log(f"连接 {url}")
        if self.connected:
            return True
        if self._connecting:
            raise RuntimeError("正在连接中")
        self._connecting = True
        try:
            # ping_interval/ping_timeout 用于发现半开连接
            self.ws = await asyncio.wait_for(
                websockets.connect(
                    url,
                    max_size=2 ** 24,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ),
                timeout=timeout)
            await self._handshake(passcode, timeout)
            self.connected = True
            self._log("✅ 安全会话已建立")
            return True
        except BaseException:
            # 握手失败/被取消时释放 socket，避免连接泄漏
            await self._abort_connect()
            raise
        finally:
            self._connecting = False

    async def _recv_frame(self, timeout: float) -> bytes:
        r = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        r = r.encode() if isinstance(r, str) else r
        if not r:
            raise ValueError("网关返回空帧")
        return r

    async def _handshake(self, passcode: str, timeout: float) -> None:
        # 1. 协议列表
        await self.ws.send(bytes([DType.PROTOCOL_LIST])
                           + json.dumps(["passcode"]).encode())

        # 2. 协议选择
        r = await self._recv_frame(timeout)
        r = r.encode() if isinstance(r, str) else r
        if r[0] != DType.SELECTED_PROTOCOL:
            raise ValueError(f"协议选择失败: {r[0]}")
        proto = json.loads(r[1:].decode()).get("protocol")
        self._log(f"协议: {proto}")
        if proto != "passcode":
            raise ValueError(f"不支持的协议: {proto}")

        # 3. ECJPAKE
        jp = ECJPAKE(passcode, "client")
        await self.ws.send(bytes([DType.ECJPAKE_ROUND_ONE]) + jp.round_one())

        r = await self._recv_frame(timeout)
        r = r.encode() if isinstance(r, str) else r
        if r[0] != DType.ECJPAKE_ROUND_ONE:
            raise ValueError(f"RoundOne 异常: {r[0]}")
        jp.read_round_one(r[1:])

        await self.ws.send(bytes([DType.ECJPAKE_ROUND_TWO]) + jp.round_two())

        r = await self._recv_frame(timeout)
        r = r.encode() if isinstance(r, str) else r
        if r[0] != DType.ECJPAKE_ROUND_TWO:
            raise ValueError(f"RoundTwo 异常: {r[0]}")
        shared = jp.read_round_two(r[1:])
        self._log("ECJPAKE 完成")

        # 4. 会话密钥交换
        sc = Cipher(shared[0:16], shared[16:24])
        my_kn = os.urandom(24)
        try:
            r = await self._recv_frame(3.0)
            r = r.encode() if isinstance(r, str) else r
            if r[0] != DType.SESSION_KEY_EXCHANGE:
                raise ValueError(f"会话密钥异常: {r[0]}")
            srv_kn = sc.decrypt(r[1:])
            await self.ws.send(bytes([DType.SESSION_KEY_EXCHANGE]) + sc.encrypt(my_kn))
            self._log("会话密钥：服务器先发")
        except asyncio.TimeoutError:
            await self.ws.send(bytes([DType.SESSION_KEY_EXCHANGE]) + sc.encrypt(my_kn))
            r = await self._recv_frame(timeout)
            r = r.encode() if isinstance(r, str) else r
            if r[0] != DType.SESSION_KEY_EXCHANGE:
                raise ValueError(f"会话密钥交换失败: {r[0]}")
            srv_kn = sc.decrypt(r[1:])
            self._log("会话密钥：客户端先发")

        self.cipher_out = Cipher(my_kn[0:16], my_kn[16:24])
        self.cipher_in = Cipher(srv_kn[0:16], srv_kn[16:24])
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self):
        try:
            async for raw in self.ws:
                raw = raw.encode() if isinstance(raw, str) else raw
                if not raw:
                    continue
                if raw[0] == DType.DATA:
                    if self.cipher_in is None:
                        continue
                    try:
                        msg = json.loads(
                            decompress(self.cipher_in.decrypt(raw[1:])).decode()
                        )
                        if "id" in msg:
                            rid = msg.get("id")
                            f = self.pending.pop(rid, None)
                            if f is not None and not f.done():
                                f.set_result(msg)
                            # 超时后晚到的响应/null-id 错误直接丢弃，不能当成推送
                        else:
                            await self._handle_push(msg)
                    except Exception as e:
                        self._log(f"解密失败: {e}")
                elif raw[0] == DType.ERROR:
                    self._log("网关返回错误帧")
        except Exception as e:
            self._log(f"接收循环结束: {type(e).__name__}")
        finally:
            self.connected = False
            for f in self.pending.values():
                if not f.done():
                    f.set_exception(ConnectionError("网关断开"))
            self.pending.clear()

    async def call(self, method: str, params: dict = None, timeout: float = 15.0):
        if not self.connected or self.ws is None or self.cipher_out is None:
            raise ConnectionError("未连接网关")
        self.req_id += 1
        rid = self.req_id
        req = {"jsonrpc": "2.0", "id": rid,
               "method": f"/api/{method}", "params": params or {}}
        enc = self.cipher_out.encrypt(compress(json.dumps(req).encode()))
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        try:
            await self.ws.send(bytes([DType.DATA]) + enc)
            resp = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            # 无论成功、超时还是发送失败都清理，避免 Future 泄漏
            self.pending.pop(rid, None)
            if not fut.done():
                fut.cancel()
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"{err.get('code')}: {err.get('message')}")
        return resp.get("result")

    # ---------- 便捷方法 ----------
    async def get_dev_list(self):
        r = await self.call("getDevList")
        self.dev_list = (r or {}).get("devList") or {}
        return self.dev_list

    @staticmethod
    def _normalize_rule(rule):
        """统一规则列表的 enabled/name/updateTime 字段。"""
        if not isinstance(rule, dict):
            return rule
        item = dict(rule)
        cfg = item.get("cfg") if isinstance(item.get("cfg"), dict) else {}
        user = item.get("userData")
        if not isinstance(user, dict):
            user = cfg.get("userData") if isinstance(cfg.get("userData"), dict) else {}
        if "enabled" not in item:
            if "enable" in item:
                item["enabled"] = bool(item["enable"])
            elif "enable" in cfg:
                item["enabled"] = bool(cfg["enable"])
        if not item.get("name"):
            item["name"] = user.get("name") or item.get("id")
        if "updateTime" not in item:
            item["updateTime"] = user.get("lastUpdateTime", 0)
        return item

    async def get_graph_list(self):
        r = await self.call("getGraphList")
        # 参考实现兼容 result 为数组或 {"rules": [...]} 两种形态
        if isinstance(r, dict):
            r = r.get("rules")
        self.graph_list = (
            [self._normalize_rule(item) for item in r]
            if isinstance(r, list) else []
        )
        return self.graph_list

    async def get_graph(self, gid: str):
        return await self.call("getGraph", {"id": gid})

    async def set_graph(self, gid: str, nodes: list, cfg: dict):
        return await self.call("setGraph", {"id": gid, "nodes": nodes, "cfg": cfg})

    async def change_graph_config(self, gid: str, enable: bool, user_data: dict):
        return await self.call("changeGraphConfig",
                               {"id": gid, "enable": enable, "userData": user_data})

    async def delete_graph(self, gid: str):
        return await self.call("deleteGraph", {"id": gid})

    async def get_log(self, num: int = 0):
        return await self.call("getLog", {"num": num})

    async def _handle_push(self, msg: dict):
        """处理网关推送（无 id 的消息）。"""
        method = msg.get("method", "")
        params = msg.get("params", {}) or {}
        self.push_count += 1
        self.last_push = {"method": method, "params": params}

        # 解析设备名
        did = str(params.get("did", ""))
        dev = self.dev_list.get(did, {})
        dev_name = dev.get("name") or dev.get("modelName") or did
        room = dev.get("roomName", "")

        kind = "unknown"
        summary = ""
        payload = {}
        if method.endswith("/push/property_changed"):
            kind = "property_changed"
            siid = params.get("siid")
            piid = params.get("piid")
            value = params.get("value")
            summary = f"{dev_name} {siid}.{piid} = {value}"
            payload = {"did": did, "siid": siid, "piid": piid, "value": value}
        elif method.endswith("/push/event_triggered"):
            kind = "event_triggered"
            siid = params.get("siid")
            eiid = params.get("eiid")
            args = params.get("arguments", [])
            summary = f"{dev_name} 事件 {siid}.{eiid} {args}"
            payload = {"did": did, "siid": siid, "eiid": eiid, "arguments": args}
        else:
            summary = str(msg)[:150]
            payload = params

        self._log(f"推送[{kind}] {summary}")

        if self._push_cb:
            try:
                result = self._push_cb({
                    "kind": kind,
                    "method": method,
                    "device_id": did,
                    "device_name": dev_name,
                    "room": room,
                    "model": dev.get("model", ""),
                    "summary": summary,
                    **payload,
                })
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                self._log(f"推送回调异常: {e}")

    async def health_check(self) -> bool:
        """发送轻量请求。

        返回 False 仅表示本次 RPC 失败，不代表 WebSocket 已断开；
        真实连接状态由 _recv_loop 维护（self.connected）。
        """
        if not self.connected or not self.ws:
            return False
        try:
            await self.call("getGraphList", {}, timeout=8)
            return True
        except Exception as e:
            self._log(f"健康检查失败: {type(e).__name__}: {e}")
            # 单次 RPC 超时不代表 socket 已断开；连接状态由 _recv_loop 维护
            return False

    # ---------- 变量 ----------
    async def get_var_scope_list(self):
        r = await self.call("getVarScopeList", {})
        if isinstance(r, dict):
            return r.get("scopes") or []
        return r or []

    async def get_var_list(self, scope: str = "global"):
        r = await self.call("getVarList", {"scope": scope})
        return r if isinstance(r, dict) else {}

    async def get_var_value(self, scope: str, var_id: str):
        """读取单个变量的值（网关返回 {"value": ...}）。"""
        r = await self.call("getVarValue",
                            {"scope": scope, "id": var_id})
        if isinstance(r, dict) and "value" in r:
            return r["value"]
        return r

    async def set_var_value(self, scope: str, var_id: str, value):
        return await self.call("setVarValue",
                               {"scope": scope, "id": var_id, "value": value})

    async def create_var(self, scope: str, var_id: str, vtype: str, value, name: str = ""):
        return await self.call("createVar", {
            "scope": scope, "id": var_id, "type": vtype,
            "value": value, "userData": {"name": name or var_id},
        })

    async def delete_var(self, scope: str, var_id: str):
        return await self.call("deleteVar", {"scope": scope, "id": var_id})

    # ---------- 备份（预留） ----------
    async def get_backup_list(self):
        return await self.call("getBackupList", {"from": "fds"})

    async def _abort_connect(self) -> None:
        """握手失败时回收接收任务与 WebSocket。"""
        self.connected = False
        task = self._recv_task
        self._recv_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        ws = self.ws
        self.ws = None
        self.cipher_out = None
        self.cipher_in = None
        if ws is not None:
            try:
                await asyncio.shield(ws.close())
            except (Exception, asyncio.CancelledError):
                pass

    async def close(self):
        self.connected = False
        task = self._recv_task
        self._recv_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        ws = self.ws
        self.ws = None
        self.cipher_out = None
        self.cipher_in = None
        if ws is not None:
            try:
                await asyncio.shield(ws.close())
            except (Exception, asyncio.CancelledError):
                pass


# ==================== 命令行测试 ====================
async def main():
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 gw_client.py <6位登录码> [网关URL]")
        return
    url = sys.argv[2] if len(sys.argv) > 2 else "http://192.168.1.100"
    gw = MijiaGateway(url, logger=print)
    try:
        await gw.connect(sys.argv[1])
        print("\n========== getDevList ==========")
        devs = await gw.get_dev_list()
        print(f"共 {len(devs)} 个设备")
        for did, d in list(devs.items())[:15]:
            print(f"  {d.get('name','?'):24s} {d.get('model','?'):34s} "
                  f"{'在线' if d.get('online') else '离线':4s} {d.get('roomName','')}")
        if len(devs) > 15:
            print(f"  ... 还有 {len(devs)-15} 个")

        print("\n========== getGraphList ==========")
        graphs = await gw.get_graph_list()
        print(f"共 {len(graphs)} 条规则")
        for g in graphs:
            print(f"  {g.get('id','?'):24s} {g.get('name','?'):30s} "
                  f"{'启用' if g.get('enabled') else '禁用'}")

        print("\n========== getLog ==========")
        try:
            log = await gw.get_log(0)
            print(str(log)[:800])
        except Exception as e:
            print(f"  ❌ {e}")
    finally:
        await gw.close()


if __name__ == "__main__":
    asyncio.run(main())
