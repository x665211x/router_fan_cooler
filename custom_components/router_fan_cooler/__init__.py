"""路由器散热底座风扇控制（独立集成）。

装好后你会得到：
    sensor        当前占空比 / CPU 温度 / 状态
    binary_sensor 风扇是否在转
    number        目标温度、容差、曲线起止温度、最低转速、手动占空比
    select        控制方式（恒温/曲线/阶梯）、模式（自动 / 手动）

服务：
    router_fan_cooler.set_speed      立即按指定占空比运行（切手动）
    router_fan_cooler.set_mode       切换模式（auto / manual）
    router_fan_cooler.set_strategy   切换控制方式（thermostat / curve / step）
    router_fan_cooler.recalculate    立刻重算一次（用于调试/广播给自动化）
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    MODE_MANUAL,
    MODES,
    PLATFORMS,
    SERVICE_RECALCULATE,
    SERVICE_SET_MODE,
    SERVICE_SET_SPEED,
    SERVICE_SET_STRATEGY,
    STRATEGIES,
)
from .manager import CoolerManager

_LOGGER = logging.getLogger(__name__)

SERVICE_ENTITY_FIELD = "entity_id_optional"

SERVICE_SET_SPEED_SCHEMA = vol.Schema(
    {
        vol.Required("percentage"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        vol.Optional(SERVICE_ENTITY_FIELD): selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=selector.EntityFilterSelectorConfig(domain="sensor", integration=DOMAIN)
            )
        ),
    }
)

SERVICE_SET_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("mode"): vol.In(MODES),
        vol.Optional(SERVICE_ENTITY_FIELD): selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=selector.EntityFilterSelectorConfig(domain="sensor", integration=DOMAIN)
            )
        ),
    }
)

SERVICE_SET_STRATEGY_SCHEMA = vol.Schema(
    {
        vol.Required("strategy"): vol.In(STRATEGIES),
        vol.Optional(SERVICE_ENTITY_FIELD): selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=selector.EntityFilterSelectorConfig(domain="sensor", integration=DOMAIN)
            )
        ),
    }
)

SERVICE_RECALC_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_ENTITY_FIELD): selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=selector.EntityFilterSelectorConfig(domain="sensor", integration=DOMAIN)
            )
        ),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """集成级别初始化（只注册服务，具体条目在 async_setup_entry）。"""
    hass.data.setdefault(DOMAIN, {})

    async def _select_managers(call: ServiceCall) -> list[CoolerManager]:
        """指定了实体就只作用于那一台，否则作用于全部（多台时等价于广播）。"""
        data = hass.data.get(DOMAIN, {})
        entry_map: dict[str, CoolerManager] = {
            k: v for k, v in data.items() if isinstance(v, CoolerManager)
        }
        if not entry_map:
            raise ValueError("尚未配置任何散热底座")

        target = call.data.get(SERVICE_ENTITY_FIELD)
        if not target:
            return list(entry_map.values())

        # ① 走实体注册表 → 设备 → 设备标识符（最稳）
        registry = er.async_get(hass)
        reg_entry = registry.async_get(target)
        if reg_entry is not None and reg_entry.device_id:
            device = dr.async_get(hass).async_get(reg_entry.device_id)
            if device is not None:
                for entry_id, mgr in entry_map.items():
                    if (DOMAIN, entry_id) in device.identifiers:
                        return [mgr]

        # ② 兜底：用我们 sensor 暴露的 entry_id 属性反查
        state = hass.states.get(target)
        if state is not None:
            wanted = state.attributes.get("entry_id")
            for entry_id, mgr in entry_map.items():
                if wanted == entry_id:
                    return [mgr]

        raise ValueError(f"找不到该实体对应的散热底座条目：{target}")

    async def _handle_set_speed(call: ServiceCall) -> None:
        for mgr in await _select_managers(call):
            await mgr.async_set_manual_duty(call.data["percentage"])
            await mgr.async_set_mode(MODE_MANUAL)

    async def _handle_set_mode(call: ServiceCall) -> dict[str, Any]:
        results = {}
        for mgr in await _select_managers(call):
            await mgr.async_set_mode(call.data["mode"])
            results[mgr.entry.entry_id] = {
                "mode": mgr.mode,
                "duty": mgr.current_duty,
            }
        return results

    async def _handle_set_strategy(call: ServiceCall) -> dict[str, Any]:
        results = {}
        for mgr in await _select_managers(call):
            await mgr.async_set_strategy(call.data["strategy"])
            results[mgr.entry.entry_id] = {
                "strategy": mgr.strategy,
                "duty": mgr.current_duty,
            }
        return results

    async def _handle_recalculate(call: ServiceCall) -> dict[str, Any]:
        results = {}
        for mgr in await _select_managers(call):
            await mgr._async_recalculate(force=True)  # noqa: SLF001
            results[mgr.entry.entry_id] = {
                "strategy": mgr.strategy,
                "temperature": mgr.temperature,
                "duty": mgr.current_duty,
                "applied": mgr.applied_duty,
                "state": mgr.state_text,
            }
        return results

    if not hass.services.has_service(DOMAIN, SERVICE_SET_SPEED):
        hass.services.async_register(
            DOMAIN, SERVICE_SET_SPEED, _handle_set_speed, schema=SERVICE_SET_SPEED_SCHEMA
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_MODE,
            _handle_set_mode,
            schema=SERVICE_SET_MODE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_STRATEGY,
            _handle_set_strategy,
            schema=SERVICE_SET_STRATEGY_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_RECALCULATE,
            _handle_recalculate,
            schema=SERVICE_RECALC_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    manager = CoolerManager(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = manager
    await manager.async_setup()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        manager: CoolerManager | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if manager is not None:
            await manager.async_shutdown()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """修改选项后热更新，不用重启 HA。"""
    manager: CoolerManager | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if manager is not None:
        manager.update_options(dict(entry.options))
        await manager._async_recalculate(force=True)  # noqa: SLF001
    else:
        await hass.config_entries.async_reload(entry.entry_id)
