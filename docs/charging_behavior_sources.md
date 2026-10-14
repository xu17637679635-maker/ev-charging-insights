# 电动汽车充电行为参数 —— 可引用来源汇编

> 用途：为 `charging-insights`（电动汽车充电行为数据分析）项目的**合成数据生成器**提供真实参数依据。
> 本文档严格区分「有出处的真实数据」与「推断/插值」。凡未能找到可靠来源的条目，一律列入第 6 节，不得当作真实值写入生成器。

## 0. 方法论、可信度分级与本次检索的限制

### 0.1 可信度分级（confidence）

| 等级 | 含义 | 可否直接写进生成器默认参数 |
| --- | --- | --- |
| `standard` | 标准/规范/官方定义值（DOE、IEA、GB/T、SAE 等），工程上无争议 | ✅ 是 |
| `typical` | 权威报告或同行评议文献中的实测统计量（均值/中位数/分布），有明确样本与来源 | ✅ 是，但需注明样本范围 |
| `demo-only` | 单一来源、单台车实测、或由其它数据推算出的派生量，仅可用于「形状合理」的占位 | ⚠️ 可用于演示，须在代码与文档中标注 |
| `推断` | 本报告作者的插值/推测，**无独立出处** | ❌ 否，只能作为启发式 |

### 0.2 本次检索的技术限制（影响覆盖度）

- 环境内 `web_fetch` **无法读取 PDF**（返回 `unsupported content type`）。以下高价值来源只拿到搜索摘要、未拿到全文，相关数字若引用需自行核对：
  - NREL/INL *EV Project* 系列（`energy.gov/sites/prod/files/2014/04/f15/2013_vsst_report.pdf`）——含「全国 Level 2 充电桩 15 分钟粒度使用率分布」图（Figure IV-22）、平均连接时长表。
  - ElaadNL 荷兰充电报告、Nationaal Laadonderzoek 2024/2025（均为 PDF，荷兰语）。
  - IEA *Global EV Outlook 2025* 正文（页面为 JS 渲染，抓取只得到导航骨架）。
  - AAA *Electric Vehicle Range Testing* 原始报告 PDF（但其**结论数字已由 AAA 新闻稿 HTML 页面完整给出**，见 §1.5）。
- ACN-Data 的 REST API 需注册 token（实测返回 `401 Please provide proper credentials`），**本次未能拉取真实会话记录**。其字段/站点元数据是公开页面，已记录于 §1.6；会话级统计量未取到，列入缺口。
- 环境内 `curl` / `Invoke-WebRequest` 出网受限（schannel 凭证错误），只能依赖 `web_fetch`。

---

## 1. 主表：参数 · 典型值 · 单位 · 来源 · 可信度

### 1.1 充电桩功率等级

| 参数 | 典型值/范围 | 单位 | 来源 URL | 可信度 |
| --- | --- | --- | --- | --- |
| AC Level 1（美国家用 120 V）典型功率 | 1.9 | kW | <https://afdc.energy.gov/fuels/electricity-stations> | standard |
| AC Level 1 补能速率 | ≈5（约 8 小时补 40 英里） | mile/h | <https://afdc.energy.gov/fuels/electricity-stations> | standard |
| AC Level 2 功率范围（设备能力） | 2.9 – 19.2 | kW | <https://afdc.energy.gov/fuels/electricity-stations> | standard |
| 美国家用 Level 2 最常见功率（≤30 A / 240 V） | 7.2 | kW | <https://afdc.energy.gov/fuels/electricity-stations> | standard |
| Level 2 公共桩常见电流档 | 40 – 80 | A | <https://afdc.energy.gov/fuels/electricity-stations> | standard |
| Level 2 补能速率（均值） | ≈25（实际范围从数 mi/h 到 ≈40 mi/h） | mile/h | <https://www.fueleconomy.gov/feg/charging.shtml> | standard |
| DC 快充单桩功率上限（现行设备） | ≤500 | kW | <https://afdc.energy.gov/fuels/electricity-stations> | standard |
| DC 快充补能速率 | 100 – 200+ / 30 min | mile/30min | <https://www.fueleconomy.gov/feg/charging.shtml> | standard |
| 美国公共桩中 Level 1 占比（2023） | <1 | % | <https://afdc.energy.gov/fuels/electricity-stations> | standard |
| 美国公共桩中 Level 2 占比（2023） | ≈80 | % | <https://afdc.energy.gov/fuels/electricity-stations> | standard |
| 美国公共桩中 DC 快充占比（2023） | >20 | % | <https://afdc.energy.gov/fuels/electricity-stations> | standard |
| 加拿大魁北克公共 L2 桩功率为 7.2 kW 的比例 | >95 | % | <https://ar5iv.labs.arxiv.org/html/2504.17722> | typical |
| 魁北克公共 L3（直流）桩功率区间 | 24 – 100（同一站点混布不同功率） | kW | <https://ar5iv.labs.arxiv.org/html/2504.17722> | typical |
| 中国公共桩：交流 / 直流 保有量（2024-12） | 交流 193.6 万台 / 直流 164.3 万台（合计 357.9 万台） | 万台 | <https://www.china5e.com/news/news-1184387-1.html> | standard |
| 中国公共桩交流:直流 比例（2024-12） | 54.1 : 45.9 | % | 由上行数据计算 | typical |
| 中国公共桩：交流 / 直流 保有量（2025-03） | 交流 211.4 万台 / 直流 178.5 万台（公共合计 390.0 万台） | 万台 | <https://www.mei.net.cn/qcgy/202504/564469544559613845.html> | standard |
| 中国公共桩交流:直流 比例（2025-03） | 54.2 : 45.8 | % | 由上行数据计算 | typical |
| 中国公共桩额定总功率（2025） | 2.20 亿（220 GW） | kW | <http://www.evobserver.com/archives/23773> | typical |
| 中国公共桩**单枪平均**额定功率（2025） | 46.53（同比 +33%）⚠️ 这是「总功率÷枪数」的统计均值，**不是**单桩典型功率 | kW | <http://www.evobserver.com/archives/23773> | typical |
| 中国 >250 kW 大功率充电枪保有量（2025，TOP5 厂商） | 202,752 | 个 | <http://www.evobserver.com/archives/23773> | typical |
| 中国 >240 kW 大功率直流桩保有量（2024） | 11.9 万台，占直流桩 7.79% | 万台 / % | <http://www.evobserver.com/archives/23773> | typical |
| 中国 2026 年公共桩增量预测：直流 / 交流 | 新增直流 75.9 万个 / 新增交流 68.9 万个 | 万个 | <http://www.evobserver.com/archives/23773> | typical |
| 中国公共桩功率份额（2025 Q1）：低功率 / 中低功率 / 高功率 | 58.9 / 34.5 / 6.7 | % | <https://www.mei.net.cn/qcgy/202504/564469544559613845.html> | typical |
| 中国 2025 预计新增公共直流桩 / 交流桩（EVCIPA 原预测） | 52.2 / 51.6 | 万台 | <https://www.mei.net.cn/qcgy/202501/550550317926750101.html> | typical |
| 中国充电基础设施总量（2025 Q1）：公共 / 私人 | 公共 390 万台 / 私人 984.9 万台 | 万台 | <https://www.mei.net.cn/qcgy/202504/564469544559613845.html> | standard |

