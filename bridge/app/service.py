"""网关连接与轮询业务逻辑（与 FastAPI 解耦）。"""
from __future__ import annotations

import asyncio
import logging

from config import SCAN_INTERVAL
from protocol import MijiaGateway
from state import AppState

_LOGGER = logging.getLogger("mijia_bridge")

CONNECT_TIMEOUT = 45.0


class NotConnectedError(RuntimeError):
    """没有可用的网关会话。"""


class GatewayService:
    def __init__(self, state: AppState) -> None:
        self.state = state

    # ---------- 会话 ----------
    def ensure_gateway(self) -> MijiaGateway:
        gateway = self.state.gateway
        if not self.state.connected or gateway is None or not gateway.connected:
            raise NotConnectedError("网关未连接")
        return gateway

    def _publish_connection(self) -> None:
        self.state.publish_event({
            "kind": "connection",
            "connected": self.state.connected,
            "last_error": self.state.last_error,
        })

    def mark_disconnected(self, reason: str) -> None:
        if self.state.connected:
            _LOGGER.warning("网关已断开: %s", reason)
        self.state.connected = False
        self.state.passcode = ""
        self.state.last_error = reason
        self._publish_connection()

    async def connect(self, code: str,
                      gateway_url: str | None = None) -> tuple[bool, str]:
        """连接网关。

        先建立新连接，成功后才替换并关闭旧连接；失败时保留旧会话。
        """
        # 必须在 await lock 之前抢占 busy 标志，否则并发调用会排队后全部重连。
        if self.state.connecting or self.state.lock.locked():
            return False, "正在连接中，请稍候"
        self.state.connecting = True
        try:
            async with self.state.lock:
                self.state.last_error = ""
                old = self.state.gateway
                url = (gateway_url or self.state.gateway_url).rstrip("/")
                gateway: MijiaGateway | None = None
                try:
                    gateway = MijiaGateway(
                        url,
                        logger=_LOGGER.info,
                        push_callback=self._on_push,
                    )
                    async with asyncio.timeout(CONNECT_TIMEOUT):
                        await gateway.connect(code)
                        # 立即拉一次数据验证
                        devices = await gateway.get_dev_list()
                        rules = await gateway.get_graph_list()

                    self.state.gateway = gateway
                    self.state.gateway_url = url
                    self.state.devices = devices or {}
                    self.state.rules = rules or []
                    self.state.passcode = code
                    self.state.connected = True
                    self.state.last_error = ""
                    self.state.save()

                    if old is not None and old is not gateway:
                        try:
                            await old.close()
                        except Exception:
                            pass

                    _LOGGER.info("✅ 连接成功: %d 设备, %d 规则",
                                 len(self.state.devices), len(self.state.rules))
                    self._publish_connection()
                    return True, ""
                except asyncio.CancelledError:
                    if gateway is not None and self.state.gateway is not gateway:
                        try:
                            await gateway.close()
                        except Exception:
                            pass
                    raise
                except Exception as err:
                    if gateway is not None:
                        try:
                            await gateway.close()
                        except Exception:
                            pass
                    msg = f"{type(err).__name__}: {err}"
                    self.state.last_error = msg
                    # 旧连接若仍可用则保持可用状态
                    self.state.connected = bool(old is not None and old.connected)
                    _LOGGER.error("连接失败: %s", msg)
                    self._publish_connection()
                    return False, msg
        finally:
            self.state.connecting = False

    # ---------- 推送 ----------
    def _on_push(self, data: dict) -> None:
        self.state.publish_push(data)
        _LOGGER.info("推送: %s", data.get("summary", ""))

    # ---------- 轮询 ----------
    def start_poll(self) -> None:
        task = self.state.poll_task
        if task is not None and not task.done():
            return
        self.state.poll_task = asyncio.create_task(self._poll_loop())

    async def stop_poll(self) -> None:
        task = self.state.poll_task
        self.state.poll_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _poll_loop(self) -> None:
        """后台轮询：只刷新数据，绝不自动重连（登录码一次性）。"""
        while True:
            try:
                await asyncio.sleep(SCAN_INTERVAL)
                gateway = self.state.gateway
                if gateway is None or not self.state.connected:
                    continue
                if not gateway.connected:
                    if self.state.gateway is gateway:
                        self.mark_disconnected("网关连接已断开，请重新输入登录码")
                    continue

                try:
                    devices = await gateway.get_dev_list()
                    rules = await gateway.get_graph_list() or []
                    last_log = self.state.last_log
                    try:
                        log = await gateway.get_log(0)
                        if log:
                            lines = [ln for ln in str(log).split("\n") if ln.strip()]
                            last_log = lines[-1] if lines else ""
                    except Exception:
                        pass

                    # 轮询期间可能已经切换到新连接，丢弃过期结果
                    if self.state.gateway is not gateway or not gateway.connected:
                        continue
                    self.state.devices = devices or {}
                    self.state.rules = rules
                    self.state.last_log = last_log
                    self.state.last_error = ""
                    _LOGGER.debug("轮询完成: %d 设备, %d 规则",
                                  len(self.state.devices), len(self.state.rules))
                except Exception as err:
                    # 单次 RPC 失败不等于连接断开，先做健康检查再决定
                    _LOGGER.warning("轮询失败: %s", err)
                    if self.state.gateway is not gateway:
                        continue
                    self.state.last_error = f"{type(err).__name__}: {err}"
                    if not await gateway.health_check() and not gateway.connected:
                        if self.state.gateway is gateway:
                            self.mark_disconnected("网关连接已断开，请重新输入登录码")
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error("轮询循环异常: %s", err)
                await asyncio.sleep(10)

    # ---------- 显式刷新 ----------
    async def refresh(self) -> tuple[int, int]:
        gateway = self.ensure_gateway()
        devices = await gateway.get_dev_list() or {}
        rules = await gateway.get_graph_list() or []
        if self.state.gateway is not gateway:
            raise NotConnectedError("连接已切换，请重试")
        self.state.devices = devices
        self.state.rules = rules
        return len(self.state.devices), len(self.state.rules)

    async def shutdown(self) -> None:
        await self.stop_poll()
        gateway = self.state.gateway
        if gateway is not None and self.state.connected:
            self.state.connected = False
            self.state.passcode = ""
            self._publish_connection()
        self.state.subscribers.clear()
        if gateway is not None:
            try:
                await gateway.close()
            except Exception:
                pass
