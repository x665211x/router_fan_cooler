"""诊断数据：排错时一键下载，看配置和当前运行态。"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_MANAGER, DOMAIN, VERSION
from .manager import CoolerManager


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    manager: CoolerManager | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    cfg = {**entry.data, **entry.options}
    cfg.pop(DATA_MANAGER, None)
    payload = {
        "version": VERSION,
        "entry_id": entry.entry_id,
        "config": {k: v for k, v in cfg.items() if "password" not in k.lower()},
    }
    if manager is not None:
        payload["runtime"] = {
            "mode": manager.mode,
            "temperature": manager.temperature,
            "current_duty": manager.current_duty,
            "applied_duty": manager.applied_duty,
            "state": manager.state_text,
            "last_error": manager.last_error,
            "last_change": manager.last_change.isoformat()
            if manager.last_change
            else None,
            "strategy": manager.strategy,
            "target_temp": manager.target,
            "deadband": manager.deadband,
            "min_temp": manager.min_temp,
            "max_temp": manager.max_temp,
            "min_duty": manager.min_duty,
            "max_duty": manager.max_duty,
        }
        temp_state = hass.states.get(manager.temp_sensor)
        fan_state = hass.states.get(manager.fan_entity)
        payload["entities"] = {
            "temperature_source": {
                "entity_id": manager.temp_sensor,
                "state": temp_state.state if temp_state else None,
                "last_updated": temp_state.last_updated.isoformat()
                if temp_state and temp_state.last_updated
                else None,
            },
            "fan_control": {
                "entity_id": manager.fan_entity,
                "state": fan_state.state if fan_state else None,
                "brightness": fan_state.attributes.get("brightness")
                if fan_state
                else None,
                "percentage": fan_state.attributes.get("percentage")
                if fan_state
                else None,
            },
        }
    return payload