> ⚠️ **中国交流桩单桩功率无直接出处。** 上述 46.53 kW 是全网平均值，被直流桩显著拉高，**不可**当作交流桩典型功率使用。国标交流桩的功率档位与占比本次未检索到可引用的官方统计，见 §6.1 第 8 条。

### 1.2 充电曲线（最关键项）

| 参数 | 典型值/范围 | 单位 | 来源 URL | 可信度 |
| --- | --- | --- | --- | --- |
| DC 快充曲线形状：先恒流/恒功率爬升 → 平顶 → 高 SOC 段 taper（降流） | 定性规律 | — | <https://evkx.net/technology/battery/charging/> | standard |
| taper 的物理原因 | 最高单体电压触及上限，BMS 必须降流以避免过压（由 CC 转入 CV 段） | — | <https://evkx.net/technology/battery/charging/> | standard |
| 「最后几个百分点耗时不成比例地长」 | 定性规律（原文：final percentages often take disproportionately long） | — | <https://evkx.net/technology/battery/charging/> | standard |
| 电池低温影响 | 内阻升高、允许充电电流下降；过冷有析锂风险；过热亦需降功率 | — | <https://evkx.net/technology/battery/charging/> | standard |
| DC 快充到 80% 的典型耗时（DOE 官方表述） | ≈30 | min | <https://www.fueleconomy.gov/feg/evtech.shtml> | standard |
| 从空到满的典型充电时长（AC） | 3 – 12 | h | <https://www.fueleconomy.gov/feg/evtech.shtml> | standard |
| 同一充电桩上不同车型曲线差异巨大（决定因素是车载 BMS 策略而非桩） | 定性规律 | — | <https://evkx.net/technology/battery/charging/> | standard |
| 实际充电功率 = min(建筑供电, 相数, 桩额定, 线缆额定, 车载充电机额定, 电池接受能力, 用户设置) | 定性规律 | — | <https://evkx.net/technology/battery/charging/> | standard |

#### 具体车型充电曲线数据点（逐 1% SOC）

均来自 EVKX 充电曲线库（HTML 页面 + 同源 GitHub 原始 Markdown，可直接复核）。条件注明为「optimum conditions」，即电池处于最佳温度、且从低 SOC 开始充电。

**Tesla Model 3 Performance（≈75 kWh，350 V 级，峰值 250 kW）**

| SOC | 功率 | 占峰值 | 特性 |
| --- | --- | --- | --- |
| 0% | 100 kW | 40% | 起步受限于低包压/电流上限 |
| 10–15% | 250 kW | 100% | **满功率平台** |
| 20% | 232 kW | 93% | taper 开始 |
| 30% | 164 kW | 66% | — |
| 41% | 132 kW | 53% | Optimum charging area 上界 |
| 50% | **96 kW** | **38%** | 「~50% 后显著掉档」经验法则成立 |
| 60% | 78 kW | 31% | — |
| 70% | 63 kW | 25% | — |
| **80%** | **46 kW** | **18%** | 降至峰值约 1/5 |
| 90% | 33 kW | 13% | — |
| 100% | 13 kW | 5% | 收尾 |

- 0→100%：1 h 8 min 51 s，充入 75.0 kWh；10→100%：1 h 5 min 29 s；**10→80%：33 min 2 s**
- 平均功率：0–100% = 65.4 kW；10–80% = 95.3 kW；Optimum charging area = 5%–43%
- Max C-rate 3.20，平均 C-rate 1.48
- 来源：<https://raw.githubusercontent.com/evkx/evkx.github.io/main/content/models/tesla/model_3/Model_3_Performance/chargingcurve.md> ｜页面：<https://evkx.net/models/tesla/model_3/model_3_performance/chargingcurve/> ｜可信度 `typical`

**Hyundai Ioniq 5 Long Range AWD（≈84 kWh，697 V，峰值 260 kW）—— 阶梯式 taper**

