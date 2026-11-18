"""路由器散热底座风扇控制 —— 常量定义。

三种控制方式（添加集成时选择，之后可在「选项」或 select 实体里切换）：
    thermostat 恒温闭环（默认）
        设定目标 CPU 温度 + 容差。温度进 目标±容差 就保持转速；
        超出容差按偏差增量加/减速，直到温度回到区间内。
    curve      温度曲线
        两点线性映射：min_temp → 最低转速，max_temp → 最高转速，
        中间按温度线性插值。标定一次就不会变，适合环境稳定的房间。
    step       阶梯分段
        min_temp~max_temp 之间分 4 档，每档固定一个占空比（不连续变化，
        耳朵听不出细碎波动），适合怕风扇频繁变速的场景。

硬件约束（米家 BLE Mesh PWM 灯控模块改装）：
    灯控模块亮度 = PWM 占空比；实测低于 20% 风扇根本不转。
    因此本集成任何控制方式都不允许停转，占空比硬下限 = 20%，
    也绝不下发 1~19% 这种"转不起来"的值。
"""

from __future__ import annotations

DOMAIN = "router_fan_cooler"
NAME = "路由器散热底座风扇控制"
VERSION = "2.1.1"

# ---------------- 配置键 ----------------
CONF_TEMP_SENSOR = "temp_sensor"          # CPU 温度传感器实体（xmluyouqiwenkong 提供）
CONF_FAN_ENTITY = "fan_entity"            # 灯控模块对应的实体（light / fan / switch）
CONF_CONTROL_TYPE = "control_type"        # 如何下发：light 亮度 / fan 百分比 / switch 开关
CONF_STRATEGY = "strategy"                # 控制方式：thermostat / curve / step
CONF_TARGET_TEMP = "target_temp"          # 恒温目标 ℃（仅 thermostat 使用，默认 40℃）
CONF_DEADBAND = "deadband"                # 恒温容差 ±℃（仅 thermostat 使用，默认 1.0）
CONF_MIN_TEMP = "min_temp"                # 曲线/阶梯起点温度（默认 38℃）
CONF_MAX_TEMP = "max_temp"                # 曲线/阶梯终点温度（默认 48℃）
CONF_MIN_DUTY = "min_duty"                # 最低转速 %（硬下限 20，模块 20% 以下不转）
CONF_MAX_DUTY = "max_duty"                # 最高转速 %
CONF_MIN_CHANGE = "min_change"            # 占空比变化小于该值时不重复下发（默认 0=每次都下发）
CONF_MAX_STEP = "max_step"                # 单次调节最大步进 %（防止转速突变啸叫，默认 15）
CONF_UPDATE_INTERVAL = "update_interval"  # 计算/下发周期（秒）
CONF_FAILSAFE = "failsafe"                # 温度失联：max=全速保护 / hold=保持上次转速

# 老版本留下的键，仅用于自动迁移
CONF_STOP_TEMP = "stop_temp"
CONF_BAND = "band"
CONF_ALLOW_STOP = "allow_stop"

# ---------------- 控制方式 ----------------
STRATEGY_THERMOSTAT = "thermostat"   # 恒温闭环
STRATEGY_CURVE = "curve"             # 温度曲线
STRATEGY_STEP = "step"               # 阶梯分段
STRATEGIES = [STRATEGY_THERMOSTAT, STRATEGY_CURVE, STRATEGY_STEP]
STRATEGY_LABELS = {
    STRATEGY_THERMOSTAT: "恒温闭环（推荐）",
    STRATEGY_CURVE: "温度曲线（两点线性）",
    STRATEGY_STEP: "阶梯分段（4 档）",
}
LABEL_TO_STRATEGY = {v: k for k, v in STRATEGY_LABELS.items()}

# ---------------- 下发方式 ----------------
CONTROL_LIGHT = "light"     # light.turn_on(brightness_pct=…)
CONTROL_FAN = "fan"         # fan.set_percentage(percentage=…)
CONTROL_SWITCH = "switch"   # switch.turn_on（只有开/关，无 PWM，等效全速）
CONTROL_TYPES = [CONTROL_LIGHT, CONTROL_FAN, CONTROL_SWITCH]

# ---------------- 运行模式 ----------------
MODE_AUTO = "auto"          # 按所选控制方式自动调节
MODE_MANUAL = "manual"      # 手动固定占空比
MODES = [MODE_AUTO, MODE_MANUAL]

# ---------------- 失联保护 ----------------
FAILSAFE_MAX = "max"
FAILSAFE_HOLD = "hold"
FAILSAFE_OPTIONS = [FAILSAFE_MAX, FAILSAFE_HOLD]

# ---------------- 默认值 ----------------
DEFAULT_STRATEGY = STRATEGY_THERMOSTAT
DEFAULT_TARGET_TEMP = 40.0   # 恒温目标（用户示例：40±1℃）
DEFAULT_DEADBAND = 1.0       # ±1℃ 容差：进这个范围就保持转速
DEFAULT_MIN_TEMP = 38.0      # 曲线/阶梯起点：38℃
DEFAULT_MAX_TEMP = 48.0      # 曲线/阶梯终点：48℃
DUTY_GAIN_PER_C = 8.0        # 每超出容差 1℃，单次多调 8 个百分点
DEFAULT_MIN_DUTY = 20        # 灯控模块实测 20% 以下风扇不转
HARD_MIN_DUTY = 20           # 硬下限：任何控制方式都不低于 20%（不允许停转）
DEFAULT_MAX_DUTY = 100
DEFAULT_MIN_CHANGE = 0
DEFAULT_MAX_STEP = 15
DEFAULT_UPDATE_INTERVAL = 10
DEFAULT_FAILSAFE = FAILSAFE_MAX
DEFAULT_STALE_SECONDS = 300  # 温度实体超过该秒数没更新即视为失联

STEP_GEARS = 4               # 阶梯分段档位：0 / 1/3 / 2/3 / 1 的占空比

# ---------------- 服务 ----------------
SERVICE_SET_SPEED = "set_speed"
SERVICE_SET_MODE = "set_mode"
SERVICE_SET_STRATEGY = "set_strategy"
SERVICE_RECALCULATE = "recalculate"

# ---------------- 内部键 ----------------
DATA_MANAGER = "manager"
PLATFORMS = ["sensor", "binary_sensor", "number", "select"]

UNIQUE_PREFIX = "router_fan_cooler"


def signal_update(entry_id: str) -> str:
    """每个条目一个 dispatcher 信号，管理器算完就广播一次。"""
    return f"{DOMAIN}_update_{entry_id}"