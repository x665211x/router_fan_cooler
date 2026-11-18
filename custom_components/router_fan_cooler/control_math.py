"""三种控制方式的纯数学部分：不依赖 Home Assistant，便于离线自测。

统一约束（硬件决定）：
    所有函数返回值都落在 [max(20, min_duty), max_duty]，绝不返回 0，
    也不返回 1~19 这种"灯亮着但风扇不转"的无效占空比。
"""

from __future__ import annotations

MIN_DELTA = 2  # 恒温模式单次调节的最小步进，避免 ±1℃ 附近只动 1% 响应太肉

HARD_FLOOR = 20  # 灯控模块实测 20% 以下不转


def clamp_duty(duty: float, min_duty: int, max_duty: int) -> int:
    """把占空比钳进 [max(20, min_duty), max_duty]，保证永不停止、永不落进 20% 以下的停转区。"""
    floor = max(HARD_FLOOR, int(min_duty))
    ceiling = max(floor, int(max_duty))
    value = int(round(duty))
    return max(floor, min(ceiling, value))


def _safe_range(min_temp: float, max_temp: float) -> tuple[float, float]:
    """起点终点相同或颠倒时的兜底，避免除零。"""
    if max_temp <= min_temp:
        max_temp = min_temp + 1.0
    return float(min_temp), float(max_temp)


def compute_duty(
    temperature: float,
    target: float,
    deadband: float,
    min_duty: int,
    max_duty: int,
    previous_duty: int | None = None,
    gain: float = 8.0,
    max_step: int = 0,
) -> int:
    """恒温闭环：按「实际温度 vs 目标」的偏差增量调节。

    |error| <= 容差  → 保持上次占空比（不低于最低转速）
    过热             → 占空比 += gain * (error - 容差)，至少 +2
    偏凉             → 占空比 -= gain * (|error| - 容差)，至少 -2
    最后钳进 [最低转速, 最高转速]，并按 max_step 单步限幅。
    永远不停转：即使算出来很低，也保底 max(20, min_duty)。
    """
    if max_duty < min_duty:
        max_duty = min_duty
    if deadband < 0:
        deadband = 0.0

    base = max(HARD_FLOOR, min_duty) if previous_duty is None else int(previous_duty)
    base = max(0, min(100, base))

    error = temperature - target
    if abs(error) <= deadband:
        out = max(base, min_duty)
    else:
        delta = int(round(gain * (abs(error) - deadband)))
        delta = max(MIN_DELTA, delta)
        out = base + delta if error > 0 else base - delta

    out = max(0, min(out, int(max_duty)))
    if out < min_duty:
        out = int(min_duty)
    if max_step:
        out = limit_step(base, out, max_step)
    return clamp_duty(out, min_duty, max_duty)


def compute_curve(
    temperature: float,
    min_temp: float,
    max_temp: float,
    min_duty: int,
    max_duty: int,
) -> int:
    """温度曲线：min_temp→最低转速，max_temp→最高转速，中间线性插值。

    T <= min_temp      → 最低转速
    min_temp < T < max → 按比例插值
    T >= max_temp      → 最高转速
    """
    lo, hi = _safe_range(min_temp, max_temp)
    floor = max(HARD_FLOOR, int(min_duty))
    ceiling = max(floor, int(max_duty))
    if temperature <= lo:
        return floor
    if temperature >= hi:
        return ceiling
    ratio = (temperature - lo) / (hi - lo)
    return clamp_duty(floor + (ceiling - floor) * ratio, min_duty, max_duty)


def compute_step(
    temperature: float,
    min_temp: float,
    max_temp: float,
    min_duty: int,
    max_duty: int,
    gears: int = 4,
) -> tuple[int, int]:
    """阶梯分段：把 min_temp~max_temp 均分成 gears 档，返回 (占空比, 档位序号)。

    档位 1..gears：温度落在第 k 段就用第 k 档；低于起点恒为 1 档，高于终点恒为最后一档。
    每档占空比 = 最低转速 + (最高-最低) * (k-1)/(gears-1)，所以第 1 档=最低、最后一档=最高。
    """
    lo, hi = _safe_range(min_temp, max_temp)
    gears = max(2, int(gears))
    floor = max(HARD_FLOOR, int(min_duty))
    ceiling = max(floor, int(max_duty))

    if temperature <= lo:
        k = 1
    elif temperature >= hi:
        k = gears
    else:
        span = (hi - lo) / gears
        k = int((temperature - lo) // span) + 1
        k = max(1, min(gears, k))

    duty = floor + (ceiling - floor) * (k - 1) / (gears - 1)
    return clamp_duty(duty, min_duty, max_duty), k


def limit_step(previous: int, target: int, max_step: int) -> int:
    """单步限幅：一次最多升降 max_step 个百分点，防止转速突变啸叫。"""
    if max_step <= 0:
        return target
    if target > previous + max_step:
        return previous + max_step
    if target < previous - max_step:
        return previous - max_step
    return target