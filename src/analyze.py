"""Analysis of the generated charging sessions.

What this module is for
-----------------------
The generator produces sessions from behavioural rules. This module takes a
step back and asks *what does that behaviour add up to* -- the questions an
operator or a product manager would actually ask:

1. **When is the grid loaded?** Hourly demand, and which station types cause it.
2. **How fast does a fast charger actually charge?** Nameplate power is not
   delivered power, because the battery tapers. The gap is the interesting bit.
3. **What does cold weather cost?** Consumption, session length and DC power.
4. **Who depends on public fast charging?** Vehicles without a home charger.
5. **How much capacity is wasted on parked-and-finished cars?** Idle time.

A note on interpreting any of this
----------------------------------
The data is synthetic. Every "finding" here is a property of the generator's
behavioural model, not a measurement of the real world. What the analysis *does*
demonstrate is the method: how to go from a session log to a defensible
operational statement, including the checks that rule out an artefact. Where a
conclusion depends on a modelling assumption, it says so.

Usage
-----
    python -m src.analyze               # print the report
    python -m src.analyze --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


def _use_utf8_stdout() -> None:
    """Make the console able to print this report.

    On a Chinese Windows install the console codepage is GBK (cp936), so any
    character outside that set -- the warning sign below, box drawing, most
    non-CJK symbols -- raises ``UnicodeEncodeError`` and kills the program. This
    is an environment problem, not a content problem, so the fix belongs here
    rather than in the report text: force UTF-8 on the output streams and, when
    stdout is a real console, also switch the console's own codepage.

    Errors are replaced rather than raised so that a console which still cannot
    render a glyph degrades to a placeholder instead of crashing mid-report.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # UTF-8
        except Exception:  # pragma: no cover - best effort only
            pass

# Station types split into the two charging regimes that behave differently.
AC_TYPES = ("HOME_AC", "WORK_AC", "PUBLIC_AC")
DC_TYPES = ("PUBLIC_DC",)

STATION_LABELS = {
    "HOME_AC": "家用交流慢充",
    "WORK_AC": "单位交流慢充",
    "PUBLIC_AC": "公共交流慢充",
    "PUBLIC_DC": "公共直流快充",
}

# A vehicle counts as "no home charging" if it never used a home charger.
# That is an inference from the session log rather than a ground-truth flag,
# which is deliberate: this is what an analyst would actually have.


