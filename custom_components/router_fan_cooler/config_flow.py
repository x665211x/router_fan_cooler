"""配置向导：先选控制方式，再填该方式需要的参数。

三种控制方式（添加集成时选择，之后可在「选项」或 select 实体里热切换）：
    thermostat 恒温闭环（默认）
        目标温度 + 容差：40±1℃ 内保持转速，超出按偏差增量加/减速。
    curve      温度曲线
        两点线性：min_temp → 最低转速，max_temp → 最高转速。
    step       阶梯分段
        min_temp~max_temp 分 4 档，每档固定占空比，风扇不会细碎变速。

任何方式都不停转：最低转速硬下限 20%（模块 20% 以下不转）。
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_BAND,
    CONF_CONTROL_TYPE,
    CONF_DEADBAND,
    CONF_FAILSAFE,
    CONF_FAN_ENTITY,
    CONF_MAX_DUTY,
    CONF_MAX_STEP,
    CONF_MAX_TEMP,
    CONF_MIN_CHANGE,
    CONF_MIN_DUTY,
    CONF_MIN_TEMP,
    CONF_STOP_TEMP,
    CONF_STRATEGY,
    CONF_TARGET_TEMP,
    CONF_TEMP_SENSOR,
    CONF_UPDATE_INTERVAL,
    CONTROL_FAN,
    CONTROL_LIGHT,
    CONTROL_SWITCH,
    DEFAULT_DEADBAND,
    DEFAULT_FAILSAFE,
    DEFAULT_MAX_DUTY,
    DEFAULT_MAX_STEP,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_CHANGE,
    DEFAULT_MIN_DUTY,
    DEFAULT_MIN_TEMP,
    DEFAULT_STRATEGY,
    DEFAULT_TARGET_TEMP,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    HARD_MIN_DUTY,
    NAME,
    STRATEGIES,
    STRATEGY_LABELS,
    STRATEGY_THERMOSTAT,
)

_LOGGER = logging.getLogger(__name__)

TEMP_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(
        filter=selector.EntityFilterSelectorConfig(domain="sensor"), multiple=False
    )
)
FAN_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(
        filter=selector.EntityFilterSelectorConfig(domain=["light", "fan", "switch"]),
        multiple=False,
    )
)
STRATEGY_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            {"value": STRATEGY_THERMOSTAT, "label": STRATEGY_LABELS[STRATEGY_THERMOSTAT]},
            {"value": "curve", "label": STRATEGY_LABELS["curve"]},
            {"value": "step", "label": STRATEGY_LABELS["step"]},
        ],
        mode=selector.SelectSelectorMode.LIST,
    )
)
FAILSAFE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            {"value": "max", "label": "温度读不到 → 全速散热（推荐）"},
            {"value": "hold", "label": "温度读不到 → 保持上次转速"},
        ],
        mode=selector.SelectSelectorMode.LIST,
        translation_key="failsafe",
    )
)


def _guess_temp_sensor(hass) -> str | None:
    """尽量帮用户先选好 CPU 温度实体：优先 cpu+temp，其次带 router 的。"""
    candidates = []
    for entity_id in hass.states.async_entity_ids("sensor"):
        low = entity_id.lower()
        if "temp" not in low:
            continue
        score = 0
        if "cpu" in low:
            score += 3
        if "router" in low or "luyou" in low:
            score += 2
        if score:
            candidates.append((-score, entity_id))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _control_type_for(entity_id: str) -> str:
    domain = (entity_id or "").split(".", 1)[0]
    if domain == "fan":
        return CONTROL_FAN
    if domain == "switch":
        return CONTROL_SWITCH
    return CONTROL_LIGHT


def _target_default(current: dict[str, Any]) -> float:
    """老配置迁移：优先沿用 target_temp，其次 1.x 的 min_temp / stop_temp。"""
    for key in (CONF_TARGET_TEMP, CONF_MIN_TEMP, CONF_STOP_TEMP):
        if key in current:
            try:
                return float(current[key])
            except (TypeError, ValueError):
                continue
    return DEFAULT_TARGET_TEMP


def _strategy_default(current: dict[str, Any]) -> str:
    value = current.get(CONF_STRATEGY)
    return value if value in STRATEGIES else DEFAULT_STRATEGY


def _number(default, minimum, maximum, step=1, unit="", mode=None):
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=mode or selector.NumberSelectorMode.BOX,
        )
    )


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_STRATEGY, default=_strategy_default(defaults)
            ): STRATEGY_SELECTOR,
            vol.Required(
                CONF_TEMP_SENSOR, default=defaults.get(CONF_TEMP_SENSOR)
            ): TEMP_SELECTOR,
            vol.Required(
                CONF_FAN_ENTITY, default=defaults.get(CONF_FAN_ENTITY)
            ): FAN_SELECTOR,
            vol.Required(
                CONF_TARGET_TEMP, default=_target_default(defaults)
            ): _number(40, 20, 70, 0.5, "℃"),
            vol.Required(
                CONF_DEADBAND, default=float(defaults.get(CONF_DEADBAND, DEFAULT_DEADBAND))
            ): _number(1, 0.5, 3, 0.5, "±℃"),
            vol.Required(
                CONF_MIN_TEMP, default=float(defaults.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP))
            ): _number(38, 25, 74.5, 0.5, "℃"),
            vol.Required(
                CONF_MAX_TEMP, default=float(defaults.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP))
            ): _number(48, 25.5, 75, 0.5, "℃"),
            vol.Required(
                CONF_MIN_DUTY, default=max(HARD_MIN_DUTY, int(defaults.get(CONF_MIN_DUTY, DEFAULT_MIN_DUTY)))
            ): _number(20, HARD_MIN_DUTY, 100, 1, "%"),
            vol.Required(
                CONF_MAX_DUTY, default=max(HARD_MIN_DUTY, int(defaults.get(CONF_MAX_DUTY, DEFAULT_MAX_DUTY)))
            ): _number(100, HARD_MIN_DUTY, 100, 1, "%"),
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=int(defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)),
            ): _number(10, 5, 300, 1, "秒"),
            vol.Required(
                CONF_FAILSAFE, default=defaults.get(CONF_FAILSAFE, DEFAULT_FAILSAFE)
            ): FAILSAFE_SELECTOR,
        }
    )


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if int(user_input[CONF_MIN_DUTY]) > int(user_input[CONF_MAX_DUTY]):
        errors[CONF_MIN_DUTY] = "min_gt_max"
    if float(user_input[CONF_MIN_TEMP]) >= float(user_input[CONF_MAX_TEMP]):
        errors[CONF_MAX_TEMP] = "temp_order"
    if user_input[CONF_TEMP_SENSOR] == user_input[CONF_FAN_ENTITY]:
        errors[CONF_FAN_ENTITY] = "same_entity"
    if int(user_input[CONF_MIN_DUTY]) < HARD_MIN_DUTY:
        errors[CONF_MIN_DUTY] = "duty_too_low"
    return errors


def _normalize(user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    strategy = data.get(CONF_STRATEGY, DEFAULT_STRATEGY)
    data[CONF_STRATEGY] = strategy if strategy in STRATEGIES else DEFAULT_STRATEGY
    data[CONF_TARGET_TEMP] = float(data[CONF_TARGET_TEMP])
    data[CONF_DEADBAND] = float(data.get(CONF_DEADBAND, DEFAULT_DEADBAND))
    data[CONF_MIN_TEMP] = float(data.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP))
    data[CONF_MAX_TEMP] = float(data.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP))
    data[CONF_MIN_DUTY] = max(HARD_MIN_DUTY, int(data[CONF_MIN_DUTY]))
    data[CONF_MAX_DUTY] = max(data[CONF_MIN_DUTY], int(data[CONF_MAX_DUTY]))
    data[CONF_UPDATE_INTERVAL] = int(data[CONF_UPDATE_INTERVAL])
    data[CONF_CONTROL_TYPE] = _control_type_for(data[CONF_FAN_ENTITY])
    # 内部调参，界面不给
    data.setdefault(CONF_MIN_CHANGE, DEFAULT_MIN_CHANGE)
    data.setdefault(CONF_MAX_STEP, DEFAULT_MAX_STEP)
    # 老键清掉：v1.x 的停转/调节带/允许停转都不再生效
    for legacy in (CONF_STOP_TEMP, CONF_BAND, "allow_stop"):
        data.pop(legacy, None)
    return data


class RouterFanCoolerConfigFlow(ConfigFlow, domain=DOMAIN):
    """配置入口。"""

    VERSION = 4

    @staticmethod
    async def async_migrate_entry(hass, entry: ConfigEntry) -> bool:
        """老配置 → v4：补上控制方式与曲线温度，默认沿用「恒温闭环」。"""
        if entry.version >= 4:
            return True
        old = {**entry.data, **entry.options}
        target = _target_default(old)
        new_data = {
            k: v
            for k, v in old.items()
            if k not in (CONF_STOP_TEMP, CONF_BAND)
        }
        new_data[CONF_STRATEGY] = _strategy_default(old)
        new_data[CONF_TARGET_TEMP] = float(target)
        new_data.setdefault(CONF_DEADBAND, DEFAULT_DEADBAND)
        new_data.setdefault(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)
        new_data.setdefault(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)
        new_data.setdefault(CONF_MIN_DUTY, HARD_MIN_DUTY)
        new_data.setdefault(CONF_MAX_DUTY, DEFAULT_MAX_DUTY)
        new_data.setdefault(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        new_data.setdefault(CONF_FAILSAFE, DEFAULT_FAILSAFE)
        new_data.setdefault(CONF_CONTROL_TYPE, _control_type_for(new_data[CONF_FAN_ENTITY]))
        new_data.setdefault(CONF_MIN_CHANGE, DEFAULT_MIN_CHANGE)
        new_data.setdefault(CONF_MAX_STEP, DEFAULT_MAX_STEP)
        new_data.pop("allow_stop", None)
        _LOGGER.info(
            "router_fan_cooler 配置迁移：%s → v4（控制方式=%s，恒温 %.1f℃±%.1f℃，曲线 %.1f~%.1f℃）",
            entry.version,
            new_data[CONF_STRATEGY],
            new_data[CONF_TARGET_TEMP],
            new_data[CONF_DEADBAND],
            new_data[CONF_MIN_TEMP],
            new_data[CONF_MAX_TEMP],
        )
        hass.config_entries.async_update_entry(entry, data=new_data, options={}, version=4)
        return True

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        defaults: dict[str, Any] = dict(user_input or {})
        if user_input is None:
            guessed = _guess_temp_sensor(self.hass)
            if guessed:
                defaults[CONF_TEMP_SENSOR] = guessed
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                data = _normalize(user_input)
                await self.async_set_unique_id(
                    f"{data[CONF_TEMP_SENSOR]}|{data[CONF_FAN_ENTITY]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=NAME, data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(defaults),
            errors=errors,
            description_placeholders={
                "tip": "① 先选控制方式：恒温闭环（推荐）/ 温度曲线 / 阶梯分段，"
                "三种都不停转、最低 20%。"
                "② 温度实体选 xmluyouqiwenkong 的 CPU 温度，风扇实体选灯控模块（多为 light）。"
                "③ 目标温度+容差只给恒温闭环用（默认 40℃±1℃）；"
                "曲线起止温度只给温度曲线/阶梯分段用（默认 38~48℃）。"
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RouterFanCoolerOptionsFlow()


class RouterFanCoolerOptionsFlow(OptionsFlow):
    """参数热调整：换控制方式、改目标温度/曲线温度/转速，保存即生效。"""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(title="", data=_normalize(user_input))
        return self.async_show_form(
            step_id="init", data_schema=_schema(current), errors=errors
        )