"""三种控制方式的算法离线自测（不需要 Home Assistant，不需要路由器）。

用法：
    cd D:\\系统文件\\桌面\\XMFanCooler
    python tools\\selftest.py

覆盖：
    恒温闭环 compute_duty   —— 目标 40℃、容差 ±1℃、最低 20%
    温度曲线 compute_curve  —— 默认 38~48℃ 线性
    阶梯分段 compute_step   —— 默认 38~48℃ 分 4 档
    统一硬下限 clamp_duty   —— 任何结果都不低于 20%，绝不返回 0
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent / "custom_components" / "router_fan_cooler"
sys.path.insert(0, str(PKG))

from control_math import (  # noqa: E402
    clamp_duty,
    compute_curve,
    compute_duty,
    compute_step,
    limit_step,
)

CASES = []


def duty(t, prev, **kw):
    cfg = dict(target=40.0, deadband=1.0, min_duty=20, max_duty=100, gain=8.0, max_step=0)
    cfg.update(kw)
    return compute_duty(
        t, cfg["target"], cfg["deadband"], cfg["min_duty"], cfg["max_duty"],
        previous_duty=prev, gain=cfg["gain"], max_step=cfg["max_step"],
    )


def curve(t, lo=38.0, hi=48.0, min_duty=20, max_duty=100):
    return compute_curve(t, lo, hi, min_duty, max_duty)


def step(t, lo=38.0, hi=48.0, min_duty=20, max_duty=100):
    return compute_step(t, lo, hi, min_duty, max_duty)


def check(name, got, want):
    ok = got == want
    CASES.append((name, got, want, ok))
    print(("  [OK] " if ok else "  [FAIL] ") + f"{name}: got={got} want={want}")


def main() -> int:
    print("== 恒温闭环：过热按偏差加速（40℃、±1℃、8%/℃）==")
    check("45℃ 从 20% → 20+8*(5-1)=52", duty(45, 20), 52)
    check("41.2℃ 偏差 1.2 → +2 → 54", duty(41.2, 52), 54)
    check("80℃ 从 20% 一次加满 100", duty(80, 20), 100)
    check("41℃ 恰好边界 → 保持 54", duty(41.0, 54), 54)

    print("== 恒温闭环：偏凉减速，最低保底 20% ==")
    check("38.5℃ 偏差 -1.5 → -4 → 50", duty(38.5, 54), 50)
    check("30℃ 从 50% → 20（硬下限）", duty(30, 50), 20)
    check("39.0℃ 恰好边界 → 保持 54", duty(39.0, 54), 54)
    check("40.6℃ 容差内 → 保持 54", duty(40.6, 54), 54)

    print("== 恒温闭环：单步限幅与边界 ==")
    check("20% → 80℃ 限到 35", duty(80, 20, max_step=15), 35)
    check("35% → 80℃ 不限幅到 100", duty(80, 35), 100)
    check("max<min 自动纠正为 60", duty(45, 20, min_duty=60, max_duty=30), 60)
    check("容差 0：40.2℃ → 22", duty(40.2, 20, deadband=0.0), 22)
    check("容差 2：43℃ → 62", duty(43, 54, deadband=2.0), 62)
    check("min_duty=0 也被硬下限抬到 20", duty(30, 10, min_duty=0), 20)

    print("== 温度曲线：38℃→20%，48℃→100% ==")
    check("30℃ 低于起点 → 20", curve(30), 20)
    check("38℃ 起点 → 20", curve(38), 20)
    check("43℃ 中点 → 60", curve(43), 60)
    check("48℃ 终点 → 100", curve(48), 100)
    check("55℃ 高于终点 → 100", curve(55), 100)
    check("最低转速 30 时 43℃ → 65", curve(43, min_duty=30), 65)
    check("起止温度颠倒也能算（45℃ 保底 20）", curve(45, lo=50, hi=40), 20)

    print("== 阶梯分段：38~48℃ 分 4 档 ==")
    check("30℃ → 1 档 20%", step(30), (20, 1))
    check("40℃ → 1 档 20%", step(40), (20, 1))
    check("43℃ → 3 档 73%", step(43), (73, 3))
    check("45℃ → 3 档 73%", step(45), (73, 3))
    check("46.5℃ → 4 档 100%", step(46.5), (100, 4))
    check("55℃ → 4 档 100%", step(55), (100, 4))
    check("最低 30 最高 90 时 43℃ → 70", step(43, min_duty=30, max_duty=90), (70, 3))

    print("== 统一硬下限 clamp_duty ==")
    check("0 → 20", clamp_duty(0, 20, 100), 20)
    check("15 → 20", clamp_duty(15, 20, 100), 20)
    check("120 → 100", clamp_duty(120, 20, 100), 100)
    check("min_duty=0 也按 20 算", clamp_duty(5, 0, 100), 20)

    print("== limit_step 基础 ==")
    check("0 → 100 限到 15", limit_step(0, 100, 15), 15)
    check("100 → 0 限到 85", limit_step(100, 0, 15), 85)
    check("54 → 60 不触发", limit_step(54, 60, 15), 60)
    check("max_step=0 不限幅", limit_step(0, 100, 0), 100)

    total = len(CASES)
    failed = [c for c in CASES if not c[3]]
    print(f"\n结果：{total - len(failed)}/{total} 通过")
    if failed:
        for name, got, want, _ in failed:
            print(f"  失败：{name} got={got} want={want}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())