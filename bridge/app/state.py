"""桥接服务运行时状态与推送广播。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from config import ENV_GATEWAY_URL, GATEWAY_URL, STATE_FILE
from protocol import MijiaGateway

_LOGGER = logging.getLogger("mijia_bridge")

_MAX_SUBSCRIBER_QUEUE = 100


class AppState:
    """全局状态。

    · 登录码只存在内存中，绝不落盘；
    · state.json 只保存网关地址；
    · 推送通过 asyncio.Queue 广播给 SSE 订阅者。
    """

    def __init__(self) -> None:
        self.gateway: MijiaGateway | None = None
        self.gateway_url = GATEWAY_URL
        self.passcode = ""
        self.devices: dict = {}
        self.rules: list = []
        self.last_log: str = ""
        self.connected = False
        self.last_push: dict = {}
        self.connecting = False
        self.last_error = ""
        self.push_seq = 0
        self.last_connect_at = 0.0
        self.subscribers: set[asyncio.Queue] = set()
        self.poll_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    # ---------- 持久化 ----------
    def save(self) -> None:
        """只持久化网关地址；登录码绝不写入磁盘。"""
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"gateway_url": self.gateway_url}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, STATE_FILE)
            try:
                os.chmod(STATE_FILE, 0o600)
            except OSError:
                pass
        except Exception as err:
            _LOGGER.warning("保存状态失败: %s", err)

    def load(self) -> None:
        try:
            if not STATE_FILE.exists():
                return
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if ENV_GATEWAY_URL:
                _LOGGER.info("使用配置的网关地址: %s", self.gateway_url)
            else:
                url = data.get("gateway_url")
                if isinstance(url, str) and url.strip():
                    self.gateway_url = url.strip().rstrip("/")
            if "passcode" in data:
                _LOGGER.info("检测到旧版持久化登录码，已忽略并清理（登录码一次性）")
                self.save()
            _LOGGER.info("已恢复网关地址: %s", self.gateway_url)
        except Exception as err:
            _LOGGER.warning("读取状态失败: %s", err)

    # ---------- 推送广播 ----------
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_SUBSCRIBER_QUEUE)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def publish_event(self, data: dict[str, Any]) -> int:
        self.push_seq += 1
        event = {**data, "seq": self.push_seq}
        for queue in list(self.subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return self.push_seq

    def publish_push(self, data: dict[str, Any]) -> int:
        seq = self.publish_event(data)
        self.last_push = {**data, "seq": seq}
        return seq