@dataclass
class Finding:
    """One analytical conclusion, with the numbers that support it."""

    key: str
    title: str
    headline: str
    detail: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load the generated CSVs and attach derived columns.

    Parsing timestamps once, here, keeps the rest of the module readable and
    makes the derived time fields (hour, weekday, season) consistent across
    every analysis instead of re-derived per chart.
    """
    sessions = pd.read_csv(data_dir / "sessions.csv")
    stations = pd.read_csv(data_dir / "stations.csv")
    daily = pd.read_csv(data_dir / "daily_summary.csv")

    sessions["start_ts"] = pd.to_datetime(sessions["start_time"])
    sessions["end_ts"] = pd.to_datetime(sessions["end_time"])
    sessions["is_dc"] = sessions["station_type"].isin(DC_TYPES)
    # Grid-side energy is what the operator bills and what the grid supplies;
    # pack-side energy is what the car stores. The difference is losses.
    sessions["losses_kwh"] = sessions["energy_grid_kwh"] - sessions["energy_kwh"]
    # Utilisation factor: how much of the nameplate power was actually used.
    # A low value on a DC charger means the taper or the battery, not the
    # charger, set the pace.
    stations_by_id = stations.set_index("station_id")["rated_kw"]
    sessions["rated_kw"] = sessions["station_id"].map(stations_by_id)
    sessions["power_utilisation"] = sessions["avg_power_kw"] / sessions["rated_kw"]

    return sessions, stations, daily


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------
def analyse_hourly_load(sessions: pd.DataFrame) -> Finding:
    """Q1: when is the system loaded, and by which station types?"""
    by_hour = sessions.groupby("hour")["energy_grid_kwh"].sum()
    peak_hour = int(by_hour.idxmax())
    peak_kwh = float(by_hour.max())
    trough_kwh = float(by_hour.min())
    mean_kwh = float(by_hour.mean())

    # Where the peak comes from. This is the part that makes the finding
    # actionable: a peak caused by home charging and a peak caused by public
    # fast charging call for completely different interventions.
    peak_mix = (sessions[sessions["hour"] == peak_hour]
                .groupby("station_type")["energy_grid_kwh"].sum()
                .sort_values(ascending=False))
    top_type = str(peak_mix.index[0])
    top_share = float(peak_mix.iloc[0] / peak_kwh)

    evening = sessions[sessions["hour"].between(17, 22)]["energy_grid_kwh"].sum()
    total = sessions["energy_grid_kwh"].sum()
    evening_share = float(evening / total)

    detail = [
        f"峰值小时 {peak_hour:02d}:00 供应 {peak_kwh:,.0f} kWh，"
        f"是全天均值 {mean_kwh:,.0f} kWh 的 {peak_kwh / mean_kwh:.2f} 倍",
        f"峰谷比 {peak_kwh / trough_kwh:.1f}:1（最低 {trough_kwh:,.0f} kWh）",
        f"峰值时段由 {STATION_LABELS[top_type]} 贡献 {top_share:.0%}",
        f"17:00-22:00 五个小时吃掉全天 {evening_share:.0%} 的电量",
    ]
    for stype, kwh in peak_mix.head(3).items():
        detail.append(f"    {STATION_LABELS[stype]}: {kwh:,.0f} kWh "
                      f"({kwh / peak_kwh:.0%} of the peak hour)")

    return Finding(
        key="hourly_load",
        title="1. 电网负荷的时间分布",
        headline=f"负荷高度集中：{peak_hour:02d}:00 达到峰值，"
                 f"傍晚五小时占全天 {evening_share:.0%}",
        detail=detail,
        metrics={
            "peak_hour": float(peak_hour),
            "peak_kwh": peak_kwh,
            "trough_kwh": trough_kwh,
            "peak_to_trough": float(peak_kwh / trough_kwh),
            "evening_share": evening_share,
        },
    )


def analyse_power_taper(sessions: pd.DataFrame) -> Finding:
    """Q2: how much of a charger's nameplate power is actually delivered?

    This is the analysis that most clearly separates "read the spec sheet" from
    "measure the system". A 120 kW charger is marketed at 120 kW, but the pack
    decides the real rate, and the pack slows down as it fills.
    """
    dc = sessions[sessions["is_dc"]]
    util = dc["power_utilisation"]

    # Utilisation split by arrival SOC: does arriving empty actually help?
    low = dc[dc["start_soc"] < 0.30]["power_utilisation"]
    high = dc[dc["start_soc"] > 0.55]["power_utilisation"]

    # The same question on AC, where the taper should bite much less because
    # the charger is far below what the pack can accept.
    ac = sessions[~sessions["is_dc"]]
    ac_util = ac["power_utilisation"]

    detail = [
        f"直流桩额定 {dc['rated_kw'].mean():.0f} kW，实际平均只用到 "
        f"{dc['avg_power_kw'].mean():.0f} kW（利用率 {util.mean():.0%}）",
        f"低电量到达（<30% SOC）利用率 {low.mean():.0%}，"
        f"高电量到达（>55% SOC）只有 {high.mean():.0%}",
        "这里的「利用率」只说明额定功率未被用满，不构成设备缺陷：",
        f"    直流：额定功率和电池接受能力都会成为瓶颈。低电量时电池能吃满，"
        f"接近满电时它把功率压下来，所以同一个桩的利用率随到达电量变化"
        f"（{low.mean():.0%} → {high.mean():.0%}）",
        f"    交流：瓶颈几乎只来自桩本身（额定 7 kW，电池能接受得远不止这些），"
        f"利用率 {ac_util.mean():.0%}，且几乎不随 SOC 变化",
        f"直流平均充 {dc['energy_kwh'].mean():.1f} kWh 用 "
        f"{dc['duration_min'].mean():.0f} 分钟；交流平均 "
        f"{ac['energy_kwh'].mean():.1f} kWh 用 {ac['duration_min'].mean():.0f} 分钟",
    ]
    gap = float(1.0 - util.mean())
    return Finding(
        key="power_taper",
        title="2. 额定功率 vs 实际功率（电池 taper 效应）",
        headline=f"直流快充实际只用到额定功率的 {util.mean():.0%}，"
                 f"差额 {gap:.0%} 由电池决定而非充电桩",
        detail=detail,
        metrics={
            "dc_mean_rated_kw": float(dc["rated_kw"].mean()),
            "dc_mean_actual_kw": float(dc["avg_power_kw"].mean()),
            "dc_utilisation": float(util.mean()),
            "dc_utilisation_low_soc": float(low.mean()),
            "dc_utilisation_high_soc": float(high.mean()),
            "ac_utilisation": float(ac_util.mean()),
        },
    )


def analyse_temperature(sessions: pd.DataFrame, daily: pd.DataFrame) -> Finding:
    """Q3: what does cold weather cost, and does it change behaviour?

    Reported separately for AC and DC. A combined average power figure is
    meaningless here: slow charging is capped at 7 kW by the charger no matter
    what the temperature does, so pooling the two regimes buries the fast-charge
    effect under the AC baseline and makes a real 43% drop look like a 40%
    drop from 11 kW to 6 kW.
    """
    sessions = sessions.copy()
    buckets = [-30, 0, 10, 20, 30, 50]
    labels = ["<0°C", "0-10°C", "10-20°C", "20-30°C", ">30°C"]
    sessions["temp_bucket"] = pd.cut(sessions["temp_c"], bins=buckets, labels=labels)

    ac = sessions[~sessions["is_dc"]]
    dc = sessions[sessions["is_dc"]]

    ac_g = ac.groupby("temp_bucket", observed=True).agg(
        n=("session_id", "count"),
        energy=("energy_kwh", "mean"),
        duration=("duration_min", "mean"),
        power=("avg_power_kw", "mean"),
    )
    dc_g = dc.groupby("temp_bucket", observed=True).agg(
        n=("session_id", "count"),
        energy=("energy_kwh", "mean"),
        duration=("duration_min", "mean"),
        power=("avg_power_kw", "mean"),
    )

    detail = ["直流快充（温度对功率的影响在这里才看得见）:"]
    for bucket in labels:
        if bucket in dc_g.index:
            r = dc_g.loc[bucket]
            detail.append(f"  {bucket:>8}: {int(r['n']):>5} 次, "
                          f"平均功率 {r['power']:>5.1f} kW, "
                          f"平均 {r['energy']:.1f} kWh / {r['duration']:.0f} 分钟")
    detail.append("")
    detail.append("交流慢充（功率由桩封顶，几乎不受温度影响）:")
    for bucket in labels:
        if bucket in ac_g.index:
            r = ac_g.loc[bucket]
            detail.append(f"  {bucket:>8}: {int(r['n']):>5} 次, "
                          f"平均功率 {r['power']:>5.1f} kW")

    # The headline number: DC power drop between the coldest and mildest bucket.
    dc_cold = dc[dc["temp_c"] < 0]["avg_power_kw"]
    dc_mild = dc[dc["temp_c"].between(20, 30)]["avg_power_kw"]
    if len(dc_cold) and len(dc_mild):
        drop = float(1.0 - dc_cold.mean() / dc_mild.mean())
        detail.append("")
        detail.append(f"直流：0°C 以下 {dc_cold.mean():.0f} kW vs 20-30°C "
                      f"{dc_mild.mean():.0f} kW，功率下降 {drop:.0%}")
    else:
        drop = float("nan")

    return Finding(
        key="temperature",
        title="3. 温度的影响",
        headline=("低温显著降低直流快充功率（下降 {:.0%}）".format(drop)
                  if drop == drop else "温度对充电功率的影响"),
        detail=detail,
        metrics={"dc_power_drop_cold": drop},
    )


def analyse_home_access(sessions: pd.DataFrame) -> Finding:
    """Q4: does lacking a home charger change behaviour?

    This is the structural question with the clearest policy implication: if
    the cars that cannot charge at home are also the ones loading the public
    network at peak, then charger siting and tariff design have a target.
    """
    home_users = set(sessions.loc[sessions["station_type"] == "HOME_AC",
                                  "vehicle_id"])
    sessions = sessions.copy()
    sessions["has_home"] = sessions["vehicle_id"].isin(home_users)

    grouped = sessions.groupby("has_home").agg(
        n_sessions=("session_id", "count"),
        n_vehicles=("vehicle_id", "nunique"),
        energy=("energy_kwh", "sum"),
        dc_share_energy=("energy_kwh", "sum"),
    )
    # Per-vehicle comparison, because the two groups differ in size.
    per_vehicle = sessions.groupby(["has_home", "vehicle_id"]).agg(
        sessions=("session_id", "count"),
        energy=("energy_kwh", "sum"),
        dc_sessions=("is_dc", "sum"),
    ).reset_index()

    summary = per_vehicle.groupby("has_home").agg(
        sessions_per_vehicle=("sessions", "mean"),
        energy_per_vehicle=("energy", "mean"),
        dc_per_vehicle=("dc_sessions", "mean"),
    )

    with_home = summary.loc[True] if True in summary.index else None
    without_home = summary.loc[False] if False in summary.index else None

    detail = []
    if with_home is not None and without_home is not None:
        detail = [
            f"有家充的车（{int(grouped.loc[True, 'n_vehicles'])} 台）："
            f"每车 {with_home['sessions_per_vehicle']:.1f} 次充电，"
            f"其中直流 {with_home['dc_per_vehicle']:.1f} 次",
            f"无家充的车（{int(grouped.loc[False, 'n_vehicles'])} 台）："
            f"每车 {without_home['sessions_per_vehicle']:.1f} 次充电，"
            f"其中直流 {without_home['dc_per_vehicle']:.1f} 次",
        ]
        ratio = float(
            (without_home["dc_per_vehicle"] / max(with_home["dc_per_vehicle"], 1e-9))
        )
        detail.append(f"无家充车辆的直流充电频次是有家充车辆的 {ratio:.1f} 倍")
        detail.append("含义：公共快充需求主要由「没有私人桩」这个结构性条件驱动，"
                      "而不是由行驶里程驱动")
    dc_ratio = ratio if with_home is not None and without_home is not None else float("nan")

    return Finding(
        key="home_access",
        title="4. 家充条件如何改变充电行为",
        headline=f"没有私人充电桩的车，直流快充使用频次高出 {dc_ratio:.1f} 倍",
        detail=detail,
        metrics={"dc_frequency_ratio": dc_ratio},
    )


def analyse_idle_time(sessions: pd.DataFrame) -> Finding:
    """Q5: how much plugged-in time is spent not charging?

    Connector occupancy, not charging speed, is the binding constraint at most
    sites, and a car that has finished charging occupies a connector it is not
    using. Quantifying that turns "we should add idle fees" from an opinion
    into a number.

    Ranked by **recoverable connector-hours**, not by idle share. A site type
    can have a high idle *fraction* and still recover very little total time if
    it is rarely used; an operator deciding where to intervene cares about the
    absolute hours freed, which is volume times per-session idle.
    """
    sessions = sessions.copy()
    sessions["occupied_min"] = sessions["duration_min"] + sessions["idle_min"]
    sessions["idle_share"] = sessions["idle_min"] / sessions["occupied_min"]

    by_type = sessions.groupby("station_type").agg(
        n=("session_id", "count"),
        charge_min=("duration_min", "mean"),
        idle_min=("idle_min", "mean"),
        idle_share=("idle_share", "mean"),
        idle_hours_total=("idle_min", lambda s: s.sum() / 60.0),
    )
    by_type["connector_hours"] = by_type["n"] * (
        by_type["charge_min"] + by_type["idle_min"]
    ) / 60.0
    by_type["idle_of_connector_hours"] = (
        by_type["idle_hours_total"] / by_type["connector_hours"]
    )
    by_type = by_type.sort_values("idle_hours_total", ascending=False)

    detail = []
    for stype, row in by_type.iterrows():
        detail.append(
            f"{STATION_LABELS[stype]:>14}: {int(row['n']):>5} 次, "
            f"充电 {row['charge_min']:>5.0f} 分钟 + 占位 {row['idle_min']:>5.0f} 分钟, "
            f"共占连接器 {row['connector_hours']:>7,.0f} 小时, "
            f"其中占位 {row['idle_hours_total']:>7,.0f} 小时 "
            f"({row['idle_of_connector_hours']:.0%})"
        )

    worst = str(by_type.index[0])
    worst_row = by_type.iloc[0]
    total_idle = float(by_type["idle_hours_total"].sum())
    total_conn = float(by_type["connector_hours"].sum())

    detail.append("")
    detail.append(f"全部站点合计：连接器被占用 {total_conn:,.0f} 小时，"
                  f"其中 {total_idle:,.0f} 小时（{total_idle / total_conn:.0%}）"
                  f"是充完没走")
    detail.append(f"按可释放时长排序，{STATION_LABELS[worst]} 最值得治理："
                  f"可释放 {worst_row['idle_hours_total']:,.0f} 小时")

    return Finding(
        key="idle_time",
        title="5. 连接器占位（充完不走）",
        headline=f"连接器有 {total_idle / total_conn:.0%} 的时间被「充完没走」的车占着，"
                 f"合计 {total_idle:,.0f} 小时",
        detail=detail,
        metrics={
            "total_idle_hours": total_idle,
            "total_connector_hours": total_conn,
            "idle_share_of_occupancy": total_idle / total_conn,
            "top_type_idle_hours": float(worst_row["idle_hours_total"]),
        },
    )


def analyse_efficiency(sessions: pd.DataFrame) -> Finding:
    """Supporting analysis: charging losses and their cost."""
    total_pack = float(sessions["energy_kwh"].sum())
    total_grid = float(sessions["energy_grid_kwh"].sum())
    losses = total_grid - total_pack

    by_type = sessions.groupby("station_type").apply(
        lambda g: 1.0 - g["energy_kwh"].sum() / g["energy_grid_kwh"].sum(),
        include_groups=False,
    ).sort_values(ascending=False)

    detail = [f"{STATION_LABELS[t]}: 损耗 {v:.1%}" for t, v in by_type.items()]
    detail.append(f"总损耗 {losses:,.0f} kWh，相当于多供了 {losses / total_grid:.1%} 的电")

    return Finding(
        key="efficiency",
        title="6. 充电损耗",
        headline=f"从电网到电池，总损耗 {losses / total_grid:.1%}（{losses:,.0f} kWh）",
        detail=detail,
        metrics={"loss_fraction": losses / total_grid, "loss_kwh": losses},
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def run_all(data_dir: Path) -> tuple[list[Finding], dict[str, pd.DataFrame]]:
    """Run every analysis and return the findings plus intermediate tables."""
    sessions, stations, daily = load(data_dir)

    findings = [
        analyse_hourly_load(sessions),
        analyse_power_taper(sessions),
        analyse_temperature(sessions, daily),
        analyse_home_access(sessions),
        analyse_idle_time(sessions),
        analyse_efficiency(sessions),
    ]

    tables = {
        "sessions": sessions,
        "stations": stations,
        "daily": daily,
        "hourly_energy": sessions.groupby("hour")["energy_grid_kwh"]
                                 .sum().rename("energy_grid_kwh").reset_index(),
    }
    return findings, tables


def format_report(findings: list[Finding], sessions: pd.DataFrame) -> str:
    lines: list[str] = []
    rule = "=" * 74

    lines.append(rule)
    lines.append("  EV 充电行为分析报告")
    lines.append(rule)
    n_days = sessions["date"].nunique()
    n_veh = sessions["vehicle_id"].nunique()
    lines.append(f"  样本：{len(sessions):,} 次充电 / {n_veh} 台车 / {n_days} 天")
    lines.append(f"  总电量：{sessions['energy_grid_kwh'].sum():,.0f} kWh（电网侧）")
    lines.append(f"  时间范围：{sessions['date'].min()} ~ {sessions['date'].max()}")
    lines.append("")
    lines.append("  ⚠ 数据为行为模型生成的合成数据，以下均为模型性质，非真实世界测量值。")
    lines.append(rule)

    for f in findings:
        lines.append("")
        lines.append(f.title)
        lines.append("-" * 74)
        lines.append(f"  ▶ {f.headline}")
        lines.append("")
        for d in f.detail:
            lines.append(f"    {d}")

    lines.append("")
    lines.append(rule)
    lines.append("  结论汇总")
    lines.append(rule)
    for i, f in enumerate(findings, 1):
        lines.append(f"  {i}. {f.headline}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Analyse charging sessions")
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--json", type=Path, default=None,
                        help="also write findings as JSON")
    args = parser.parse_args(argv)

    findings, tables = run_all(args.data)
    print(format_report(findings, tables["sessions"]))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {"key": f.key, "title": f.title, "headline": f.headline,
             "detail": f.detail, "metrics": f.metrics}
            for f in findings
        ]
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"findings written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