| SOC | 功率 | 占峰值 | 特性 |
| --- | --- | --- | --- |
| 1–25% | 205 → 225 kW | 79→87% | 爬升段 |
| 26–50% | 246 → 257 kW | 95→99% | 满功率平台 |
| **51%** | **204 kW** | **78%** | 第一次掉档（-21%） |
| 58–68% | 195 → 162 kW | 75→62% | 第二段台阶 |
| **69%** | **118 kW** | **45%** | 第二次掉档（-54%） |
| 76–80% | 100 → 67 kW | 38→26% | 第三次掉档 |
| 90% | 32 kW | 12% | — |
| 100% | 9 kW | 3% | 收尾 |

- 0→100%：1 h 15 min 38 s，充入 80.0 kWh；10→100%：1 h 13 min 5 s；**10→80%：19 min 33 s**（另一数据版本 19 min 8 s）
- 平均功率：0–100% = 63.5 kW；10–80% = 171.8 kW；Optimum charging area = 1%–63%
- 来源：<https://raw.githubusercontent.com/evkx/evkx.github.io/main/content/models/hyundai/ioniq_5/Ioniq_5_Long_Range_AWD/chargingcurve.md> ｜可信度 `typical`

**Volkswagen ID.4 Pro（≈77 kWh，峰值 ≈141 kW）—— 低平台、缓 taper**

| SOC | 功率 | 占峰值 | 特性 |
| --- | --- | --- | --- |
| 1–39% | 131 → 139 kW | 93→99% | 近似平顶 |
| 40–62% | 130 → 125 kW | 92→89% | 缓降 |
| 70% | 110 kW | 78% | — |
| **80%** | **73 kW** | **52%** | — |
| 90% | 31 kW | 22% | — |
| 100% | 20 kW | 14% | 收尾 |

- 0→100%：1 h 3 min 51 s，充入 77.0 kWh；**10→80%：28 min 13 s**
- 平均功率：0–100% = 72.3 kW；10–80% = 114.6 kW；Max C-rate 仅 1.72
- 来源：<https://raw.githubusercontent.com/evkx/evkx.github.io/main/content/models/volkswagen/id.4/ID.4_Pro/chargingcurve.md> ｜可信度 `typical`

**跨车型规律汇总（可直接用于生成器设计，均 `typical`）**

| 规律 | 数值 |
| --- | --- |
| 满功率平台覆盖范围 | 从 ~1–10% 起，分别到 ~15%（Tesla）/ ~50%（Ioniq 5）/ ~62%（ID.4） |
| 「~50% SOC 开始明显 taper」 | 对 250 kW 级 400 V 车型成立（Model 3 P 在 50% 已降至峰值 38%） |
| 「~80% SOC 功率为峰值的 1/5 ~ 1/2」 | Tesla 18% / Ioniq 5 26% / ID.4 52% |
| 10→80% 耗时 | 19 min（Ioniq 5）/ 28 min（ID.4）/ 33 min（Model 3 P） |
| 0→100% 平均功率 vs 峰值 | 仅为峰值的 1/4 ~ 1/3（65.4/250、63.5/260、72.3/141） |
| **80%→100% 能量占比 vs 时间占比严重不对称** | Model 3 P 曲线：80→100% 充入 15.0 kWh（占 0→100% 能量的 20%），耗时 32 min 27 s（占 0→100% 时间的 47%） |

**独立佐证（实测录像，非建模）**：CleanTechnica 记录 Model 3 LR 在 V3 超充上「2% 插枪，5% 即达 250 kW，满功率维持到约 22%」，11 min 29 s 补充 50% 电量——与上表 15–20% 开始 taper 高度一致。
来源：<https://cleantechnica.com/2019/06/24/tesla-model-3-on-supercharger-v3-adds-50-range-in-under-12-minutes-charts/> ｜可信度 `typical`（单台车单次实测）

### 1.3 单次充电行为

| 参数 | 典型值/范围 | 单位 | 来源 URL | 可信度 |
| --- | --- | --- | --- | --- |
| 每日充电次数（多研究汇总） | 0.71（中位，Visaria 2022）/ 0.75（均值，同上）/ 1.17（均值，Tal 2018）/ 1.1（均值，Tal 2020）/ 1.47（均值，Axsen 2015） | 次/日 | <https://ar5iv.labs.arxiv.org/html/2504.17722> | typical |
| 有充电行为的当日的充电次数上限（Tal 2020，全车型） | <1.66 | 次/日 | <https://ar5iv.labs.arxiv.org/html/2504.17722> | typical |
| 人均年充电次数（van den Hoed 2013，荷兰） | 18 – 20 | 次/年 | <https://ar5iv.labs.arxiv.org/html/2504.17722> | typical |
| 单次充电时长分布 | **未取到** —— 见 §6.1 第 1 条 | — | — | — |
| 单次充电电量分布 | **未取到** —— 见 §6.1 第 2 条 | — | — | — |
| 起始 / 结束 SOC 分布 | **未取到** —— 见 §6.1 第 3 条 | — | — | — |
| 以 100% SOC 结束的会话占比 | **未取到** —— 见 §6.1 第 4 条 | — | — | — |
| 充电地点结构（荷兰公共桩会话） | 过夜公共 50 / 非工作时段日间 35 / 工作场所 14 | % | <https://ar5iv.labs.arxiv.org/html/2504.17722> | typical |
| 家用充电时段 | 绝大多数车主在**夜间**在家充电 | — | <https://afdc.energy.gov/fuels/electricity-charging-home> | standard |
| 工作场所充电「典型到达时段」 | 多数车辆在工作日 **8 a.m. 之后**开始充电 | — | 搜索摘要，源自 Sabancı 大学学位论文 PDF（未读全文） | demo-only |
| 一天中各时段分布（量化） | **未取到** —— 见 §6.1 第 5 条 | — | — | — |

