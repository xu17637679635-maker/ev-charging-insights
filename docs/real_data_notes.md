# UrbanEV 数据集笔记

记录接入这个数据集时踩到的坑。目的：下次（或别人）用同一份数据时不用重新摸索。

## 来源

- 仓库：<https://github.com/IntelligentSystemsLab/UrbanEV>
- 论文：*UrbanEV: An Open Benchmark Dataset for Urban Electric Vehicle Charging
  Demand Prediction*, Scientific Data (2025)
- 下载：`python scripts/fetch_data.py --dataset urbanev`
- 姊妹数据集：ST-EVCDP（18,061 桩 / 30 天 / 5 分钟粒度），同一实验室

## 坑 1（最重要）：文件是**宽表**，列名是交通小区，不是充电站

`volume.csv` / `occupancy.csv` / `duration.csv` 的结构是：

```
time,102,104,105,106,...

2022-09-01 00:00:00,56.29,11.08,60.37,...
```

只有 **275 列**，而 `inf.csv` 有 **1,362 个充电站**、`station_id` 是 4 位数
（1001~2681）。两者直接 join 会得到 56 个匹配 —— 明显不对。

**真相**：列名是 **TAZID**（交通小区），不是 `station_id`。验证方式：

```python
set(int(c) for c in volume.columns) == set(inf.TAZID)   # -> True，275 == 275
```

`station_id` 和 TAVID 是两套独立的 ID 空间。要按站分析，得先用
`inf.csv` 把站聚合到小区（本项目正是这么做的：`n_piles` 由
`groupby('TAZID')['charge_count'].sum()` 得到）。

## 坑 2：粒度是"小区 × 小时"，不是单次会话

数据里**没有**起止 SOC、没有功率曲线、没有插枪时间。只有：

| 文件 | 含义 |
|---|---|
| `volume.csv` | 该小区该小时充入的电量（kWh） |
| `occupancy.csv` | 该小时处于占用状态的桩数 |
| `duration.csv` | 平均充电时长（h） |

因此本项目里凡是与"单次充电"有关的分析（起止 SOC、功率曲线、家充条件）
只能用仿真数据做，真实数据做不了。这个边界在 README 里写明了。

## 坑 3：推导功率会被单行异常放大到荒谬的量级

自然想算 `mean_power_kw = volume / occupancy`。但这是**除以一个小整数**，
一次占用数采集故障就会炸出一个四位数：

| 时间 | 小区 | volume | occupancy | 推导功率 |
|---|---|---|---|---|
| 2022-11-30 00:00 | 775 | 5,580 kWh | **1** | **5,580 kW** |

同一小区相邻小时的占用数是 21、25、22，功率约 250 kW —— 这是个直流快充枢纽，
电量数据没问题，**坏的是那一小时的占用数**。

处理方式（`src/real_data.py`）：

- `MIN_OCCUPANCY_FOR_POWER = 3`：低于此不计算逐行功率（占 13.3% 行）
- `MAX_PLAUSIBLE_PILE_KW = 400`：超过此值的行打标记并在聚合中排除（占 0.04%）
- **聚合一律用 `sum(volume)/sum(occupancy)`**，绝不用逐行比值的均值

第三条是关键。逐行比值的均值会被上述异常主导；比值之和稳定且物理意义清楚
（"这段时间里，一个被占用的桩平均输出了多少功率"）。

## 坑 4：`curl` 能连但会断，需要重试

`raw.githubusercontent.com` 在本机间歇性 `RemoteDisconnected`，开发中大约每三次
请求失败一次。另外 `curl.exe` 在该沙箱里返回 `000`，但 Python 的 HTTP 栈可用
——所以下载逻辑必须写在 Python 里（见 `scripts/fetch_data.py`）。

## 数据概况（本项目实际使用范围）

| 项 | 值 |
|---|---|
| 时间 | 2022-09-01 00:00 ~ 2023-02-28 23:00（4,344 小时） |
| 小区 | 275 |
| 充电站 | 1,362 |
| 充电桩 | 17,532 |
| 总电量 | 311.7 GWh |
| 缺失值 | volume 0 / occupancy 0（完整） |

注：README 中 UrbanEV 论文称 24,798 桩，`inf.csv` 的 `charge_count` 求和为
17,532。差异可能来自数据版本（仓库里的是处理后版本）或论文统计口径不同。
本项目以**实际文件**为准，写 17,532。

## 快速自查

```bash
python -c "
import sys; sys.path.insert(0,'.')
from src import real_data as rd
df = rd.load_zone_hourly()
print(rd.summarise(df, rd.load_station_meta()))
print(rd.quality_report(df))
print('validate:', rd.validate(df) or 'OK')
print('power:', rd.aggregate_power_kw(df.volume_kwh, df.occupancy), 'kW/pile')
"
```

期望：`validate` 返回空、聚合功率落在 1–400 kW。
