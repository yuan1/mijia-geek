"""协议层回归测试。

运行：pytest -q
"""
from __future__ import annotations

import asyncio
import os

import pytest

from protocol.gateway import (
    Cipher, ECJPAKE, MijiaGateway, compress, decode_point, decompress,
)  # noqa: E402


def test_ws_url_requires_scheme():
    with pytest.raises(ValueError):
        MijiaGateway("192.168.1.100").ws_url()
    assert MijiaGateway("http://192.168.1.100/").ws_url() == \
        "ws://192.168.1.100:80/centrallinkws/"
    # 与参考实现一致：保留显式端口与路径前缀
    assert MijiaGateway("http://192.168.1.100:8080/prefix/").ws_url() == \
        "ws://192.168.1.100:8080/prefix/centrallinkws/"
    assert MijiaGateway("https://gw.local").ws_url() == \
        "wss://gw.local:443/centrallinkws/"


def test_ecjpake_roundtrip():
    client = ECJPAKE("123456", "client")
    server = ECJPAKE("123456", "server")

    client_round1 = client.round_one()
    server_round1 = server.round_one()
    server.read_round_one(client_round1)
    client.read_round_one(server_round1)

    client_round2 = client.round_two()
    server_round2 = server.round_two()

    # 客户端读取服务器 round2 时带 3 字节前缀（与服务端实现一致）
    client_secret = client.read_round_two(b"\x00\x00\x00" + server_round2)
    server_secret = server.read_round_two(client_round2)

    assert client_secret == server_secret
    assert len(client_secret) == 32


class _FakeCipher:
    def encrypt(self, data: bytes) -> bytes:
        return b""


class _FakeWS:
    async def send(self, data: bytes) -> None:
        return None


def test_compression_roundtrip():
    payload = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    assert decompress(compress(payload)) == payload


def test_cipher_roundtrip_and_replay_protection():
    key = os.urandom(16)
    nonce = os.urandom(8)
    sender = Cipher(key, nonce)
    receiver = Cipher(key, nonce)
    frame = sender.encrypt(b"hello")
    assert receiver.decrypt(frame) == b"hello"
    with pytest.raises(ValueError, match="Replay"):
        receiver.decrypt(frame)


def test_call_timeout_does_not_leak_pending():
    async def scenario():
        gw = MijiaGateway("http://127.0.0.1")
        gw.connected = True
        gw.ws = _FakeWS()
        gw.cipher_out = _FakeCipher()
        with pytest.raises(asyncio.TimeoutError):
            await gw.call("getDevList", timeout=0.01)
        return gw

    gw = asyncio.run(scenario())
    assert gw.pending == {}


def test_response_shapes_match_reference():
    async def scenario():
        gw = MijiaGateway("http://127.0.0.1")

        async def fake_call(method, params=None, timeout=None):
            return {
                "getGraphList": {"rules": [{"id": "r1"}]},
                "getVarValue": {"value": 26},
                "getVarScopeList": {"scopes": ["global", "rule_r1"]},
                "getDevList": {"devList": {"dev1": {"name": "灯"}}},
            }[method]

        gw.call = fake_call
        assert await gw.get_graph_list() == [{"id": "r1", "name": "r1", "updateTime": 0}]
        assert await gw.get_var_value("global", "v1") == 26
        assert await gw.get_var_scope_list() == ["global", "rule_r1"]
        assert await gw.get_dev_list() == {"dev1": {"name": "灯"}}

    asyncio.run(scenario())


def test_cipher_does_not_advance_counter_on_bad_tag():
    key = os.urandom(16)
    nonce = os.urandom(8)
    sender = Cipher(key, nonce)
    receiver = Cipher(key, nonce)
    frame = sender.encrypt(b"hello")
    tampered = frame[:-1] + bytes([frame[-1] ^ 0xFF])
    with pytest.raises(Exception):
        receiver.decrypt(tampered)
    assert receiver.dec == 0
    assert receiver.decrypt(frame) == b"hello"


def test_point_and_compression_validation():
    with pytest.raises(ValueError):
        decode_point(bytes([65, 4]) + (1).to_bytes(32, "big")
                     + (1).to_bytes(32, "big"))
    frame = compress(b"abc")
    bad = (4).to_bytes(4, "little") + frame[4:]
    with pytest.raises(ValueError, match="长度"):
        decompress(bad)