### 1.4 车主行为

| 参数 | 典型值/范围 | 单位 | 来源 URL | 可信度 |
| --- | --- | --- | --- | --- |
| 主要在家充电的车主占比 | 82 – 93 | % | <https://ar5iv.labs.arxiv.org/html/2504.17722> | typical |
| 经常使用工作场所充电的用户占比 | 19 – 40（地区差异大） | % | <https://ar5iv.labs.arxiv.org/html/2504.17722> | typical |
| 美国「多数车主大部分时间在家充电」 | 定性 | — | <https://afdc.energy.gov/fuels/electricity-charging-home> | standard |
| 工作日 / 周末差异（量化） | **未取到** —— 见 §6.1 第 6 条 | — | — | — |
| 公共充电站排队 / 等待时长 | **未取到实测数据** —— 见 §6.1 第 7 条 | — | — | — |
| 排队建模研究（存在性，无实测值） | MAP/PH/c 排队模型；「可接受等待时间」建模 | — | <https://www.tandfonline.com/doi/pdf/10.1080/21680566.2026.2642261> ；<https://www.mdpi.com/2071-1050/16/6/2536>（403 被拦） | demo-only |
| 用户画像阈值：共享 / 商用账户判据 | 单日充电 ≥4 次；或月充电 >500 kWh、年 >6,000 kWh | 次/日、kWh | <https://ar5iv.labs.arxiv.org/html/2504.17722> | typical |
| 私人 EV 年行驶里程（多研究） | 15,563（非 Tesla，挪威）/ 20,150（Axsen 2015）/ 23,367（Tesla，挪威） | km/年 | <https://ar5iv.labs.arxiv.org/html/2504.17722> | typical |

### 1.5 能耗参考

| 参数 | 典型值/范围 | 单位 | 来源 URL | 可信度 |
| --- | --- | --- | --- | --- |
| 紧凑型轿车（Tesla Model 3 LR，MY2026）WLTP 能耗 | 11.45 – 12.42 | kWh/100km | <https://evkx.net/models/tesla/model_3/model_3_long_range/rangeandconsumption/> | typical |
| 同上，EPA 能耗 | 14.7 – 14.9 | kWh/100km | <https://evkx.net/models/tesla/model_3/model_3_long_range/rangeandconsumption/> | typical |
| 同上，真实工况 90 km/h | 12.8（+2 kW 暖风 15.0） | kWh/100km | <https://evkx.net/models/tesla/model_3/model_3_long_range/rangeandconsumption/> | typical |
| 同上，真实工况 112 km/h（70 mph） | 16.1（+2 kW 暖风 17.9） | kWh/100km | <https://evkx.net/models/tesla/model_3/model_3_long_range/rangeandconsumption/> | typical |
| 中型 SUV（Ioniq 5 LR AWD）WLTP 能耗 | 14.65 – 16.16 | kWh/100km | <https://raw.githubusercontent.com/evkx/evkx.github.io/main/content/models/hyundai/ioniq_5/Ioniq_5_Long_Range_AWD/rangeandconsumption.md> | typical |
| 同上，真实工况 90 km/h | 17.0（+2 kW 暖风 19.2） | kWh/100km | 同上 | typical |
| 同上，真实工况 112 km/h | 22.0（+2 kW 暖风 23.8） | kWh/100km | 同上 | typical |
| 同上，真实工况 120 km/h | 24.0（+2 kW 暖风 25.7） | kWh/100km | 同上 | typical |
| **车型级别差异**（紧凑轿车 vs 中型 SUV AWD，WLTP） | 11.45 vs 14.65，即 **约 +28%** | % | 由上述两组数据算得 | typical |
| 2 kW 暖风导致能耗上升 | +17%（90 km/h）/ +11%（112 km/h）/ +7%（120 km/h） | % | 由上述数据算得 | typical |
| **冬季 20°F（-7°C）开暖风：EV 能耗变化** | **+≈39** | % | <https://www.fueleconomy.gov/feg/coldweather.shtml> | standard |
| **冬季 20°F 开暖风：续航变化** | **-≈41** | % | <https://www.fueleconomy.gov/feg/coldweather.shtml> ＋ <https://newsroom.aaa.com/2019/02/cold-weather-reduces-electric-vehicle-range/> | standard |
| 冬季开暖风时额外能耗用于座舱加热的比例 | ≈2/3 | — | <https://www.fueleconomy.gov/feg/coldweather.shtml> | standard |
| 冬季**不开**暖风：能耗 / 续航变化 | -8 / -12 | % | <https://www.fueleconomy.gov/feg/coldweather.shtml> | standard |
| 夏季 95°F（35°C）开空调：续航变化 | -17 | % | <https://newsroom.aaa.com/2019/02/cold-weather-reduces-electric-vehicle-range/> | standard |
| 冬季开暖风的额外用车成本 | ≈$25 额外 / 1,000 英里（对比 75°F） | USD | <https://newsroom.aaa.com/2019/02/cold-weather-reduces-electric-vehicle-range/> | typical |
| 电池充电效率 | 84 – 93 | % | <https://www.fueleconomy.gov/feg/evtech.shtml> | standard |
| 电网到车轮总效率 | >77 | % | <https://www.fueleconomy.gov/feg/evtech.shtml> | standard |

> AAA 测试方法：5 台车（每家厂商 1 台，EPA 续航 ≥100 英里），在南加州汽车俱乐部气候控制舱 + 底盘测功机上，对比 20°F / 95°F 与 75°F 基准。属受控实验而非问卷，可作为 `standard` 引用。

