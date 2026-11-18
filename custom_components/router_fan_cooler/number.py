"""number：恒温参数 / 曲线温度 / 最低转速 / 手动占空比（卡片上直接可调）。"""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, HARD_MIN_DUTY, signal_update
from .manager import CoolerManager

DEVICE = lambda entry, manager: {  # noqa: E731
    "identifiers": {(DOMAIN, entry.entry_id)},
    "name": manager.device_info_name,
    "manufacturer": "DIY（米家 BLE Mesh PWM 灯控模块改装）",
    "model": "路由器散热底座",
}


class _BaseNumber(NumberEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: ConfigEntry, manager: CoolerManager, key: str, name: str) -> None:
        self._entry = entry
        self._manager = manager
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._slug = key
        self._attr_device_info = DEVICE(entry, manager)

    async def async_added_to_hass(self) -> None:
        # 固定英文 entity_id（中文名会让 HA 生成一串无意义 ID）；已在注册表里存在则不动
        if not self.entity_id:
            self.entity_id = f"number.router_fan_cooler_{self._slug}"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self._entry.entry_id), self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class TargetTempNumber(_BaseNumber):
    """恒温闭环目标温度：风扇会把 CPU 温度夹在这个值 ±容差 内（默认 40℃）。"""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_min_value = 20
    _attr_native_max_value = 70
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "℃"
    _attr_icon = "mdi:thermostat"

    def __init__(self, entry, manager) -> None:
        super().__init__(entry, manager, "target_temp", "目标温度")

    @property
    def native_value(self) -> float:
        return self._manager.target

    async def async_set_native_value(self, value: float) -> None:
        self._manager.async_set_target_temp(value)


class DeadbandNumber(_BaseNumber):
    """恒温容差 ±℃：进这个范围就不动转速（默认 1.0，即 40±1℃）。"""

    _attr_native_min_value = 0.5
    _attr_native_max_value = 3
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "±℃"
    _attr_icon = "mdi:thermometer-lines"

    def __init__(self, entry, manager) -> None:
        super().__init__(entry, manager, "deadband", "恒温容差")

    @property
    def native_value(self) -> float:
        return self._manager.deadband

    async def async_set_native_value(self, value: float) -> None:
        self._manager.async_set_deadband(value)


class MinTempNumber(_BaseNumber):
    """曲线/阶梯起点温度：到这个温度就用最低转速（默认 38℃）。"""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_min_value = 25
    _attr_native_max_value = 74.5
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "℃"
    _attr_icon = "mdi:thermometer-low"

    def __init__(self, entry, manager) -> None:
        super().__init__(entry, manager, "min_temp", "曲线起点温度")

    @property
    def native_value(self) -> float:
        return self._manager.min_temp

    async def async_set_native_value(self, value: float) -> None:
        self._manager.async_set_min_temp(value)


class MaxTempNumber(_BaseNumber):
    """曲线/阶梯终点温度：到这个温度就用最高转速（默认 48℃）。"""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_min_value = 25.5
    _attr_native_max_value = 75
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "℃"
    _attr_icon = "mdi:thermometer-high"

    def __init__(self, entry, manager) -> None:
        super().__init__(entry, manager, "max_temp", "曲线终点温度")

    @property
    def native_value(self) -> float:
        return self._manager.max_temp

    async def async_set_native_value(self, value: float) -> None:
        self._manager.async_set_max_temp(value)


class MinDutyNumber(_BaseNumber):
    """最低转速：模块低于这个占空比风扇不转；硬下限 20%，不允许更低。"""

    _attr_native_min_value = HARD_MIN_DUTY
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:speedometer-slow"

    def __init__(self, entry, manager) -> None:
        super().__init__(entry, manager, "min_duty", "最低转速")

    @property
    def native_value(self) -> int:
        return self._manager.min_duty

    async def async_set_native_value(self, value: float) -> None:
        self._manager.async_set_min_duty(int(value))


class ManualDutyNumber(_BaseNumber):
    """手动占空比：固定转速，最低仍为 20%，不会停转。"""

    _attr_native_min_value = HARD_MIN_DUTY
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:percent"

    def __init__(self, entry, manager) -> None:
        super().__init__(entry, manager, "manual_duty", "手动占空比")

    @property
    def native_value(self) -> int:
        return self._manager.manual_duty

    async def async_set_native_value(self, value: float) -> None:
        await self._manager.async_set_manual_duty(int(value))
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    manager: CoolerManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            TargetTempNumber(entry, manager),
            DeadbandNumber(entry, manager),
            MinTempNumber(entry, manager),
            MaxTempNumber(entry, manager),
            MinDutyNumber(entry, manager),
            ManualDutyNumber(entry, manager),
        ]
    )