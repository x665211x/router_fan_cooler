"""假 Home Assistant 跑全套控制方式（不需要真 HA、不需要真路由器）。

校验：
    恒温闭环 / 温度曲线 / 阶梯分段 各自动作是否正确；
    手动模式、控制方式切换、参数热更新；
    任何路径都不会下发 0%，也不会低于 20%；
    温度失联兜底、下发失败重试、light / fan / switch 三种实体路径。

用法：
    python tools\\mock_ha_test.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent / "custom_components" / "router_fan_cooler"
sys.path.insert(0, str(PKG))


def _make_package() -> None:
    pkg = types.ModuleType("router_fan_cooler")
    pkg.__path__ = [str(PKG)]
    sys.modules["router_fan_cooler"] = pkg


def _install_stubs() -> None:
    ha = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    helpers = types.ModuleType("homeassistant.helpers")
    dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")
    event = types.ModuleType("homeassistant.helpers.event")
    util = types.ModuleType("homeassistant.util")
    dt = types.ModuleType("homeassistant.util.dt")

    core.HomeAssistant = object
    core.callback = lambda f: f
    dispatcher.async_dispatcher_send = lambda *a, **k: None
    dispatcher.async_dispatcher_connect = lambda *a, **k: (lambda: None)
    event.async_track_state_change_event = lambda *a, **k: (lambda: None)
    event.async_track_time_interval = lambda *a, **k: (lambda: None)
    dt.utcnow = lambda: datetime.now(timezone.utc)

    for name, mod in {
        "homeassistant": ha,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.dispatcher": dispatcher,
        "homeassistant.helpers.event": event,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt,
    }.items():
        sys.modules.setdefault(name, mod)
    helpers.dispatcher = dispatcher
    helpers.event = event
    util.dt = dt


class FakeState:
    def __init__(self, state: str, age_seconds: float = 0):
        stamp = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        self.state = state
        self.last_updated = stamp
        self.last_reported = stamp
        self.attributes = {}


class FakeStates:
    def __init__(self):
        self._d = {}

    def set(self, entity_id, state):
        self._d[entity_id] = state

    def get(self, entity_id):
        return self._d.get(entity_id)


class FakeServices:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def async_call(self, domain, service, data=None, blocking=False, **kw):
        self.calls.append((domain, service, dict(data or {})))
        if self.fail:
            raise RuntimeError("网关超时（模拟）")


class FakeEntry:
    def __init__(self, data):
        self.entry_id = "test_entry"
        self.data = data
        self.options = {}

    def add_update_listener(self, fn):
        return lambda: None

    def async_on_unload(self, fn):
        return None


CFG = {
    "temp_sensor": "sensor.router_cpu_temp",
    "fan_entity": "light.fan_pwm",
    "control_type": "light",
    "strategy": "thermostat",
    "target_temp": 40.0,
    "deadband": 1.0,
    "min_temp": 38.0,
    "max_temp": 48.0,
    "min_duty": 20,
    "max_duty": 100,
    "min_change": 0,
    "max_step": 15,
    "update_interval": 10,
    "failsafe": "max",
}


def _new_manager(extra=None):
    from router_fan_cooler.manager import CoolerManager

    class FakeConfigEntries:
        def __init__(self):
            self.updates = []

        def async_update_entry(self, entry, **kw):
            self.updates.append(kw)
            if "options" in kw:
                entry.options = dict(kw["options"])

    hass = types.SimpleNamespace(
        states=FakeStates(),
        services=FakeServices(),
        data={},
        config_entries=FakeConfigEntries(),
        async_create_task=lambda c: c.close(),
    )
    cfg = dict(CFG)
    if extra:
        cfg.update(extra)
    entry = FakeEntry(cfg)
    return hass, CoolerManager(hass, entry)


async def scenario() -> int:
    checks = []

    def expect(name, cond, detail=""):
        checks.append((name, bool(cond), detail))
        print(("  [OK] " if cond else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))

    # ---------- A. 恒温闭环 ----------
    hass, mgr = _new_manager()
    hass.states.set("sensor.router_cpu_temp", FakeState("30.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("30℃ → 保底 20%", mgr.current_duty == 20, f"duty={mgr.current_duty}")
    expect(
        "下发 light.turn_on brightness_pct=20",
        hass.services.calls[-1][2].get("brightness_pct") == 20,
        str(hass.services.calls[-1]),
    )
    expect("状态含『降速保温』", "降速保温" in mgr.state_text, mgr.state_text)

    hass.states.set("sensor.router_cpu_temp", FakeState("45.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("45℃ → 52%", mgr.current_duty == 52, f"duty={mgr.current_duty}")
    expect("状态含『加速降温』", "加速降温" in mgr.state_text, mgr.state_text)

    hass.states.set("sensor.router_cpu_temp", FakeState("41.2"))
    mgr._read_temperature()
    await mgr._async_recalculate()
    expect("41.2℃ → 54%", mgr.current_duty == 54, f"duty={mgr.current_duty}")

    hass.states.set("sensor.router_cpu_temp", FakeState("40.6"))
    mgr._read_temperature()
    await mgr._async_recalculate()
    expect("40.6℃ 容差内 → 保持 54%", mgr.current_duty == 54, f"duty={mgr.current_duty}")
    expect("状态含『恒温保持』", "恒温保持" in mgr.state_text, mgr.state_text)

    hass.states.set("sensor.router_cpu_temp", FakeState("38.5"))
    mgr._read_temperature()
    await mgr._async_recalculate()
    expect("38.5℃ → 50%", mgr.current_duty == 50, f"duty={mgr.current_duty}")

    hass.states.set("sensor.router_cpu_temp", FakeState("30.0"))
    mgr._read_temperature()
    await mgr._async_recalculate()
    expect("30℃ 单步限幅 → 35%", mgr.current_duty == 35, f"duty={mgr.current_duty}")

    hass.states.set("sensor.router_cpu_temp", FakeState("80.0"))
    mgr._read_temperature()
    await mgr._async_recalculate()
    expect("80℃ 单步限幅 → 50%", mgr.current_duty == 50, f"duty={mgr.current_duty}")
    await mgr._async_recalculate()
    expect("再算 → 65%", mgr.current_duty == 65, f"duty={mgr.current_duty}")
    await mgr._async_recalculate()
    expect("第三次 → 80%", mgr.current_duty == 80, f"duty={mgr.current_duty}")

    # ---------- B. 手动模式（同样不会停转） ----------
    await mgr.async_set_manual_duty(40)
    await mgr.async_set_mode("manual")
    expect("手动 40% 生效", mgr.current_duty == 40, f"duty={mgr.current_duty}")
    expect(
        "手动态 brightness_pct=40",
        hass.services.calls[-1][2].get("brightness_pct") == 40,
        str(hass.services.calls[-1]),
    )
    await mgr.async_set_manual_duty(5)
    expect("手动设 5% 被硬抬到 20%", mgr.current_duty == 20, f"duty={mgr.current_duty}")

    rejected = False
    try:
        await mgr.async_set_mode("off")
    except ValueError:
        rejected = True
    expect("不允许切到 off（无停转模式）", rejected)

    # ---------- C. 失联 / 下发失败 ----------
    hass, mgr = _new_manager()
    hass.states.set("sensor.router_cpu_temp", FakeState("30.0", age_seconds=600))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("温度过期 → 全速 100%", mgr.current_duty == 100, f"duty={mgr.current_duty}")
    expect("状态含『失联』", "失联" in mgr.state_text, mgr.state_text)

    hass, mgr = _new_manager()
    hass.services.fail = True
    hass.states.set("sensor.router_cpu_temp", FakeState("45.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("报错时 applied_duty 仍为空", mgr.applied_duty is None, str(mgr.applied_duty))
    expect("报错被记录", "RuntimeError" in (mgr.last_error or ""), str(mgr.last_error))
    expect("报错后提示重试", "下发失败" in mgr.state_text, mgr.state_text)

    # ---------- D. 实体类型 ----------
    hass, mgr = _new_manager({"fan_entity": "fan.fan_pwm", "control_type": "fan"})
    hass.states.set("sensor.router_cpu_temp", FakeState("45.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect(
        "fan 实体 → set_percentage(52)",
        any(c[0] == "fan" and c[1] == "set_percentage" and c[2].get("percentage") == 52
            for c in hass.services.calls),
        str(hass.services.calls),
    )

    hass, mgr = _new_manager({"fan_entity": "switch.fan_relay", "control_type": "switch"})
    hass.states.set("sensor.router_cpu_temp", FakeState("30.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect(
        "switch 实体 → 只 turn_on，永不断电",
        hass.services.calls[-1][:2] == ("switch", "turn_on")
        and not any(c[1] == "turn_off" for c in hass.services.calls),
        str(hass.services.calls),
    )

    # ---------- E. 温度曲线 ----------
    hass, mgr = _new_manager({"strategy": "curve"})
    hass.states.set("sensor.router_cpu_temp", FakeState("43.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("曲线 43℃（38~48）→ 60%", mgr.current_duty == 60, f"duty={mgr.current_duty}")
    expect("状态含『曲线』", "曲线" in mgr.state_text, mgr.state_text)

    hass.states.set("sensor.router_cpu_temp", FakeState("30.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("曲线 30℃ → 保底 20%", mgr.current_duty == 20, f"duty={mgr.current_duty}")

    hass.states.set("sensor.router_cpu_temp", FakeState("60.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("曲线 60℃ → 满载 100%", mgr.current_duty == 100, f"duty={mgr.current_duty}")
    expect("状态含『曲线满载』", "曲线满载" in mgr.state_text, mgr.state_text)

    # ---------- F. 阶梯分段 ----------
    hass, mgr = _new_manager({"strategy": "step"})
    hass.states.set("sensor.router_cpu_temp", FakeState("43.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("阶梯 43℃ → 3/4 档 73%", (mgr.current_duty, "3/4" in mgr.state_text) == (73, True),
           f"duty={mgr.current_duty} {mgr.state_text}")

    hass.states.set("sensor.router_cpu_temp", FakeState("40.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("阶梯 40℃ → 1 档 20%", mgr.current_duty == 20, f"duty={mgr.current_duty}")

    hass.states.set("sensor.router_cpu_temp", FakeState("47.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("阶梯 47℃ → 4 档 100%", mgr.current_duty == 100, f"duty={mgr.current_duty}")

    # ---------- G. 切换控制方式并落盘 ----------
    hass, mgr = _new_manager()
    await mgr.async_set_strategy("curve")
    expect("切到曲线后 strategy=curve", mgr.strategy == "curve", mgr.strategy)
    expect(
        "控制方式写回 entry.options",
        hass.config_entries.updates and hass.config_entries.updates[-1].get("options", {}).get("strategy") == "curve",
        str(hass.config_entries.updates),
    )
    hass.states.set("sensor.router_cpu_temp", FakeState("43.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("切换后立刻按曲线算 → 60%", mgr.current_duty == 60, f"duty={mgr.current_duty}")

    await mgr.async_set_strategy("step")
    expect("再切阶梯 → strategy=step", mgr.strategy == "step", mgr.strategy)
    await mgr.async_set_strategy("thermostat")

    # ---------- H. 参数热更新 ----------
    hass, mgr = _new_manager()
    mgr.async_set_target_temp(45)
    hass.states.set("sensor.router_cpu_temp", FakeState("45.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("目标改 45℃ 后 45℃ → 保底 20%", mgr.current_duty == 20, f"duty={mgr.current_duty}")

    mgr.async_set_deadband(2)
    hass.states.set("sensor.router_cpu_temp", FakeState("43.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("容差 ±2℃ 时 43℃ → 保持 20%", mgr.current_duty == 20, f"duty={mgr.current_duty}")

    mgr.async_set_min_duty(25)
    hass.states.set("sensor.router_cpu_temp", FakeState("30.0"))
    mgr._read_temperature()
    await mgr._async_recalculate(force=True)
    expect("最低转速改 25% 后 30℃ → 25%", mgr.current_duty == 25, f"duty={mgr.current_duty}")

    mgr.async_set_min_duty(5)
    expect("最低转速设 5% 被硬抬到 20%", mgr.min_duty == 20, f"min_duty={mgr.min_duty}")

    # 曲线起止温度联动
    mgr.async_set_min_temp(50)
    expect("起点 ≥ 终点时自动拉开", mgr.max_temp > mgr.min_temp, f"{mgr.min_temp}/{mgr.max_temp}")

    # ---------- I. 任意温度/任意方式都不返回 0 ----------
    zero_hits = []
    for strat in ("thermostat", "curve", "step"):
        hass, mgr = _new_manager({"strategy": strat})
        for t in (-10, 0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 70, 90, 120):
            hass.states.set("sensor.router_cpu_temp", FakeState(str(t)))
            mgr._read_temperature()
            await mgr._async_recalculate(force=True)
            if mgr.current_duty < 20:
                zero_hits.append((strat, t, mgr.current_duty))
    expect("三种方式 × 全温度区间 → 从不低于 20%", not zero_hits, str(zero_hits))

    total = len(checks)
    failed = [c for c in checks if not c[1]]
    print(f"\n结果：{total - len(failed)}/{total} 通过")
    if failed:
        for name, _c, detail in failed:
            print(f"  失败：{name}  {detail}")
        return 1
    return 0


def main() -> int:
    _install_stubs()
    _make_package()
    return asyncio.run(scenario())


if __name__ == "__main__":
    raise SystemExit(main())