### 1.6 公开真实数据集

| 数据集 | 规模 | 粒度 / 字段 | 可获取性 | 可信度 |
| --- | --- | --- | --- | --- |
| **ACN-Data**（Caltech / PowerFlex） | Caltech 54 个 EVSE；JPL 50 个 EVSE；Office 1 共 8 个 EVSE | 会话级：`connectionTime`、`disconnectTime`、`doneChargingTime`、`kWhDelivered`、`chargingCurrent`（时间序列）、`pilotSignal`（时间序列）、`userID`、`stationID`、`spaceID`、`siteID`、`clusterID`、`timezone`；用户输入：`WhPerMile`、`kWhRequested`、`milesRequested`、`minutesAvailable`、`requestedDeparture`、`paymentRequired` | 免费但**需注册获取 API token**（实测无 token 返回 401）。Web 界面可导出 JSON | standard |
| **UrbanEV**（深圳） | 24,798 个公共充电桩、1,682 个充电站；2022-09 – 2023-02，**6 个月** | 小时级：`inf.csv`（坐标/容量）、`occupancy.csv`（占用数）、`duration.csv`（h）、`volume.csv`（kWh）、`e_price.csv`（电价 元/kWh）、`s_price.csv`（服务费 元/kWh）、两个气象站天气数据 | **完全开放**：Dryad + GitHub | standard |
| **ST-EVCDP**（深圳，UrbanEV 前身） | 18,061 个公共充电桩；2022-06-19 – 2022-07-18（30 天）；5 分钟最小间隔、8,640 个时间戳；247 个交通小区、1,006 条邻接边 | `occupancy.csv`、`duration.csv`、`volume.csv`、`price.csv`、`information.csv`（桩容量/经纬度/是否 CBD/是否分时电价）、`adj.csv`、`distance.csv`；另有天气 CSV | **完全开放**：GitHub 仓库直接下载 | standard |
| **NREL Workplace Charging Data** | 300+ 台车、4 年（含免费 vs 收费对比、COVID 前后分段）；PHEV 小电池到 BEV 大电池 | 工作场所充电行为数据 | **完全开放**，CC-BY-4.0，OpenEI/OEDI 直接下载 | standard |
| ElaadNL（荷兰） | **未核实**规模与字段 | — | 平台有 Data sharing / Download 入口：<https://elaad.nl/en/data/> | 未核实 |

**引用方式**

- ACN-Data：Lee, Z. J., Li, T., & Low, S. H. (2019). *ACN-Data: Analysis and Applications of an Open EV Charging Dataset*. e-Energy '19. 数据集页 <https://ev.caltech.edu/dataset>
- UrbanEV：Li, H., Qu, H., Tan, X., You, L., Zhu, R., & Fan, W. (2025). *UrbanEV: An Open Benchmark Dataset for Urban Electric Vehicle Charging Demand Prediction*. Scientific Data, 12, 523. DOI `10.1038/s41597-025-04874-4`；Dryad DOI `10.5061/dryad.np5hqc04z`；仓库 <https://github.com/IntelligentSystemsLab/UrbanEV>
- ST-EVCDP：Qu, H., Kuang, H., Li, J., & You, L. (2023). *A physics-informed and attention-based graph learning approach for regional electric vehicle charging demand prediction*. IEEE T-ITS. 仓库 <https://github.com/IntelligentSystemsLab/ST-EVCDP>
- NREL Workplace Charging Data：Neuman, C., Meintz, A., & Jun, M. (2022). DOI `10.7799/1838140`；<https://data.openei.org/submissions/8237>

---

## 2. 充电曲线：可直接用于生成器的结论

### 2.1 直流快充的功率–电量曲线（推荐建模方式）

**形状**：三段式，且第二段不是平滑下降，而是带**台阶**的衰减。

```text
P(soc) =
  爬升段   soc ∈ [0, s_ramp]       : 从 P_start 升到 P_peak
  平顶段   soc ∈ [s_ramp, s_knee]  : P_peak（近似恒功率）
  taper段  soc ∈ [s_knee, 100]     : 单调递减，建议用分段台阶或指数衰减
```

**三个真实车型的实测拐点（不是猜的）**

| 车型 | 峰值 | 满功率平台结束 | 明显 taper 起点 | 80% SOC 处功率 | 80% 处占峰值 |
| --- | --- | --- | --- | --- | --- |
| Tesla Model 3 Performance | 250 kW | ≈15% | ≈20%（50% 时已降至 96 kW） | 46 kW | 18% |
| Hyundai Ioniq 5 LR AWD | 260 kW | ≈50% | 51%（台阶），69% 再台阶 | 67 kW | 26% |
| VW ID.4 Pro | ≈141 kW | ≈62% | ≈63%（缓降） | 73 kW | 52% |

**生成器建议参数（`推断`，但有三个实测锚点支撑）**

- `s_knee` 取 0.45 – 0.65：峰值功率越高取越小值，越低取越大值。
- 80% SOC 处功率设为峰值的 0.2 – 0.5 倍，与峰值功率正相关（峰值越高，80% 处相对衰减越大）。
- 90% 之后快速跌到峰值的 10–25%；100% 附近为峰值的 5–13%。
- **必须实现「能量–时间不对称」**：Model 3 P 曲线上 80→100% 只占 20% 的能量，却占 47% 的时间。若生成器让 80–100% 段时长与能量成正比，曲线真实感会立刻崩掉。

**这一点的业务含义**：80% 之后又慢又不划算，因此真实快充会话极少充到 100%，绝大多数在 80% 附近结束——这正是「10–80%」成为行业标准口径的原因（见 <https://evkx.net/technology/battery/charging/> 的「Use the Same SOC Window」一节）。

