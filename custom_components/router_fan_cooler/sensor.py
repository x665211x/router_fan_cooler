"""传感器平台：占空比 / CPU 温度 / 控制状态。"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, signal_update
from .manager import CoolerManager


def _device_info(entry: ConfigEntry, manager: CoolerManager) -> dict:
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": manager.device_info_name,
        "manufacturer": "DIY（米家 BLE Mesh PWM 灯控模块改装）",
        "model": "路由器散热底座",
    }


class _BaseCoolerSensor(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, manager: CoolerManager, key: str, name: str) -> None:
        self._entry = entry
        self._manager = manager
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._slug = key
        self._attr_device_info = _device_info(entry, manager)

    async def async_added_to_hass(self) -> None:
        # 固定英文 entity_id（中文名会让 HA 生成一串无意义 ID）；已在注册表里存在则不动
        if not self.entity_id:
            self.entity_id = f"sensor.router_fan_cooler_{self._slug}"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self._entry.entry_id), self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class CoolerDutySensor(_BaseCoolerSensor):
    """当前占空比（PWM 目标值）。"""

    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:percent"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry, manager) -> None:
        super().__init__(entry, manager, "duty", "风扇占空比")

    @property
    def native_value(self) -> int:
        return int(self._manager.current_duty)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "entry_id": self._entry.entry_id,
            "实际已下发": self._manager.applied_duty,
            "模式": self._manager.mode,
            "控制方式": self._manager.strategy,
            "控制方式说明": self._manager.strategy_label,
            "目标温度": self._manager.target,
            "恒温容差": self._manager.deadband,
            "曲线起点温度": self._manager.min_temp,
            "曲线终点温度": self._manager.max_temp,
            "最低转速": self._manager.min_duty,
            "最高转速": self._manager.max_duty,
            "控制实体": self._manager.fan_entity,
            "温度传感器": self._manager.temp_sensor,
        }


class CoolerTempSensor(_BaseCoolerSensor):
    """CPU 温度（镜像源传感器，方便一个卡片看全）。"""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = "℃"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry, manager) -> None:
        super().__init__(entry, manager, "cpu_temp", "CPU 温度")

    @property
    def native_value(self) -> float | None:
        return self._manager.temperature

    @property
    def extra_state_attributes(self) -> dict:
        return {"源实体": self._manager.temp_sensor}


class CoolerStatusSensor(_BaseCoolerSensor):
    """控制状态文字。"""

    _attr_icon = "mdi:fan-auto"

    def __init__(self, entry, manager) -> None:
        super().__init__(entry, manager, "status", "控制状态")

    @property
    def native_value(self) -> str:
        return self._manager.state_text

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "上次错误": self._manager.last_error,
            "上次调节时间": self._manager.last_change.isoformat()
            if self._manager.last_change
            else None,
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    manager: CoolerManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            CoolerDutySensor(entry, manager),
            CoolerTempSensor(entry, manager),
            CoolerStatusSensor(entry, manager),
        ]
    )
