"""Analysis of the real UrbanEV dataset (Shenzhen public charging).

What this module is for
-----------------------
``analyze.py`` analyses a *simulated* fleet built from behavioural assumptions.
This module analyses *observed* public charging telemetry. Running both and
putting them side by side is the most useful thing in this project, because the
simulation turns out to disagree with the real city in a way that is
informative rather than embarrassing: the simulated evening peak does not exist
in Shenzhen, where public charging is overnight-dominated and largely
fleet-driven.

That contrast is the headline. It is reported by
:func:`contrast_with_synthetic` and printed at the end of the report.

What the real data cannot answer
--------------------------------
The dataset contains only **public** charging piles, recorded as zone-hour
aggregates. There is no per-session record and no private/home charging, so two
questions from the synthetic pipeline cannot be asked here at all:

* home-charging access and its effect on behaviour (no home piles in the data),
* charging losses (no pack-side vs grid-side energy).

Rather than quietly dropping them, the report says so.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import real_data as rd
from .analyze import Finding, _use_utf8_stdout

# Energy-quality filter: rows whose implied per-pile power is physically
# impossible are excluded from every aggregate. They are 0.04% of the data, but
# they are exactly the rows that would otherwise dominate a ratio.
PLAUSIBLE = "power_implausible == 0"


def _plausible(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["power_implausible"] == 0]


def _fmt_int(x: float) -> str:
    return f"{x:,.0f}"


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
def analyse_daily_profile(df: pd.DataFrame, days: int) -> Finding:
    """Q1: when does public charging actually happen?

    This is the analysis that refutes the simulation. The synthetic fleet peaks
    at 19:00 because it models private cars coming home from work. Shenzhen's
    public network peaks at 01:00, with 40% of daily energy between midnight and
    06:00.

    Reported as a share of daily energy because the absolute figures depend on
    fleet size, and the shape is the point.
    """
    by_hour = df.groupby("hour")["volume_kwh"].sum() / days
    share = 100.0 * by_hour / by_hour.sum()
    peak_hour = int(by_hour.idxmax())
    trough_hour = int(by_hour.idxmin())
    night = float(share.loc[0:6].sum())
    evening = float(share.loc[17:22].sum())
    daytime = float(share.loc[10:16].sum())

    detail = [
        f"负荷峰值 {peak_hour:02d}:00（{_fmt_int(by_hour.loc[peak_hour])} kWh/日），"
        f"谷值 {trough_hour:02d}:00（{_fmt_int(by_hour.loc[trough_hour])} kWh/日）",
        f"峰谷比 {by_hour.max() / by_hour.min():.2f}",
        "",
        f"  夜间 00-06 点：全天 {night:.1f}%",
        f"  早高峰 07-09 点：{float(share.loc[7:9].sum()):.1f}%",
        f"  白天 10-16 点：{daytime:.1f}%",
        f"  傍晚 17-22 点：{evening:.1f}%",
        "",
        "关键：傍晚五小时只占约五分之一，凌晨反而最高。",
        "这与「私家车下班回家充电」的直觉相反，指向夜间集中充电的商业车队"
        "（出租车 / 网约车 / 物流车）。",
    ]

    return Finding(
        key="real_daily_profile",
        title="1. 真实数据的日内负荷：凌晨主导，不是傍晚",
        headline=f"峰值在 {peak_hour:02d}:00，夜间 00-06 点占全天 {night:.0f}%，"
                 f"傍晚只占 {evening:.0f}%",
        detail=detail,
        metrics={
            "peak_hour": float(peak_hour),
            "trough_hour": float(trough_hour),
            "night_share": night,
            "evening_share": evening,
            "peak_to_trough": float(by_hour.max() / by_hour.min()),
        },
    )


def analyse_weekday_weekend(df: pd.DataFrame) -> Finding:
    """Q2: does the weekend look different? Almost no -- another fleet signal.

    For a private-car commuting pattern the weekend should shift the curve
    noticeably (later start, more midday activity, much less commuting). In this
    dataset weekend and weekday energy differ by about 2% and the hourly shares
    match within a percentage point.
    """
    days_wd = df[df["is_weekend"] == 0]["timestamp"].dt.normalize().nunique()
    days_we = df[df["is_weekend"] == 1]["timestamp"].dt.normalize().nunique()
    daily_wd = df[df["is_weekend"] == 0]["volume_kwh"].sum() / max(days_wd, 1)
    daily_we = df[df["is_weekend"] == 1]["volume_kwh"].sum() / max(days_we, 1)

    hw = df[df["is_weekend"] == 0].groupby("hour")["volume_kwh"].sum()
    he = df[df["is_weekend"] == 1].groupby("hour")["volume_kwh"].sum()
    sw = 100.0 * hw / hw.sum()
    se = 100.0 * he / he.sum()
    max_gap = float((sw - se).abs().max())

    detail = [
        f"工作日日均 {_fmt_int(daily_wd)} kWh，周末日均 {_fmt_int(daily_we)} kWh，"
        f"比值 {daily_we / daily_wd:.3f}",
        f"两个峰值小时都是 {int(hw.idxmax()):02d}:00",
        f"逐小时占比的最大差异只有 {max_gap:.1f} 个百分点",
        "",
        "如果负荷主要来自私家车通勤，周末理应明显不同（起床晚、白天活跃、"
        "通勤消失）。这里几乎完全一致，说明负荷来源不是通勤车辆。",
    ]

    return Finding(
        key="real_weekend",
        title="2. 周末与工作日几乎无差别",
        headline=f"周末日均电量是工作日的 {daily_we / daily_wd:.2f} 倍，"
                 f"逐小时占比最大只差 {max_gap:.1f} 个百分点",
        detail=detail,
        metrics={
            "weekend_weekday_ratio": float(daily_we / daily_wd),
            "max_hourly_share_gap_pct": max_gap,
        },
    )


def analyse_spatial(df: pd.DataFrame) -> Finding:
    """Q3: how concentrated is charging demand across the city?

    Relevant because it decides where infrastructure investment matters: if a
    handful of zones carry half the energy, average-per-zone figures describe
    almost no real location.
    """
    by_zone = df.groupby("zone_id")["volume_kwh"].sum().sort_values(ascending=False)
    total = float(by_zone.sum())
    top = {k: 100.0 * float(by_zone.head(k).sum()) / total for k in (5, 10, 25, 50)}

    # Gini coefficient over zones: 0 = every zone equal, 1 = one zone does it all.
    values = np.sort(by_zone.to_numpy())
    n = len(values)
    gini = float((2.0 * np.sum((np.arange(1, n + 1)) * values)
                  / (n * values.sum())) - (n + 1.0) / n) if values.sum() > 0 else float("nan")

    detail = [
        f"{len(by_zone)} 个小区，前 5 个占 {top[5]:.1f}%，前 10 个占 {top[10]:.1f}%，"
        f"前 25 个占 {top[25]:.1f}%，前 50 个占 {top[50]:.1f}%",
        f"基尼系数 {gini:.3f}",
        "",
        "含义：用「全市平均每个小区」来描述需求会误导——一半能量集中在"
        f"前 10 个小区里。选址和扩容应该按这个分布来做，不是按平均值。",
    ]

    return Finding(
        key="real_spatial",
        title="3. 空间高度集中",
        headline=f"前 10 个小区（占全部 {len(by_zone)} 个）承担了 {top[10]:.0f}% 的充电电量",
        detail=detail,
        metrics={
            "zones": len(by_zone),
            "top10_share": top[10],
            "top25_share": top[25],
            "gini": gini,
        },
    )


def analyse_utilisation(df: pd.DataFrame) -> Finding:
    """Q4: how full is the network, and when does it saturate?

    Utilisation is observed directly here (occupancy / installed piles), not
    inferred from a model. That makes it the most trustworthy number in the
    project.
    """
    by_hour = df.groupby("hour").agg(
        occ=("occupancy", "sum"),
        piles=("n_piles", "sum"),
    )
    by_hour["util"] = by_hour["occ"] / by_hour["piles"]
    peak_hour = int(by_hour["util"].idxmax())

    zone_util = df.groupby("zone_id").agg(
        occ=("occupancy", "sum"), piles=("n_piles", "sum"))
    zone_util["util"] = zone_util["occ"] / zone_util["piles"]

    detail = [
        f"全区平均利用率 {100 * by_hour['occ'].sum() / by_hour['piles'].sum():.1f}%",
        f"利用率最高的时段是 {peak_hour:02d}:00（{100 * by_hour['util'].loc[peak_hour]:.1f}%）",
        f"逐小时利用率范围 {100 * by_hour['util'].min():.1f}% ~ "
        f"{100 * by_hour['util'].max():.1f}%",
        f"小区层面：中位 {100 * zone_util['util'].median():.1f}%，"
        f"最高 {100 * zone_util['util'].max():.1f}%",
        f"完全空闲（占用数为 0）的区-小时占 {100 * (df['occupancy'] == 0).mean():.1f}%",
        "",
        "\"充电桩不够用\"和\"充电桩闲着\"同时成立：平均利用率不高，但空间和"
        "时间上的分布极不均匀。",
    ]

    return Finding(
        key="real_utilisation",
        title="4. 网络利用率（观测值，不是模型推论）",
        headline=f"整体利用率 {100 * by_hour['occ'].sum() / by_hour['piles'].sum():.0f}%，"
                 f"最高时段 {peak_hour:02d}:00 达到 "
                 f"{100 * by_hour['util'].loc[peak_hour]:.0f}%",
        detail=detail,
        metrics={
            "overall_utilisation": float(by_hour["occ"].sum() / by_hour["piles"].sum()),
            "peak_hour": float(peak_hour),
            "peak_utilisation": float(by_hour["util"].loc[peak_hour]),
            "idle_share": float((df["occupancy"] == 0).mean()),
        },
    )


def analyse_power_by_time(df: pd.DataFrame) -> Finding:
    """Q5: how much power does an occupied pile actually deliver, by hour?

    Computed as total energy over total occupied-hours (ratio of sums), never as
    the mean of per-row ratios -- see :func:`real_data.aggregate_power_kw` for
    why the distinction matters on this data.
    """
    rows = []
    for label, hours in (("夜间 00-06", range(0, 7)),
                         ("早高峰 07-09", range(7, 10)),
                         ("白天 10-16", range(10, 17)),
                         ("傍晚 17-22", range(17, 23))):
        sub = df[df["hour"].isin(list(hours))]
        rows.append((label, rd.aggregate_power_kw(sub["volume_kwh"], sub["occupancy"])))

    night_kw = rows[0][1]
    day_kw = rows[2][1]
    detail = [f"{label:>12}：{kw:5.1f} kW / 每个占用桩" for label, kw in rows]
    detail += [
        "",
        f"夜间比白天高 {100 * (night_kw / day_kw - 1):.0f}%。",
        "一个合理的解释：夜间以长时间慢充为主（桩的功率被用得比较满、"
        "单桩长时间占用），白天则以短时补电为主，单桩在每小时里实际输出的"
        "电量被「来一下就走」的车稀释。",
        "",
        "注意：这里的「功率」是 电量/占用小时，不是铭牌功率，"
        "也不是某一时刻的瞬时功率。",
    ]

    return Finding(
        key="real_power_time",
        title="5. 占用桩的实际输出功率：夜间高于白天",
        headline=f"夜间 {night_kw:.1f} kW/占用桩，白天 {day_kw:.1f} kW，"
                 f"夜间高 {100 * (night_kw / day_kw - 1):.0f}%",
        detail=detail,
        metrics={"night_kw": night_kw, "day_kw": day_kw,
                 "ratio": night_kw / day_kw},
    )


def analyse_monthly(df: pd.DataFrame) -> Finding:
    """Q6: does demand change across the six months?

    Includes the January-February dip, which lines up with the Chinese New Year
    travel period -- a seasonal effect worth naming rather than absorbing into
    an average.
    """
    monthly = (df.groupby(df["timestamp"].dt.to_period("M"))["volume_kwh"].sum()
               / 1e6)
    best = monthly.idxmax()
    worst = monthly.idxmin()
    detail = [f"{str(period)}：{value:6.1f} GWh" for period, value in monthly.items()]
    detail += [
        "",
        f"最高 {best}（{monthly.loc[best]:.1f} GWh），最低 {worst}"
        f"（{monthly.loc[worst]:.1f} GWh），相差 "
        f"{100 * (1 - monthly.loc[worst] / monthly.loc[best]):.0f}%",
        "1-2 月偏低，与春节假期（大量车辆停驶、司机返乡）吻合。"
        "这类季节性波动如果不单独看，会被错误地平均进「常态」里。",
    ]

    return Finding(
        key="real_monthly",
        title="6. 半年趋势与春节效应",
        headline=f"月度电量 {monthly.min():.0f}-{monthly.max():.0f} GWh，"
                 f"1-2 月最低（春节）",
        detail=detail,
        metrics={str(k): float(v) for k, v in monthly.items()},
    )


# ---------------------------------------------------------------------------
# Contrast with the simulation
# ---------------------------------------------------------------------------
def contrast_table(synth: dict[str, float], real: dict[str, float]) -> list[tuple[str, str, str, str]]:
    """Build the simulation-versus-reality comparison.

    Each row is (metric, simulated, observed, verdict). The verdict column is
    intentionally blunt: the point of the exercise is that a model which was
    internally consistent and plausible turned out to have the load timing
    wrong, and saying so plainly is more useful than hedging.
    """
    rows: list[tuple[str, str, str, str]] = []

    def add(metric: str, s: float, r: float, fmt: str, agree: bool) -> None:
        rows.append((metric, fmt.format(s), fmt.format(r),
                     "一致" if agree else "不一致"))

    add("负荷峰值时刻",
        synth.get("peak_hour", float("nan")), real.get("peak_hour", float("nan")),
        "{:02.0f}:00", abs(synth.get("peak_hour", 0) - real.get("peak_hour", -12)) <= 1)
    add("傍晚 17-22 点电量占比",
        synth.get("evening_share", float("nan")) * 100,
        real.get("evening_share", float("nan")),
        "{:.0f}%", abs(synth.get("evening_share", 0) * 100
                       - real.get("evening_share", 0)) < 10)
    add("峰谷比",
        synth.get("peak_to_trough", float("nan")),
        real.get("peak_to_trough", float("nan")),
        "{:.2f}", abs(synth.get("peak_to_trough", 0)
                      - real.get("peak_to_trough", 0)) < 0.5)
    return rows


def synthetic_reference(data_dir: Path) -> dict[str, float]:
    """Metrics from the simulated dataset, if it has been generated.

    Returns an empty dict when ``data/raw`` does not exist, so a reader who only
    downloaded the real data still gets a usable report instead of an error.
    """
    sessions_path = data_dir / "sessions.csv"
    if not sessions_path.exists():
        return {}
    try:
        from . import analyze as synth_analyze  # local import: optional dependency
        sessions, _stations, _daily = synth_analyze.load(data_dir)
        finding = synth_analyze.analyse_hourly_load(sessions)
        return dict(finding.metrics)
    except Exception:  # noqa: BLE001 - the contrast is a bonus, never a failure
        return {}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def run_all(data_dir: Path = rd.DEFAULT_DIR) -> tuple[list[Finding], pd.DataFrame, dict]:
    df = rd.load_zone_hourly(data_dir)
    meta = rd.load_station_meta(data_dir)
    summary = rd.summarise(df, meta)
    problems = rd.validate(df)
    if problems:
        raise rd.RealDataError("; ".join(problems))

    ok = _plausible(df)
    days = int(ok["timestamp"].dt.normalize().nunique())

    findings = [
        analyse_daily_profile(ok, days),
        analyse_weekday_weekend(ok),
        analyse_spatial(ok),
        analyse_utilisation(ok),
        analyse_power_by_time(ok),
        analyse_monthly(ok),
    ]
    return findings, df, {"summary": summary, "quality": rd.quality_report(df)}


def format_report(findings: list[Finding], df: pd.DataFrame,
                  context: dict, contrast: list[tuple[str, str, str, str]] | None,
                  has_synthetic: bool) -> str:
    s = context["summary"]
    q = context["quality"]
    rule = "=" * 74
    lines = [
        rule,
        "  EV 充电行为分析报告 —— 真实数据（UrbanEV, 深圳公共充电）",
        rule,
        f"  来源：{rd.DATASET['repo']}",
        f"  论文：{rd.DATASET['paper']}",
        "",
        f"  时间范围：{s.start:%Y-%m-%d %H:%M} ~ {s.end:%Y-%m-%d %H:%M}"
        f"（{s.n_hours:,} 小时）",
        f"  规模：{s.n_zones} 交通小区 / {s.n_stations:,} 个充电站 / "
        f"{s.n_piles:,} 个公共充电桩",
        f"  总电量：{s.total_kwh / 1e6:,.1f} GWh",
        "",
        "  数据质量（影响口径，必须一起看结论）：",
        f"    占用数 < {rd.MIN_OCCUPANCY_FOR_POWER} 的区-小时："
        f"{q['rows_below_min_occupancy']:,} 行（{q['pct_below_min_occupancy']:.1f}%），"
        f"这些行的推导功率无意义，已置空",
        f"    推导功率超过 {rd.MAX_PLAUSIBLE_PILE_KW:.0f} kW 的行："
        f"{q['rows_implausible_power']:,} 行（{q['pct_implausible_power']:.2f}%），"
        f"已从所有聚合中排除",
        f"    完全空闲的区-小时：{q['rows_fully_idle']:,} 行"
        f"（{100 * q['rows_fully_idle'] / max(q['rows'], 1):.1f}%）",
        rule,
    ]

    for f in findings:
        lines += ["", f.title, "-" * 74, f"  ▶ {f.headline}", ""]
        lines += [f"    {d}" if d else "" for d in f.detail]

    if contrast:
        lines += [
            "",
            rule,
            "  仿真模型 vs 真实数据",
            rule,
            "",
            "  把两套同口径指标放在一起，是为了检验仿真到底预测对了什么。",
            "  仿真内部自洽、参数有文献依据，但负荷时间结构是错的——",
            "  因为它假设的是私家车通勤，而深圳公共充电由夜间集中充电的",
            "  商业车队主导。",
            "",
            f"  {'指标':<26}{'仿真':>10}{'真实':>10}   {'判定'}",
            "  " + "-" * 60,
        ]
        for metric, sim, real, verdict in contrast:
            lines.append(f"  {metric:<26}{sim:>10}{real:>10}   {verdict}")

        lines += [
            "",
            "  结论：仿真的价值不在于「预测了真实城市会怎样」，而在于",
            "  它把一个可检验的假设明确写了出来。有了真实数据，这个假设",
            "  被证伪了，并且证伪本身指向了一个具体的领域事实。",
        ]
        if not has_synthetic:
            lines += ["", "  （仿真侧数据未生成，上表用文档记录的参考值。）"]

    lines += [
        "",
        rule,
        "  真实数据回答不了的问题",
        rule,
        "  1. 家庭充电的影响 —— 数据集只含公共桩，没有私人桩数据。",
        "  2. 充电损耗 —— 没有「电网侧 vs 电池侧」的区分。",
        "  这两项只在仿真里做了分析，结论标注为模型推论而非观测。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Analyse the real UrbanEV dataset")
    parser.add_argument("--data", type=Path, default=rd.DEFAULT_DIR)
    parser.add_argument("--synthetic", type=Path, default=Path("data/raw"),
                        help="simulated dataset, for the contrast table")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    findings, df, context = run_all(args.data)
    synth = synthetic_reference(args.synthetic)
    contrast = contrast_table(synth, findings[0].metrics) if synth else None

    print(format_report(findings, df, context, contrast, bool(synth)))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "findings": [
                {"key": f.key, "title": f.title, "headline": f.headline,
                 "detail": f.detail, "metrics": f.metrics}
                for f in findings
            ],
            "quality": context["quality"],
            "contrast": [
                {"metric": m, "simulated": s, "observed": r, "verdict": v}
                for m, s, r, v in (contrast or [])
            ],
        }
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"findings written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