### 2.2 交流充电的功率曲线

**结论：交流充电在绝大多数 SOC 区间为恒功率（恒流），仅在接近满电的最后阶段降功率。**

推理链（**量化拐点属 `推断`，定性部分有出处**）：

1. 有出处：AC 充电时 AC→DC 转换由**车载充电机（OBC）**完成，充电桩只提供受控电源、通信与保护，不决定功率曲线（<https://evkx.net/technology/battery/charging/>，`standard`）。因此 AC 曲线 = OBC 策略 ∩ 电池接受能力。
2. 有出处：电池充电收尾阶段存在高 SOC taper；「最后几个百分点耗时不成比例地长」（同上，`standard`）。
3. 有出处：DC 快充曲线上，Model 3 P 在 95–100% 段的功率落在 10–26 kW，与**家用 Level 2 的 7.2 kW** 已属同一量级。这说明高 SOC 段的限制因素是电池本身，而非充电设备。
4. `推断`：因此 AC 会话中 **OBC 满功率维持到约 90–95% SOC，之后转入恒压段并逐步下降**。生成器可把 AC 曲线建模为「平顶 + 尾部衰减」，但**衰减拐点本身没有直接出处，必须标注为推断**。

**AC 相关的确定值**

| 项 | 值 | 来源 | 可信度 |
| --- | --- | --- | --- |
| 美国家用 L2 最常见功率 | 7.2 kW | <https://afdc.energy.gov/fuels/electricity-stations> | standard |
| L2 设备功率范围 | 2.9 – 19.2 kW | 同上 | standard |
| 魁北克公共 L2 中 7.2 kW 占比 | >95% | <https://ar5iv.labs.arxiv.org/html/2504.17722> | typical |
| AC 充电时长（空到满） | 3 – 12 h | <https://www.fueleconomy.gov/feg/evtech.shtml> | standard |
| 11 kW OBC 的车无法从 22 kW 桩取到 22 kW | 定性规律 | <https://evkx.net/technology/battery/charging/> | standard |

### 2.3 可直接引用的车型曲线汇总

| 车型 | 峰值 | 电池 | 10→80% 时间 | 10→80% 平均功率 | 0→100% 时间 | 0→100% 平均功率 |
| --- | --- | --- | --- | --- | --- | --- |
| Tesla Model 3 Performance | 250 kW | ≈75 kWh | 33 min 2 s | 95.3 kW | 1 h 8 min 51 s | 65.4 kW |
| Tesla Model 3 LR (MY2026) | 250 kW | 85.3 kWh | 35 min 16 s | 97.6 kW | 1 h 14 min 15 s | 66.3 kW |
| Hyundai Ioniq 5 LR AWD | 260 kW | 84.0 kWh | 19 min 8 s | 175.5 kW | 1 h 14 min 2 s | 64.8 kW |
| VW ID.4 Pro | ≈141 kW | ≈77 kWh | 28 min 13 s | 114.6 kW | 1 h 3 min 51 s | 72.3 kW |

来源：<https://evkx.net/models/tesla/model_3/model_3_long_range/chargingcurve/>（Model 3 LR）及其余三项对应的 EVKX GitHub 原始数据文件（见 §1.2）。

---

## 3. 与 `charging-insights/src/generate.py` 现状的逐条对照

现有 `charge_step_kw()` 采用**单一 logistic taper**：`taper = 0.12 + 0.88 / (1 + exp((soc - 0.80) / 0.055))`，即 taper 拐点固定在 ~80%、下限为额定的 12%，且**与额定功率无关**。对照本文档的实测锚点：

| # | 现状 | 实测值 | 判断 |
| --- | --- | --- | --- |
| 1 | 拐点固定在 80% SOC | 250 kW 级 ≈20%；800 V 250 kW 级 ≈51%；140 kW 级 ≈63% | ⚠️ **偏晚**。实测的显著 taper 起点普遍低于 80%（高功率桩尤其早），当前模型会让 DC 会话「平均功率偏高、时长偏短」 |
| 2 | taper 曲线与 `rated_kw` 无关 | 峰值越高，80% 处相对衰减越大（18% vs 26% vs 52%） | ⚠️ 建议令拐点随额定功率前移 |
| 3 | 下限为额定的 12% | 80% 处实测为峰值的 18% / 26% / 52% | ⚠️ 偏激进；但 100% 附近实测确实低到 3–14%，故 12% 作为**渐近下限**尚可接受，只是到达 80% 时不应已接近下限 |
| 4 | 平滑 logistic | Ioniq 5 呈明显**台阶**（51% / 69% / 76% 三次掉档） | ℹ️ 可选增强：加入离散台阶更真实 |
| 5 | `TARGET_SOC` 让家用/工作充到 85–90% | 真实快充极少充到 100%；AC 高 SOC 段电池接受功率已很低 | ✅ 注释中「85% overnight 更站得住脚」的判断与证据一致 |
| 6 | `rated_kw * taper` 未与电池容量/倍率耦合 | 同功率对 50 kWh 与 100 kWh 电池的 C-rate 差一倍（`standard`） | ℹ️ 可选增强：按 C-rate 上限再夹一层 |
| 7 | 温度惩罚 `COLD_PENALTY_PER_DEG = 0.022`（每 °C，<20 °C） | DOE/AAA：-7 °C 时能耗 +39%、续航 -41% | ⚠️ 线性外推在 -7 °C 附近会明显低估；建议改为带地板的分段曲线 |
| 8 | `USABLE_SOC_WINDOW = 0.95` | 无直接出处；属合理工程假设 | ℹ️ 需在代码注释中标注为假设 |

