# 路由器散热底座风扇控制（router_fan_cooler v2.1.1）

米家蓝牙 Mesh PWM 灯控模块 → 风扇调速器 → 路由器散热底座，在 Home Assistant 里控制转速。
数据源是 `xmluyouqiwenkong` 集成提供的 CPU 温度实体，控制对象是灯控模块的亮度（= PWM 占空比）。

**三种控制方式，添加集成时选一种，之后随时可换：**

| 控制方式 | 思路 | 适合 |
|---|---|---|
| **恒温闭环**（默认，推荐） | 设目标温度 + 容差，按偏差增量加/减速，把 CPU 夹在目标 ±1℃ 内 | 想让温度稳住、又不想一直全速 |
| **温度曲线** | 两点线性：起点温度=最低转速，终点温度=最高转速，中间线性 | 房间温度稳定，想一次标定长期不动 |
| **阶梯分段** | 起点到终点分 4 档，每档固定占空比，风扇不做细碎变速 | 怕风扇频繁变速、听感要安静 |

**所有控制方式都不准停转**：占空比硬下限 20%（你的模块实测 20% 以下不转），
手动模式、失联兜底、曲线最低档、阶梯第 1 档，任何路径都不会下发 0% 或 1~19%。

---

## 1. 安装

1. 解压 `router_fan_cooler.zip`，把 `custom_components/router_fan_cooler` 整个目录放进 HA 的 `config/custom_components/`（目录名必须就是这个）。
2. 重启 Home Assistant。
3. 设置 → 设备与服务 → 添加集成 → 搜「路由器散热底座风扇控制」。
4. 向导第一项就是 **控制方式**：
   - 恒温闭环（推荐）
   - 温度曲线（两点线性）
   - 阶梯分段（4 档）
5. 选 CPU 温度实体（会自动预选 `xmluyouqiwenkong` 的 cpu 温度）与风扇实体（多为 `light.xxx`）。
6. 其余参数按下表填，保存即可。

老版本（v1/v2/v3）配置会自动迁移到 v4：保留原目标温度，控制方式默认「恒温闭环」，曲线温度补 38~48℃。

## 2. 配置项

| 参数 | 默认 | 范围 | 用在哪 |
|---|---|---|---|
| 控制方式 | 恒温闭环 | 三选一 | 决定下面哪组参数生效 |
| CPU 温度传感器 | — | sensor | 全部方式 |
| 风扇控制实体 | — | light/fan/switch | 全部方式 |
| 目标 CPU 温度 | 40 ℃ | 20–70 ℃ | 恒温闭环：要稳住的温度 |
| 恒温容差 | ±1.0 ℃ | ±0.5–3 ℃ | 恒温闭环：这个范围内不动转速 |
| 曲线起点温度 | 38 ℃ | 25–74.5 ℃ | 温度曲线 / 阶梯分段：到这个温度用最低转速 |
| 曲线终点温度 | 48 ℃ | 25.5–75 ℃ | 温度曲线 / 阶梯分段：到这个温度用最高转速 |
| 最低转速 | 20 % | 20–100 % | 全部方式：硬下限，填不低于 20 |
| 最高转速 | 100 % | 20–100 % | 全部方式 |
| 计算周期 | 10 秒 | 5–300 秒 | 全部方式；Mesh 设备建议 ≥10 秒 |
| 温度读不到时 | 全速散热 | 全速 / 保持 | 全部方式：绝不停转 |

改配置：设置 → 设备与服务 → 该集成 → 配置（选项），保存即生效，不用重启。

## 3. 三种算法

### 3.1 恒温闭环（thermostat）

```
error = 当前温度 - 目标温度
|error| ≤ 容差        → 保持当前占空比（且不低于最低转速）
error >  容差（过热）  → 占空比 += 8 × (error - 容差)，单次至少 +2
error < -容差（偏凉）  → 占空比 -= 8 × (|error| - 容差)，单次至少 -2
钳在 [最低转速, 最高转速]，单次最多变 15 个百分点
```

示例（40℃ ±1℃、最低 20%）：

| 温度 | 上一占空比 | 新占空比 |
|---|---|---|
| 45.0 ℃ | 20 % | 52 % |
| 41.2 ℃ | 52 % | 54 % |
| 40.6 ℃ | 54 % | 54 %（容差内保持） |
| 38.5 ℃ | 54 % | 50 % |
| 30.0 ℃ | 50 % | 35 % → 下一拍 20 % |

### 3.2 温度曲线（curve）

```
T ≤ 起点温度        → 最低转速（20%）
T ≥ 终点温度        → 最高转速（100%）
中间               → 最低 + (最高-最低) × (T-起点)/(终点-起点)
```

默认 38~48℃：38℃→20%、43℃→60%、48℃→100%。
想更凉就把区间整体下移（如 35~45℃），想安静就上移（如 42~52℃）。

### 3.3 阶梯分段（step）

起点到终点均分 4 档，每档固定占空比：

| 档位 | 温度区间（默认 38~48℃） | 占空比 |
|---|---|---|
| 1/4 | ≤ 40.5 ℃ | 20 % |
| 2/4 | 40.5–43 ℃ | 47 % |
| 3/4 | 43–45.5 ℃ | 73 % |
| 4/4 | ≥ 45.5 ℃ | 100 % |

风扇只在跨档时变一次，不会像曲线那样每 0.5℃ 就动。

