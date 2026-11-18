"""binary_sensor：风扇是否在转（按已下发的占空比判断）。"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, signal_update
from .manager import CoolerManager


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    manager: CoolerManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CoolerRunningBinarySensor(entry, manager)])


class CoolerRunningBinarySensor(BinarySensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "风扇运转中"
    _attr_icon = "mdi:fan"

    def __init__(self, entry: ConfigEntry, manager: CoolerManager) -> None:
        self._entry = entry
        self._manager = manager
        self._slug = "running"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": manager.device_info_name,
            "manufacturer": "DIY（米家 BLE Mesh PWM 灯控模块改装）",
            "model": "路由器散热底座",
        }

    async def async_added_to_hass(self) -> None:
        # 固定英文 entity_id（中文名会让 HA 生成一串无意义 ID）；已在注册表里存在则不动
        if not self.entity_id:
            self.entity_id = "binary_sensor.router_fan_cooler_running"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self._entry.entry_id), self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        applied = self._manager.applied_duty
        return bool(applied) and applied > 0