> **最重要的一条**：把 taper 拐点从固定 80% 改为随额定功率变化（高功率桩更早进入 taper），会同时改善「平均功率」「会话时长」「DC/AC 能量比」三个下游分析指标的真实性。

---

## 4. 必须避开的「标准值 vs 统计均值」陷阱

1. **中国「平均功率 46.53 kW」不是单桩典型功率。** 它是「公共桩额定总功率 ÷ 枪数」，被直流桩拉高。若用单一 46.53 kW 分布生成中国公共桩功率，会同时错掉交流桩（偏高）和直流桩（偏低）。
2. **「10–80%」是行业评价口径，不是用户行为口径。** 它是为可比性人为划定的窗口（EVKX 明确指出不同 SOC 窗口不可直接比较）。不要把它当作「真实用户充到 80% 就拔枪」的证据。
3. **AC 的「桩功率」≠「实际充电功率」。** 实际值取「建筑供电 / 相数 / 桩额定 / 线缆额定 / OBC 额定 / 电池接受能力 / 用户设置」中的**最小值**（`standard`）。生成 AC 会话时必须显式取 min，否则会出现「7 kW 桩给只支持 3.6 kW 的车充出 7 kW」这类假数据。
4. **充电曲线只在「从低 SOC 起充」时成立。** EVKX 明确提示：从中高 SOC 插枪会以不同方式影响曲线（可能更好也可能更差）。若生成器允许从 60% SOC 起充，不能直接套用 0% 起充的曲线。
5. **曲线是「最佳工况」曲线。** 低温会显著压低曲线（电池需预热）。若不建模温度，冬季场景的充电时长会系统性偏短。

---

## 5. 数据来源可信度说明

| 来源 | 性质 | 在本报告中的定位 |
| --- | --- | --- |
| U.S. DOE / AFDC / fueleconomy.gov（ORNL 与 EPA 维护） | 政府官方 | 功率等级、充电时长、季节能耗影响的 `standard` 依据 |
| AAA（美国汽车协会） | 行业协会受控实验（5 车、气候舱、底盘测功机） | 季节对续航影响的 `standard` 依据 |
| 中国充电联盟（EVCIPA）/ 国家能源局，经中国能源网、机经网、电动汽车观察家转述 | 官方统计 + 行业媒体转述 | 中国桩数量、交直流比例的 `standard`/`typical` 依据。**注意：本次未能直接抓取 evcipa.org.cn 原文（页面 JS 渲染），数字来自两家行业媒体的一致转述** |
| EVKX.net（含其 GitHub 原始数据） | 第三方实测/建模曲线数据库，明确注明工况 | 车型充电曲线的 `typical` 依据；可比性优于厂商宣传值 |
| CleanTechnica / InsideEVs | 媒体，转载用户实测录像 | 交叉验证，`typical` 但为单一实测 |
| 同行评议文献（arXiv/ar5iv 2504.17722） | peer-reviewed 综述 + 实测数据，本身引用 Hardman 2018、Tal 2018/2020、Visaria 2022 等 | 车主行为的 `typical` 依据 |
| UrbanEV / ST-EVCDP（Scientific Data + IEEE T-ITS + Dryad） | 同行评议数据集论文 | 数据集规模的 `standard` 依据 |
| 电动汽车观察家（evobserver.com） | 行业媒体 | 中国 2025 年结构数据的唯一可得来源，标记 `typical` |

---

## 6. 查不到的参数（**不得当作真实值使用**）

以下条目本次**未能找到可靠、可引用的公开数字**。生成器若需要，必须显式标记为「合成假设」，不得宣称基于真实数据。

### 6.1 完全查不到

1. **单次充电时长的分布**（均值 / 中位数 / 分位数），且区分家用慢充 / 公共慢充 / 公共快充三类。**这是本项目最关键的缺口。**
   - 原因：主要数据源受限——NREL/INL *EV Project* 报告含「平均连接时长」表（`https://www.osti.gov/servlets/purl/1159487`、`http://info.ornl.gov`）但为 PDF 且重定向失败；ACN-Data 的 `connectionTime`/`disconnectTime` 需 API token（实测 401）；ElaadNL 报告为荷兰语 PDF。
   - **建议补救路径**：注册 ACN-Data 免费 token（<https://ev.caltech.edu/register>），直接拉 `connectionTime`、`disconnectTime`、`kWhDelivered` 三个字段自行计算分布。这是最可靠的做法，ACN-Data 明确欢迎研究用途。
2. **单次充电电量的分布**（kWh，区分 AC/DC、家用/公共）。
   - 部分可替代：用 §2.3 的曲线可算出「从 SOC_a 充到 SOC_b 会充入多少 kWh」，但把它反推成分布仍需要 SOC 分布（见下条）。
3. **起始 SOC 与结束 SOC 的分布**。未取到任何可靠的直方图或分位数。
   - 唯一线索：某文献（Wiley IET-ITS，`https://onlinelibrary.wiley.com/doi/pdfdirect/10.1049/iet-its.2019.0351`）摘要提到「到达与离开 SOC 常用高斯分布建模」——这只是**建模惯例**，不是实测分布参数，**不可引用为真实值**。
   - 另一条线索：ACN-Data 的部分 Level 3 桩记录了起止 SOC（据 <https://ar5iv.labs.arxiv.org/html/2504.17722> 原文「for some level 3 charging outlets, the starting and ending state of charge are also recorded」）——这是拿到真实 SOC 分布的最佳路径。
