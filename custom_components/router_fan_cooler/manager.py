"""风扇控制核心逻辑。

职责：
    1. 订阅 CPU 温度传感器（来自 xmluyouqiwenkong 集成）；
    2. 按所选控制方式算占空比：
         thermostat 恒温闭环 / curve 温度曲线 / step 阶梯分段；
    3. 下发到灯控模块实体（light 亮度 / fan 百分比 / switch 开关）；
    4. 温度失联时按配置兜底（默认全速），绝不停转；
    5. 对外暴露状态给 sensor / binary_sensor / number / select。

硬约束：
    min_duty 永远 ≥ 20%（HARD_MIN_DUTY），任何路径（手动、失联、曲线、
    阶梯、恒温）都不会下发 0 或 1~19%，因为该模块在这个区间风扇不转。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .control_math import clamp_duty, compute_curve, compute_duty, compute_step, limit_step
from .const import (
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
    CONF_STRATEGY,
    CONF_TARGET_TEMP,
    CONF_TEMP_SENSOR,
    CONF_UPDATE_INTERVAL,
    CONTROL_FAN,
    CONTROL_LIGHT,
    CONTROL_SWITCH,
    DEFAULT_DEADBAND,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_STALE_SECONDS,
    DEFAULT_STRATEGY,
    DEFAULT_TARGET_TEMP,
    DUTY_GAIN_PER_C,
    FAILSAFE_HOLD,
    HARD_MIN_DUTY,
    MODE_AUTO,
    MODE_MANUAL,
    STEP_GEARS,
    STRATEGIES,
    STRATEGY_CURVE,
    STRATEGY_LABELS,
    STRATEGY_STEP,
    signal_update,
)

_LOGGER = logging.getLogger(__name__)

# 目标温度允许的范围（配置向导与 number 实体同源）
TARGET_MIN = 20.0
TARGET_MAX = 70.0
TEMP_POINT_MIN = 25.0
TEMP_POINT_MAX = 75.0


class CoolerManager:
    """一个 config entry 对应一个管理器（一台路由器 + 一个散热底座）。"""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        cfg = {**entry.data, **entry.options}

        self.temp_sensor: str = cfg[CONF_TEMP_SENSOR]
        self.fan_entity: str = cfg[CONF_FAN_ENTITY]
        self.control_type: str = cfg[CONF_CONTROL_TYPE]

        # 控制方式
        strategy = cfg.get(CONF_STRATEGY, DEFAULT_STRATEGY)
        self.strategy: str = strategy if strategy in STRATEGIES else DEFAULT_STRATEGY

        # 恒温参数（仅 thermostat 使用）
        self.target: float = float(cfg.get(CONF_TARGET_TEMP, self._legacy_target(cfg)))
        self.deadband: float = max(0.0, float(cfg.get(CONF_DEADBAND, DEFAULT_DEADBAND)))

        # 曲线/阶梯参数
        self.min_temp: float = float(cfg.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP))
        self.max_temp: float = float(cfg.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP))
        self._fix_temp_range()

        # 转速：最低转速硬性 ≥20%，且 ≤ 最高
        self.min_duty: int = max(HARD_MIN_DUTY, int(cfg[CONF_MIN_DUTY]))
        self.max_duty: int = max(self.min_duty, int(cfg[CONF_MAX_DUTY]))
        self.min_change: int = int(cfg[CONF_MIN_CHANGE])
        self.max_step: int = int(cfg[CONF_MAX_STEP])
        self.interval: int = int(cfg[CONF_UPDATE_INTERVAL])
        self.failsafe: str = cfg[CONF_FAILSAFE]

        # 运行态
        self.mode: str = MODE_AUTO
        self.manual_duty: int = self.min_duty
        self.current_duty: int = self.min_duty     # 期望占空比（常转，初始即最低转速）
        self.applied_duty: int | None = None       # 已成功下发的占空比
        self.temperature: float | None = None
        self.state_text: str = "初始化"
        self.last_error: str | None = None
        self.last_change: Any = None
        self._listeners: list = []
        self._unsub_interval = None

    # ------------------------------------------------------------------ 参数兜底
    def _fix_temp_range(self) -> None:
        """曲线/阶梯的起点必须低于终点，至少差 0.5℃。"""
        self.min_temp = max(TEMP_POINT_MIN - 5, min(TEMP_POINT_MAX - 0.5, float(self.min_temp)))
        if self.max_temp <= self.min_temp:
            self.max_temp = self.min_temp + 0.5
        self.max_temp = max(self.min_temp + 0.5, min(TEMP_POINT_MAX, float(self.max_temp)))

    @property
    def strategy_label(self) -> str:
        return STRATEGY_LABELS.get(self.strategy, STRATEGY_LABELS[DEFAULT_STRATEGY])

    @staticmethod
    def _legacy_target(cfg: dict) -> float:
        """老配置迁移：target_temp → 1.x 的 min_temp / stop_temp → 默认 40℃。"""
        for key in (CONF_TARGET_TEMP, CONF_MIN_TEMP, "stop_temp"):
            if key in cfg:
                try:
                    return float(cfg[key])
                except (TypeError, ValueError):
                    continue
        return DEFAULT_TARGET_TEMP

    # ------------------------------------------------------------------ 生命周期
    async def async_setup(self) -> None:
        self._listeners = [
            async_track_state_change_event(
                self.hass, [self.temp_sensor], self._handle_temp_event
            )
        ]
        self._unsub_interval = async_track_time_interval(
            self.hass, self._handle_tick, timedelta(seconds=self.interval)
        )
        self._read_temperature()
        await self._async_recalculate(force=True)

    async def async_shutdown(self) -> None:
        for unsub in self._listeners:
            unsub()
        self._listeners = []
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None

    # ------------------------------------------------------------------ 事件回调
    @callback
    def _handle_temp_event(self, event) -> None:
        new_state = event.data.get("new_state")
        self.temperature = self._to_float(new_state)
        self.hass.async_create_task(self._async_recalculate())

    async def _handle_tick(self, now=None) -> None:
        """周期兜底：温度实体没变化也要保证按配置执行一次（含失效保护）。"""
        self._read_temperature()
        await self._async_recalculate()

    # ------------------------------------------------------------------ 温度读取
    def _read_temperature(self) -> None:
        state = self.hass.states.get(self.temp_sensor)
        self.temperature = self._to_float(state)

    @staticmethod
    def _to_float(state) -> float | None:
        if state is None or state.state in ("unknown", "unavailable", "", None):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _temp_is_stale(self) -> bool:
        state = self.hass.states.get(self.temp_sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            return True
        stamp = getattr(state, "last_reported", None) or getattr(
            state, "last_updated", None
        )
        if stamp is None:
            return False
        age = (dt_util.utcnow() - stamp).total_seconds()
        return age > DEFAULT_STALE_SECONDS

    # ------------------------------------------------------------------ 计算与下发
    def _desired_duty(self) -> int:
        # ① 手动：固定占空比（同样不低于最低转速，不会停转）
        if self.mode == MODE_MANUAL:
            duty = clamp_duty(self.manual_duty, self.min_duty, self.max_duty)
            self.state_text = f"手动固定（{duty}%）"
            return duty

        # ② 温度失联：全速或保持，绝不停转
        if self.temperature is None or self._temp_is_stale():
            if self.failsafe == FAILSAFE_HOLD:
                self.state_text = "温度失联·保持上次转速"
                return clamp_duty(self.current_duty, self.min_duty, self.max_duty)
            self.state_text = "温度失联·全速保护"
            return self.max_duty

        # ③ 按所选控制方式
        temp = self.temperature
        if self.strategy == STRATEGY_CURVE:
            duty = compute_curve(temp, self.min_temp, self.max_temp, self.min_duty, self.max_duty)
            self.state_text = self._curve_state_text(temp, duty)
            return duty
        if self.strategy == STRATEGY_STEP:
            duty, gear = compute_step(
                temp, self.min_temp, self.max_temp,
                self.min_duty, self.max_duty, gears=STEP_GEARS,
            )
            self.state_text = self._step_state_text(temp, duty, gear)
            return duty

        duty = compute_duty(
            temp,
            self.target,
            self.deadband,
            self.min_duty,
            self.max_duty,
            previous_duty=self.current_duty,
            gain=DUTY_GAIN_PER_C,
        )
        self.state_text = self._auto_state_text(duty)
        return duty

    def _auto_state_text(self, duty: int) -> str:
        """恒温模式：把「偏差方向 + 当前转速」写成人话。"""
        assert self.temperature is not None
        temp = self.temperature
        error = temp - self.target
        if abs(error) <= self.deadband:
            return (
                f"恒温保持（{temp:.1f}℃，偏差 {error:+.1f}℃ 在 ±{self.deadband:.1f} 内，"
                f"保持 {duty}%）"
            )
        if duty >= self.max_duty:
            return (
                f"已满载（{temp:.1f}℃，仍超目标 {error:+.1f}℃，"
                f"散热不足，请检查风扇/风道）"
            )
        if error > 0:
            return f"加速降温（{temp:.1f}℃，超目标 {error:+.1f}℃ → 加到 {duty}%）"
        return f"降速保温（{temp:.1f}℃，低于目标 {abs(error):.1f}℃ → 降到 {duty}%）"

    def _curve_state_text(self, temp: float, duty: int) -> str:
        base = f"曲线调速（{temp:.1f}℃，{self.min_temp:.1f}~{self.max_temp:.1f}℃ → {duty}%）"
        if duty >= self.max_duty:
            return f"曲线满载（{temp:.1f}℃，已达上限 {duty}%）"
        if duty <= self.min_duty:
            return f"曲线最低档（{temp:.1f}℃，保底 {duty}%）"
        return base

    def _step_state_text(self, temp: float, duty: int, gear: int) -> str:
        return (
            f"阶梯 {gear}/{STEP_GEARS} 档（{temp:.1f}℃，"
            f"{self.min_temp:.1f}~{self.max_temp:.1f}℃ → {duty}%）"
        )

    async def _async_recalculate(self, force: bool = False) -> None:
        target = self._desired_duty()
        stepped = target if force else limit_step(self.current_duty, target, self.max_step)
        # 硬下限保护：永远不低于最低转速（≥20%），不存在停转分支
        stepped = clamp_duty(stepped, self.min_duty, self.max_duty)
        # 防抖：变化太小且不是因为模式切换，则不下发
        delta = abs(stepped - (self.applied_duty if self.applied_duty is not None else -999))
        if force or delta >= self.min_change:
            self.current_duty = stepped
            await self._async_apply(self.current_duty)
        else:
            self.current_duty = stepped
        self._notify()

    def _notify(self) -> None:
        """通知所有实体刷新状态。"""
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    async def _async_apply(self, duty: int) -> None:
        """按实体类型下发占空比，失败只记日志不抛出（下一周期会重试）。

        注意：duty 恒 ≥20%，这里没有任何 turn_off 路径——风扇永远不停。
        """
        domain = self.fan_entity.split(".", 1)[0]
        duty = clamp_duty(duty, self.min_duty, self.max_duty)
        try:
            if domain == "light" or self.control_type == CONTROL_LIGHT:
                await self.hass.services.async_call(
                    "light",
                    "turn_on",
                    {"entity_id": self.fan_entity, "brightness_pct": duty},
                    blocking=True,
                )
            elif domain == "fan" or self.control_type == CONTROL_FAN:
                await self.hass.services.async_call(
                    "fan", "turn_on", {"entity_id": self.fan_entity}, blocking=True
                )
                await self.hass.services.async_call(
                    "fan",
                    "set_percentage",
                    {"entity_id": self.fan_entity, "percentage": duty},
                    blocking=True,
                )
            elif domain == "switch" or self.control_type == CONTROL_SWITCH:
                # 开关型模块没有 PWM，只能开/关；保持常开（等效全速）
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self.fan_entity}, blocking=True
                )
            else:
                raise ValueError(f"不支持的实体：{self.fan_entity}")
        except Exception as err:  # noqa: BLE001  —— 设备离线/网关超时都可能抛
            self.last_error = f"{type(err).__name__}: {err}"
            self.state_text = "下发失败（将重试）"
            _LOGGER.warning("向 %s 下发占空比 %s%% 失败：%s", self.fan_entity, duty, err)
            return
        self.applied_duty = duty
        self.last_change = dt_util.utcnow()
        self.last_error = None

    # ------------------------------------------------------------------ 外部设置
    async def async_set_mode(self, mode: str) -> None:
        if mode not in (MODE_AUTO, MODE_MANUAL):
            raise ValueError(f"非法模式：{mode}（本集成不允许关闭风扇）")
        self.mode = mode
        await self._async_recalculate(force=True)

    async def async_set_strategy(self, strategy: str, persist: bool = True) -> None:
        """切换控制方式：恒温闭环 / 温度曲线 / 阶梯分段。"""
        if strategy not in STRATEGIES:
            raise ValueError(f"未知控制方式：{strategy}")
        self.strategy = strategy
        if persist:
            new_options = {**self.entry.options, CONF_STRATEGY: strategy}
            self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        await self._async_recalculate(force=True)

    async def async_set_manual_duty(self, duty: int) -> None:
        self.manual_duty = clamp_duty(duty, self.min_duty, self.max_duty)
        if self.mode == MODE_MANUAL:
            await self._async_recalculate(force=True)

    def async_set_target_temp(self, value: float) -> None:
        """恒温目标温度（手动可调，默认 40℃）。"""
        self.target = max(TARGET_MIN, min(TARGET_MAX, float(value)))
        self.hass.async_create_task(self._async_recalculate(force=True))

    def async_set_deadband(self, value: float) -> None:
        """恒温容差 ±℃，默认 1.0（40±1 → 39~41 不动转速）。"""
        self.deadband = max(0.0, min(5.0, float(value)))
        self.hass.async_create_task(self._async_recalculate(force=True))

    def async_set_min_temp(self, value: float) -> None:
        """曲线/阶梯起点温度。"""
        self.min_temp = max(TEMP_POINT_MIN, min(TEMP_POINT_MAX - 0.5, float(value)))
        if self.min_temp >= self.max_temp:
            self.max_temp = self.min_temp + 0.5
        self.hass.async_create_task(self._async_recalculate(force=True))

    def async_set_max_temp(self, value: float) -> None:
        """曲线/阶梯终点温度。"""
        self.max_temp = max(TEMP_POINT_MIN + 0.5, min(TEMP_POINT_MAX, float(value)))
        if self.max_temp <= self.min_temp:
            self.min_temp = self.max_temp - 0.5
        self.hass.async_create_task(self._async_recalculate(force=True))

    def async_set_min_duty(self, value: int) -> None:
        """最低转速：模块低于这个占空比风扇不转；硬下限 20%。"""
        value = max(HARD_MIN_DUTY, min(100, int(value)))
        if value > self.max_duty:
            self.max_duty = value
        self.min_duty = value
        if self.mode == MODE_MANUAL:
            self.manual_duty = clamp_duty(self.manual_duty, self.min_duty, self.max_duty)
        self.hass.async_create_task(self._async_recalculate(force=True))

    def update_options(self, options: dict) -> None:
        """重新配置后热更新参数（不重启条目）。"""
        cfg = {**self.entry.data, **options}
        old_temp_sensor = self.temp_sensor
        old_interval = self.interval
        self.temp_sensor = cfg.get(CONF_TEMP_SENSOR, self.temp_sensor)
        self.fan_entity = cfg.get(CONF_FAN_ENTITY, self.fan_entity)
        self.control_type = cfg.get(CONF_CONTROL_TYPE, self.control_type)
        strategy = cfg.get(CONF_STRATEGY, self.strategy)
        self.strategy = strategy if strategy in STRATEGIES else self.strategy
        self.target = float(cfg.get(CONF_TARGET_TEMP, self._legacy_target(cfg)))
        self.deadband = max(0.0, float(cfg.get(CONF_DEADBAND, self.deadband)))
        self.min_temp = float(cfg.get(CONF_MIN_TEMP, self.min_temp))
        self.max_temp = float(cfg.get(CONF_MAX_TEMP, self.max_temp))
        self._fix_temp_range()
        self.min_duty = max(HARD_MIN_DUTY, int(cfg.get(CONF_MIN_DUTY, self.min_duty)))
        self.max_duty = max(self.min_duty, int(cfg.get(CONF_MAX_DUTY, self.max_duty)))
        self.min_change = int(cfg.get(CONF_MIN_CHANGE, self.min_change))
        self.max_step = int(cfg.get(CONF_MAX_STEP, self.max_step))
        self.interval = int(cfg.get(CONF_UPDATE_INTERVAL, self.interval))
        self.failsafe = cfg.get(CONF_FAILSAFE, self.failsafe)
        self.manual_duty = clamp_duty(self.manual_duty, self.min_duty, self.max_duty)

        # 换了温度实体或改了周期，订阅要跟着换，否则还会盯着旧实体
        if old_temp_sensor != self.temp_sensor or old_interval != self.interval:
            self._read_temperature()
            self._resubscribe()

    def _resubscribe(self) -> None:
        """重建「温度状态订阅 + 周期任务」。"""
        for unsub in self._listeners:
            unsub()
        self._listeners = []
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None
        self._listeners = [
            async_track_state_change_event(
                self.hass, [self.temp_sensor], self._handle_temp_event
            )
        ]
        self._unsub_interval = async_track_time_interval(
            self.hass, self._handle_tick, timedelta(seconds=self.interval)
        )

    # ------------------------------------------------------------------ 给实体读的属性
    @property
    def device_info_name(self) -> str:
        return f"路由器散热底座（{self.fan_entity}）"