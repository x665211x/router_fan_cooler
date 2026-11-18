"""select：控制方式切换（恒温闭环 / 温度曲线 / 阶梯分段）+ 运行模式（自动 / 手动）。"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    LABEL_TO_STRATEGY,
    MODE_AUTO,
    MODE_MANUAL,
    STRATEGY_LABELS,
    signal_update,
)
from .manager import CoolerManager

MODE_LABELS = {
    MODE_AUTO: "自动（按所选控制方式）",
    MODE_MANUAL: "手动占空比",
}
MODE_BY_LABEL = {v: k for k, v in MODE_LABELS.items()}


def _device_info(entry: ConfigEntry, manager: CoolerManager) -> dict:
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": manager.device_info_name,
        "manufacturer": "DIY（米家 BLE Mesh PWM 灯控模块改装）",
        "model": "路由器散热底座",
    }


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    manager: CoolerManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CoolerStrategySelect(entry, manager), CoolerModeSelect(entry, manager)])


class _BaseSelect(SelectEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, manager: CoolerManager, slug: str, name: str) -> None:
        self._entry = entry
        self._manager = manager
        self._slug = slug
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{slug}"
        self._attr_device_info = _device_info(entry, manager)

    async def async_added_to_hass(self) -> None:
        if not self.entity_id:
            self.entity_id = f"select.router_fan_cooler_{self._slug}"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self._entry.entry_id), self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class CoolerStrategySelect(_BaseSelect):
    """控制方式：恒温闭环（推荐）/ 温度曲线 / 阶梯分段。改完立刻按新方式重算，并写回配置。"""

    _attr_icon = "mdi:auto-fix"

    def __init__(self, entry, manager) -> None:
        super().__init__(entry, manager, "strategy", "控制方式")
        self._attr_options = [STRATEGY_LABELS[s] for s in STRATEGY_LABELS]

    @property
    def current_option(self) -> str:
        return self._manager.strategy_label

    async def async_select_option(self, option: str) -> None:
        strategy = LABEL_TO_STRATEGY.get(option)
        if strategy is None:
            raise ValueError(f"未知控制方式：{option}")
        await self._manager.async_set_strategy(strategy)
        self.async_write_ha_state()


class CoolerModeSelect(_BaseSelect):
    """运行模式：自动（按控制方式）/ 手动固定占空比。没有「关闭」——风扇永不停转。"""

    _attr_icon = "mdi:fan-auto"

    def __init__(self, entry, manager) -> None:
        super().__init__(entry, manager, "mode", "风扇模式")
        self._attr_options = list(MODE_LABELS.values())

    @property
    def current_option(self) -> str:
        return MODE_LABELS.get(self._manager.mode, MODE_LABELS[MODE_AUTO])

    async def async_select_option(self, option: str) -> None:
        mode = MODE_BY_LABEL.get(option)
        if mode is None:
            raise ValueError(f"未知模式：{option}")
        await self._manager.async_set_mode(mode)
        self.async_write_ha_state()