## 4. 实体（共 12 个）

| 名称 | entity_id | 说明 |
|---|---|---|
| 风扇占空比 | `sensor.router_fan_cooler_duty` | 当前目标占空比 %（附控制方式、曲线温度等属性） |
| CPU 温度 | `sensor.router_fan_cooler_cpu_temp` | 读到的 CPU 温度 |
| 控制状态 | `sensor.router_fan_cooler_status` | 恒温保持 / 加速降温 / 曲线调速 / 阶梯 3/4 档 / 失联 |
| 风扇运转中 | `binary_sensor.router_fan_cooler_running` | 占空比 > 0（本集成恒为开） |
| 目标温度 | `number.router_fan_cooler_target_temp` | 恒温闭环用，20–70℃ |
| 恒温容差 | `number.router_fan_cooler_deadband` | 恒温闭环用，±0.5–3℃ |
| 曲线起点温度 | `number.router_fan_cooler_min_temp` | 曲线/阶梯用，默认 38℃ |
| 曲线终点温度 | `number.router_fan_cooler_max_temp` | 曲线/阶梯用，默认 48℃ |
| 最低转速 | `number.router_fan_cooler_min_duty` | 20–100%，不会低于 20 |
| 手动占空比 | `number.router_fan_cooler_manual_duty` | 20–100%，调它切手动模式 |
| 控制方式 | `select.router_fan_cooler_strategy` | 恒温闭环 / 温度曲线 / 阶梯分段（切换即写回配置） |
| 风扇模式 | `select.router_fan_cooler_mode` | 自动（按控制方式）/ 手动占空比 |

> 没有「关闭风扇」选项。这是故意的：任何模式都不停转。

## 5. 服务

```yaml
# 立即给固定占空比（切手动；<20 会被抬到 20）
service: router_fan_cooler.set_speed
data:
  percentage: 60

# 切模式：auto / manual
service: router_fan_cooler.set_mode
data:
  mode: auto

# 切控制方式：thermostat / curve / step
service: router_fan_cooler.set_strategy
data:
  strategy: curve

# 立刻重算（返回 strategy / temperature / duty / applied / state）
service: router_fan_cooler.recalculate
```

## 6. 自动化示例

`examples/automations_router_fan_cooler.yaml` 内含 6 条：夜间降噪（目标 40→45℃）、早上恢复、
过热兜底全速、回落恢复自动、下发失败提醒、每日小结。也可按控制方式切：

```yaml
- alias: 散热底座-夜间改曲线降噪
  trigger:
    - platform: time
      at: "23:00:00"
  action:
    - service: router_fan_cooler.set_strategy
      data: { strategy: curve }
```

## 7. 参数标定建议

1. **最低转速**：先手动从 20 → 25 → 30 试，找到能稳定起转且不咯噔的值，填进最低转速（不能低于 20）。
2. **恒温闭环**：目标设成你希望 CPU 长期待的温度；设太低（如 35℃）会长期满载，状态会显示「已满载」。
3. **容差**：默认 ±1℃；若占空比在 1℃ 边界反复微调，把容差调到 ±1.5~2℃。
4. **曲线/阶梯**：把起点设成「平时 CPU 温度附近」，终点设成「希望开始狂吹的温度」。
   曲线适合环境稳定；阶梯适合怕噪音波动。

## 8. 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| 加载报 `Invalid handler specified` | 目录层级不对 | 必须是 `config/custom_components/router_fan_cooler/manifest.json` |
| 一直 20% 不加速 | 温度低于目标/曲线起点 | 正常；想更凉就把目标/起点温度调低 |
| 状态「已满载」但温度不降 | 风扇压不住或风道没贴住 | 检查风扇、进风口，或把目标温度调高 |
| 风扇不转 | 占空比低于模块起转值 | 最低转速必须 ≥20（集成已强制） |
| 状态「温度失联·全速保护」 | 温度实体 5 分钟没更新 | 检查 `xmluyouqiwenkong` 与 Telnet 连接 |
| 改了控制方式没生效 | 只在「手动」模式 | 把 `select.router_fan_cooler_mode` 切回「自动」 |

## 9. 自测（不需要 HA、不需要路由器）

```powershell
cd D:\系统文件\桌面\XMFanCooler
python tools\selftest.py      # 三种算法 + 硬下限：36 项
python tools\mock_ha_test.py  # 假 HA 全链路：42 项
```

## 10. 文件清单

| 文件 | 说明 |
|---|---|
| `custom_components/router_fan_cooler/manifest.json` | 集成声明（version 2.1.1，依赖 xmluyouqiwenkong） |
| `const.py` | 常量：控制方式、硬下限 20%、默认值 |
| `control_math.py` | 三种算法纯函数（compute_duty / compute_curve / compute_step） |
| `manager.py` | 订阅温度、按控制方式计算、下发、失联兜底、参数热更新 |
| `config_flow.py` | 向导（先选控制方式）+ 老配置自动迁移 v4 |
| `sensor.py` / `binary_sensor.py` / `number.py` / `select.py` | 12 个实体 |
| `services.yaml` | 4 个服务 |
| `strings.json` + `translations/` | 中英文文案 |
| `diagnostics.py` | 诊断信息 |
| `examples/` | 自动化与卡片示例 |
| `tools/` | 两套自测脚本 |