4. **以 100% SOC 结束的会话占比**。未取到。
5. **一天中充电时段分布的量化数据**（家用 / 工作 / 公共快充分开的小时级分布）。
   - 唯一线索：INL *EV Project* 的 `2013_vsst_report.pdf` Figure IV-22 有「全国 Level 2 EVSE 按 15 分钟增量的使用率分布」，但为 PDF 未读。
   - 可用的定性结论（`standard`）：家用以夜间为主；工作场所集中在工作日 8 a.m. 之后开始；荷兰公共桩会话中 50% 为过夜充电。
6. **工作日 vs 周末的充电行为差异**（量化）。未取到。
7. **充电站排队 / 等待时间的实测数据**（分钟、等待概率、队列长度）。
   - 确认存在该方向研究（MAP/PH/c 排队模型：<https://www.tandfonline.com/doi/pdf/10.1080/21680566.2026.2642261>；「可接受等待时间」建模：<https://www.mdpi.com/2071-1050/16/6/2536>，抓取被 403 拦截），但**没有取到任何实测均值或分位数**。
   - 另注：上述离散选择论文明确指出其数据**不含等待时间**（原文 "nor is waiting time before charging"），说明该字段在公开数据中普遍缺失。
8. **中国交流充电桩的单桩典型功率**（如 7 kW / 40 kW 的官方分布）。
   - 只找到全网平均 46.53 kW（含直流，不可用）。国标交流桩功率档位与占比未检索到可引用来源。
9. **美国直流快充按功率等级的占比分布**（50 / 120 / 150 / 250 / 350 kW 各占多少）。
   - AFDC 季度报告（<https://afdc.energy.gov/fuels/electricity-infrastructure-trends>）按充电等级统计，但**不按功率档位细分**，且报告本身是 PDF。
   - 找到一条行业媒体线索「Most DC Fast Chargers Have More Than 150 kW (January 2025)」（<https://evchargingstations.com/chargingnews/most-dc-fast-chargers-have-more-than-150-kw/>），但页面抓取失败，**数字未核实，不得引用**。
   - 可用的替代信息（**中国口径，不能套用到美国**）：中国 >240 kW 直流桩占直流桩 7.79%（2024）；>250 kW 大功率枪保有量 202,752 个（2025）。
10. **每周充电次数的直接统计**。只有「每日次数」（0.71–1.47 次/日）与「每年 18–20 次」两种口径，换算成「每周几次」需要假设，属推断。
11. **ElaadNL 数据集的规模与字段**。仅确认存在数据平台与下载入口，未核实规模、字段、许可条款。
12. **IEA Global EV Outlook 2025 中「全球公共桩按慢充/快充的功率分档占比」**。页面为 JS 渲染，正文未取到。

### 6.2 找到了但是二手/单一来源，谨慎使用

| 条目 | 内容 | 问题 |
| --- | --- | --- |
| 工作场所充电「多数在工作日 8 a.m. 后开始」 | 定性 | 仅来自搜索引擎摘要（Sabancı 大学学位论文 PDF），未能读原文 |
| 中国 2025 年结构数据（1537.5 万私人桩、2009.2 万充电枪、46.53 kW 均值等） | 见 §1.1 | 单一行业媒体转述充电联盟数据，未能与官方原文交叉核对 |
| Model 3 LR 在 V3 超充的 250 kW 平顶到 22% | 定性 | 单台车、单次录像，非统计样本 |
| 排队模型论文 | 无实测值 | 仅确认研究方向存在，未提供可用数字 |

---

## 7. 给生成器的最小可信参数集

若只允许使用**完全有出处**的参数，建议采用以下集合；其余一律作为显式「合成假设」，并在 README 中声明。

**桩侧（`standard`）**

- AC L1 = 1.9 kW；AC L2 = 7.2 kW（家用最常见），范围 2.9 – 19.2 kW
- DC 快充 ≤ 500 kW；美国公共桩构成 ≈ 80% L2 / >20% DC / <1% L1
- 中国公共桩 交:直 ≈ 54 : 46

**曲线侧（`typical`，来自 EVKX 实测库）**

- 满功率平台：从 ~1–10% 起，高功率车到 ~15%，低功率车到 ~62%
- taper 起点：~20%（250 kW / 400 V 级）、~51%（250 kW / 800 V 级）、~63%（140 kW 级）
- 80% SOC 功率：峰值 18% / 26% / 52%
- 10→80% 时间：19 – 35 min；0→100% 平均功率 ≈ 峰值的 1/4 – 1/3
- 80→100%：约 20% 的能量、约 47% 的时间

**能耗侧（`standard` / `typical`）**

- 紧凑轿车 11.5 – 14.9 kWh/100km；中型 SUV AWD 14.7 – 16.2 kWh/100km
- 高速（112–120 km/h）能耗上浮 25–50%
- 冬季 -7°C 开暖风：能耗 +39%、续航 -41%；不开暖风：能耗 -8%、续航 -12%
- 夏季 35°C 开空调：续航 -17%

**行为侧（`typical`）**

- 每日充电 0.71 – 1.47 次；人均年 18 – 20 次
- 82–93% 车主以家充为主
- 荷兰公共桩：过夜 50% / 非工作日间 35% / 工作场所 14%

**必须作为「合成假设」显式声明的**

- 单次充电时长分布、单次电量分布、起止 SOC 分布、时段分布曲线形状、工作日/周末差异、排队时长、`USABLE_SOC_WINDOW`、电池容量分布与权重。

---

*文档生成说明：本报告仅使用 `web_search` 与 `web_fetch` 检索公开网络资料；环境内 `web_fetch` 不支持 PDF，故所有 PDF 类来源均已明确标注「未读全文」。所有标注为「由…算得」的数字均可由本文列出的来源数字复现。*
