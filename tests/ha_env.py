"""测试辅助：加载 HA 集成模块。

``ha_integration/mijia_geek/__init__.py`` 会导入完整的 Home Assistant 运行时，
而本仓库的测试环境只安装 Bridge 依赖。这里注入一组最小桩模块，并绕过
``mijia_geek/__init__.py`` 直接加载被测子模块（bridge / coordinator）。
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

INTEGRATION_DIR = (Path(__file__).resolve().parents[1]
                   / "ha_integration" / "mijia_geek")

# async_dispatcher_send 记录，供测试断言
DISPATCHED: list[tuple[str, tuple]] = []


class ConfigEntryAuthFailed(Exception):
    pass


class ConfigEntryError(Exception):
    pass


class UpdateFailed(Exception):
    pass


class DataUpdateCoordinator:
    """DataUpdateCoordinator 的最小桩。"""

    def __init__(self, hass, logger=None, *, name=None,
                 update_interval=None, **kwargs) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data = None
        self.refresh_requests = 0
        self.listener_updates = 0

    async def async_request_refresh(self) -> None:
        self.refresh_requests += 1

    async def async_refresh(self):
        self.refresh_requests += 1
        self.data = await self._async_update_data()
        return self.data

    async def async_config_entry_first_refresh(self):
        self.data = await self._async_update_data()
        return self.data

    def async_update_listeners(self) -> None:
        self.listener_updates += 1


def install_ha_stubs() -> None:
    if "homeassistant" in sys.modules:
        return

    ha = types.ModuleType("homeassistant")
    ha.__path__ = []

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D401 - 桩
        pass

    core.HomeAssistant = HomeAssistant

    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    exceptions.ConfigEntryError = ConfigEntryError

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []

    dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")

    def async_dispatcher_send(hass, signal, *args) -> None:
        DISPATCHED.append((signal, args))

    dispatcher.async_dispatcher_send = async_dispatcher_send

    update_coordinator = types.ModuleType(
        "homeassistant.helpers.update_coordinator")
    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed

    helpers.dispatcher = dispatcher
    helpers.update_coordinator = update_coordinator
    ha.core = core
    ha.exceptions = exceptions
    ha.helpers = helpers
    sys.modules.update({
        "homeassistant": ha,
        "homeassistant.core": core,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.dispatcher": dispatcher,
        "homeassistant.helpers.update_coordinator": update_coordinator,
    })


def load_integration_module(name: str):
    """加载 ``ha_integration/mijia_geek/<name>.py``，不执行包 __init__。"""
    install_ha_stubs()
    package = sys.modules.get("mijia_geek")
    if package is None:
        package = types.ModuleType("mijia_geek")
        package.__path__ = [str(INTEGRATION_DIR)]
        sys.modules["mijia_geek"] = package
    return importlib.import_module(f"mijia_geek.{name}")
