"""Chart generation for the charging analysis.

Design notes
------------
* **Fonts.** Matplotlib ships no CJK-capable font, so on a Chinese Windows
  install every label renders as tofu boxes unless a font is configured
  explicitly. :func:`configure_fonts` probes for the fonts actually present and
  disables the axes' unicode-minus replacement, which is the second half of the
  problem (a configured CJK font otherwise renders negative numbers as boxes).
* **One message per figure.** Each chart answers exactly one of the questions in
  ``analyze.py``; if a figure needs a paragraph to explain, it is doing too much.
* **The synthetic-data caveat is drawn on every figure**, not only in the
  README. A chart travels further than the text around it, and a chart that
  looks like real measurement without saying otherwise is misleading.
* **Real-data figures get a different caveat**, naming the dataset instead. The
  two must never be confused: one is a model, the other is measurement.

Usage
-----
    python -m src.plots                     # synthetic figures -> figures/
    python -m src.plots --real              # real-data figures -> figures/
    python -m src.plots --dpi 200
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless: no display needed, works over SSH and in CI
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

from .analyze import DC_TYPES, STATION_LABELS, load

# Ordering used consistently across every stacked/grouped chart, cheapest and
# slowest first.
STATION_ORDER = ["HOME_AC", "WORK_AC", "PUBLIC_AC", "PUBLIC_DC"]
STATION_COLORS = {
    "HOME_AC": "#4C72B0",
    "WORK_AC": "#5DA271",
    "PUBLIC_AC": "#E1A33B",
    "PUBLIC_DC": "#C4453C",
}

# Fonts to try, best first. SimHei and Microsoft YaHei are present on Chinese
# Windows installs; Noto/WenQuanYi cover Linux.
CJK_CANDIDATES = [
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC",
    "WenQuanYi Zen Hei", "PingFang SC", "Heiti SC", "SimSun",
]

CAVEAT = "合成数据（行为模型生成）· 非真实世界测量值"
REAL_CAVEAT = ("真实观测数据 · UrbanEV 深圳公共充电 · "
               "17,532 桩 · 2022-09 ~ 2023-02")


def configure_fonts() -> str:
    """Pick an available CJK font and return its name.

    Raises if none is found, because silently falling back produces a figure
    full of empty boxes that looks like a code bug rather than a missing font.
    """
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((name for name in CJK_CANDIDATES if name in available), None)
    if chosen is None:
        raise RuntimeError(
            "no CJK font found; install one of: " + ", ".join(CJK_CANDIDATES)
        )
    plt.rcParams["font.sans-serif"] = [chosen] + CJK_CANDIDATES
    plt.rcParams["font.family"] = "sans-serif"
    # Without this, a CJK font renders the minus sign in tick labels as tofu.
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 110
    plt.rcParams["savefig.bbox"] = "tight"
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.25
    plt.rcParams["axes.axisbelow"] = True
    return chosen


def _stamp(fig: plt.Figure, text: str = CAVEAT) -> None:
    """Put the provenance caveat on the figure itself."""
    fig.text(0.995, 0.005, text, ha="right", va="bottom",
             fontsize=7.5, color="#888888")


def _thousands(x: float, _pos: int) -> str:
    return f"{x:,.0f}"


def _save(fig: plt.Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def fig_hourly_load(sessions: pd.DataFrame, out: Path) -> Path:
    """When the grid is loaded, split by station type."""
    pivot = (sessions.pivot_table(index="hour", columns="station_type",
                                  values="energy_grid_kwh", aggfunc="sum")
             .reindex(columns=STATION_ORDER).fillna(0.0))
    hours = pivot.index.to_numpy()
    peak_hour = int(pivot.sum(axis=1).idxmax())

    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    ax.stackplot(hours, [pivot[c].to_numpy() for c in STATION_ORDER],
                 labels=[STATION_LABELS[c] for c in STATION_ORDER],
                 colors=[STATION_COLORS[c] for c in STATION_ORDER], alpha=0.9)

    total = pivot.sum(axis=1)
    ax.axvline(peak_hour, color="#333333", ls="--", lw=1.0)
    ax.annotate(f"峰值 {peak_hour:02d}:00\n{total.max():,.0f} kWh",
                xy=(peak_hour, total.max()), xytext=(peak_hour - 6.2, total.max() * 0.94),
                fontsize=9.5, color="#333333",
                arrowprops=dict(arrowstyle="->", color="#333333", lw=1.0))

    evening = total.loc[17:22].sum() / total.sum()
    ax.set_title(f"电网负荷的时间分布：傍晚五小时占全天 {evening:.0%}",
                 fontsize=13, fontweight="bold")
    ax.set_xlabel("一天中的小时")
    ax.set_ylabel("充电电量（kWh，电网侧）")
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlim(0, 23)
    ax.yaxis.set_major_formatter(FuncFormatter(_thousands))
    ax.legend(loc="upper left", framealpha=0.9)
    _stamp(fig)
    return _save(fig, out / "01_hourly_load.png")


def fig_charge_curve(sessions: pd.DataFrame, out: Path) -> Path:
    """Why nameplate power overstates real charging: the acceptance curve.

    Left panel is drawn as the *constraints themselves* rather than as several
    resulting curves, because the point is that delivered power is the minimum
    of two things:

      * a horizontal cap (the charger's nameplate rating), and
      * a falling curve (what the pack will accept).

    Shading the region below both makes the switchover visible: to the left of
    the intersection the charger binds and the car charges at a flat rate; to
    the right the pack binds and power falls. This is the whole explanation for
    the widely repeated claim that "high-power chargers taper earlier" -- they
    do not taper, they simply stop being the binding constraint sooner.

    Right panel is the operational consequence, measured from the session log.
    """
    from .generate import charge_step_kw, knee_soc

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.0, 4.9))

    pack_kwh = 80.0
    soc = np.linspace(0.01, 1.0, 300)
    acceptance = [charge_step_kw(s, 1e6, pack_kwh, 25.0) for s in soc]
    acc_arr = np.array(acceptance)

    # The pack's acceptance is a material property; draw it once, boldly.
    ax1.plot(soc * 100, acc_arr, lw=2.6, color="#222222", zorder=4,
             label=f"电池接受能力（{pack_kwh:.0f} kWh 包）")

    for rated, color in ((60.0, "#7F7F7F"), (120.0, "#E1A33B"),
                         (180.0, "#5DA271"), (250.0, "#C4453C")):
        cap = np.full_like(soc, rated)
        delivered = np.minimum(cap, acc_arr)
        ax1.fill_between(soc * 100, 0, delivered, color=color, alpha=0.13, zorder=1)
        ax1.plot(soc * 100, cap, lw=1.7, ls="--", color=color, alpha=0.85, zorder=2)
        ax1.plot(soc * 100, delivered, lw=2.3, color=color, zorder=3,
                 label=f"{rated:.0f} kW 桩")
        knee = knee_soc(rated, pack_kwh)
        if knee < 0.99:
            ax1.plot([knee * 100], [rated], marker="v", ms=9, color=color,
                     markeredgecolor="white", markeredgewidth=1.0, zorder=6)

    ax1.axvspan(0, 33, color="#000000", alpha=0.035, zorder=0)
    ax1.text(1.5, 253, "桩在限制\n（功率是平的）", fontsize=9.5, color="#444444",
             va="top")
    ax1.text(96, 253, "电池在限制\n（功率往下走）", fontsize=9.5, color="#444444",
             va="top", ha="right")
    ax1.text(0.985, 0.055, "虚线 = 桩的额定上限    ▼ = 交点（桩不再是瓶颈）",
             transform=ax1.transAxes, ha="right", va="bottom",
             fontsize=8.5, color="#666666")

    ax1.set_title("实际功率 = min（桩额定功率, 电池接受能力）", fontsize=11.5)
    ax1.set_xlabel("电池电量 SOC（%）")
    ax1.set_ylabel("充电功率（kW）")
    ax1.set_xlim(0, 100)
    ax1.set_ylim(0, 272)
    # Lower right: the upper right holds the "battery-limited" caption and the
    # middle right is where the 180 kW curve lives, so both are taken.
    ax1.legend(fontsize=8.5, loc="lower right", framealpha=0.95,
               borderaxespad=0.6)

    # -- right: utilisation vs arrival SOC ---------------------------------
    dc = sessions[sessions["is_dc"]].copy()
    bins = np.arange(0.0, 0.91, 0.10)
    dc["soc_bin"] = pd.cut(dc["start_soc"], bins=bins)
    grouped = dc.groupby("soc_bin", observed=True)["power_utilisation"]
    centres = [iv.mid * 100 for iv in grouped.mean().index]
    means = grouped.mean().to_numpy() * 100
    counts = grouped.size().to_numpy()

    ax2.bar(centres, means, width=8.0, color="#C4453C", alpha=0.88)
    for x, y, n in zip(centres, means, counts):
        ax2.text(x, y + 1.2, f"n={n}", ha="center", fontsize=8, color="#555555")
    overall = dc["power_utilisation"].mean() * 100
    ax2.axhline(overall, color="#333333", ls="--", lw=1.3)
    ax2.text(79, overall + 1.8, f"整体 {overall:.0f}%", fontsize=9.5,
             color="#333333", fontweight="bold", ha="right")
    ax2.set_title("到达时电量越低，桩的利用率越高\n（利用率 = 实际平均功率 / 额定功率）",
                  fontsize=11)
    ax2.set_xlabel("充电开始时的 SOC（%）")
    ax2.set_ylabel("利用率（%）")
    ax2.set_ylim(0, max(means) * 1.32 if len(means) else 100)

    fig.suptitle("快充的真实瓶颈是电池，不是充电桩", fontsize=13.5, fontweight="bold")
    fig.tight_layout(rect=(0, 0.02, 1, 0.93))
    _stamp(fig)
    return _save(fig, out / "02_charge_curve.png")


def fig_temperature(sessions: pd.DataFrame, out: Path) -> Path:
    """Cold-weather effect, kept separate for AC and DC.

    Pooling the two regimes would hide the effect entirely: AC is capped at 7 kW
    by the charger whatever the weather does.
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 4.4),
                                   gridspec_kw={"width_ratios": [1.15, 1]})

    dc = sessions[sessions["is_dc"]]
    ac = sessions[~sessions["is_dc"]]

    for data, ax, color, title in (
        (dc, ax1, "#C4453C", "直流快充：温度真的影响功率"),
        (ac, ax2, "#4C72B0", "交流慢充：功率由桩封顶"),
    ):
        ax.scatter(data["temp_c"], data["avg_power_kw"], s=9, alpha=0.30,
                   color=color, edgecolors="none")
        # Binned median overlay: the cloud alone does not show the trend.
        bins = np.arange(-10, 40, 5.0)
        cut = pd.cut(data["temp_c"], bins=bins)
        med = data.groupby(cut, observed=True)["avg_power_kw"].median()
        xs = [iv.mid for iv in med.index]
        ax.plot(xs, med.to_numpy(), color="#222222", lw=2.0,
                marker="o", ms=5, label="分箱中位数")
        ax.set_title(title, fontsize=11.5)
        ax.set_xlabel("环境温度（°C）")
        ax.set_ylabel("平均充电功率（kW）")
        ax.legend(fontsize=9)

    cold = dc[dc["temp_c"] < 0]["avg_power_kw"]
    mild = dc[dc["temp_c"].between(20, 30)]["avg_power_kw"]
    if len(cold) and len(mild):
        drop = 1.0 - cold.mean() / mild.mean()
        ax1.annotate(f"0°C 以下比 20-30°C\n低 {drop:.0%}",
                     xy=(1.0, cold.mean()), xytext=(9.0, mild.mean() * 0.62),
                     fontsize=10, color="#C4453C", fontweight="bold",
                     arrowprops=dict(arrowstyle="->", color="#C4453C", lw=1.3))

    fig.suptitle("温度对充电功率的影响", fontsize=13.5, fontweight="bold")
    fig.tight_layout(rect=(0, 0.02, 1, 0.93))
    _stamp(fig)
    return _save(fig, out / "03_temperature.png")


def fig_access(sessions: pd.DataFrame, out: Path) -> Path:
    """How not having a home charger reshapes a driver's charging profile."""
    home_users = set(sessions.loc[sessions["station_type"] == "HOME_AC", "vehicle_id"])
    s = sessions.copy()
    s["group"] = np.where(s["vehicle_id"].isin(home_users), "有私人桩", "无私人桩")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 4.4))

    # -- left: sessions per vehicle per year, by station type --------------
    per = (s.groupby(["group", "vehicle_id", "station_type"]).size()
           .rename("n").reset_index())
    days = s["date"].nunique()
    per["per_year"] = per["n"] * 365.0 / days
    agg = per.groupby(["group", "station_type"])["per_year"].mean().unstack().fillna(0.0)
    agg = agg.reindex(columns=STATION_ORDER).fillna(0.0)

    groups = ["有私人桩", "无私人桩"]
    x = np.arange(len(groups))
    width = 0.62
    bottom = np.zeros(len(groups))
    for stype in STATION_ORDER:
        vals = np.array([agg.loc[g, stype] if g in agg.index else 0.0 for g in groups])
        ax1.bar(x, vals, width, bottom=bottom,
                color=STATION_COLORS[stype], label=STATION_LABELS[stype], alpha=0.9)
        bottom += vals
    for xi, total in zip(x, bottom):
        ax1.text(xi, total + max(bottom) * 0.02, f"{total:.0f} 次/年",
                 ha="center", fontsize=9.5, fontweight="bold")
    ax1.set_xticks(x, groups)
    ax1.set_ylabel("每车每年充电次数")
    ax1.set_title("充电总量：有桩的车靠家充，没桩的车总量更少", fontsize=11.5)
    ax1.legend(fontsize=8.5, loc="upper right")
    ax1.set_ylim(0, max(bottom) * 1.18)

    # -- right: the DC share, which is the number that matters -------------
    # Computed the same way as analyze.analyse_home_access: mean DC sessions
    # per vehicle, then a ratio of those means. An earlier version annualised
    # first and took a ratio of annualised means, which gave 6x where the
    # analysis reported 8.9x -- the ratio of averages is not the average of
    # ratios, and two numbers for one finding is indefensible in a report.
    dc_rate = (per[per["station_type"] == "PUBLIC_DC"]
               .groupby(["group", "vehicle_id"])["n"].sum()
               .groupby("group").mean().reindex(groups).fillna(0.0))
    bars = ax2.bar(groups, dc_rate.to_numpy(), width=0.5,
                   color=["#4C72B0", "#C4453C"], alpha=0.9)
    for b, v in zip(bars, dc_rate.to_numpy()):
        ax2.text(b.get_x() + b.get_width() / 2, v + max(dc_rate) * 0.03,
                 f"{v:.1f} 次", ha="center", fontsize=11, fontweight="bold")
    if dc_rate.iloc[0] > 0:
        ratio = dc_rate.iloc[1] / dc_rate.iloc[0]
        ax2.set_title(f"公共快充使用频次相差 {ratio:.1f} 倍\n"
                      f"—— 由「有没有私人桩」决定，不是由里程决定", fontsize=11.5)
    ax2.set_ylabel("每车期内直流快充次数")
    ax2.set_ylim(0, max(dc_rate) * 1.25)

    fig.tight_layout()
    _stamp(fig)
    return _save(fig, out / "04_home_access.png")


def fig_occupancy(sessions: pd.DataFrame, out: Path) -> Path:
    """Connector time split into charging vs parked-and-finished."""
    by = sessions.groupby("station_type").agg(
        charge_h=("duration_min", lambda s: s.sum() / 60.0),
        idle_h=("idle_min", lambda s: s.sum() / 60.0),
    ).reindex(STATION_ORDER).fillna(0.0)

    fig, ax = plt.subplots(figsize=(9.5, 4.4))
    y = np.arange(len(by))
    ax.barh(y, by["charge_h"], color="#4C72B0", label="正在充电", alpha=0.9)
    ax.barh(y, by["idle_h"], left=by["charge_h"], color="#C4453C",
            label="充完没走（占着桩）", alpha=0.9)

    for yi, (_, row) in zip(y, by.iterrows()):
        total = row["charge_h"] + row["idle_h"]
        share = row["idle_h"] / total if total else 0.0
        ax.text(total + by.sum(axis=1).max() * 0.015, yi,
                f"{share:.0%} 是占位", va="center", fontsize=9.5)

    ax.set_yticks(y, [STATION_LABELS[t] for t in by.index])
    ax.invert_yaxis()
    ax.set_xlabel("连接器被占用的小时数（全周期累计）")
    total_idle = by["idle_h"].sum()
    total_all = by.sum(axis=1).sum()
    ax.set_title(f"连接器有 {total_idle / total_all:.0%} 的时间被「充完没走」的车占着\n"
                 f"合计 {total_idle:,.0f} 小时", fontsize=12.5, fontweight="bold")
    ax.xaxis.set_major_formatter(FuncFormatter(_thousands))
    ax.legend(fontsize=9.5, loc="lower right")
    ax.set_xlim(0, by.sum(axis=1).max() * 1.22)
    _stamp(fig)
    return _save(fig, out / "05_connector_occupancy.png")


def fig_daily_energy(daily: pd.DataFrame, sessions: pd.DataFrame, out: Path) -> Path:
    """Daily energy over the whole run, against the seasonal temperature curve."""
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    # 7-day rolling mean: the day-to-day series is too noisy to read a trend from.
    d["smooth"] = d["energy_kwh"].rolling(7, min_periods=1).mean()
    span_months = (d["date"].max() - d["date"].min()).days / 30.44

    fig, ax = plt.subplots(figsize=(10.5, 4.2))
    ax.bar(d["date"], d["energy_kwh"], width=0.85, color="#B8C4D9",
           label="每日电量")
    ax.plot(d["date"], d["smooth"], color="#C4453C", lw=2.2, label="7 日滚动均值")

    ax2 = ax.twinx()
    ax2.plot(d["date"], d["temp_mean_c"], color="#E1A33B", lw=1.6, ls="--",
             label="日平均气温")
    ax2.set_ylabel("气温（°C）", color="#B8860B")
    ax2.tick_params(axis="y", colors="#B8860B")
    ax2.grid(False)

    ax.set_ylabel("充电电量（kWh/天）")
    ax.set_xlabel("日期")
    ax.set_title(f"{span_months:.0f} 个月趋势：气温有明确季节周期，电量相对平稳",
                 fontsize=12.5, fontweight="bold")
    ax.yaxis.set_major_formatter(FuncFormatter(_thousands))

    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, fontsize=9, loc="upper left")
    fig.autofmt_xdate()
    _stamp(fig)
    return _save(fig, out / "06_seasonal_trend.png")


# ---------------------------------------------------------------------------
# Real-data figures
# ---------------------------------------------------------------------------
def fig_synthetic_vs_real(sessions: pd.DataFrame, zone_hourly: pd.DataFrame,
                          out: Path) -> Path:
    """The headline chart: the simulated daily profile against the observed one.

    Both curves are normalised to share-of-daily-energy, because the two systems
    differ by orders of magnitude in absolute size and the *shape* is the claim
    being tested.

    The disagreement is the point. The simulation peaks in the evening because
    it models private cars returning from work; Shenzhen peaks after midnight
    because the public network mostly serves commercial fleets.
    """
    synth = sessions.groupby("hour")["energy_grid_kwh"].sum()
    synth_pct = 100.0 * synth / synth.sum()

    real = zone_hourly[zone_hourly["power_implausible"] == 0].groupby("hour")["volume_kwh"].sum()
    real_pct = 100.0 * real / real.sum()

    fig, ax = plt.subplots(figsize=(10.2, 5.0))
    ax.plot(synth_pct.index, synth_pct.to_numpy(), lw=2.6, marker="o", ms=5,
            color="#4C72B0", label="仿真（私家车通勤假设）")
    ax.plot(real_pct.index, real_pct.to_numpy(), lw=2.6, marker="s", ms=5,
            color="#C4453C", label="真实观测（深圳公共充电）")

    # Annotations are placed with explicit offsets inside the axes: the two
    # peaks are close together in x but far apart in y, so a fixed "above the
    # point" rule collides with the title for the tall simulated curve.
    peaks = ((synth_pct, "#4C72B0", "仿真峰值", (11.2, 13.4), (19, synth_pct.loc[19])),
             (real_pct, "#C4453C", "真实峰值", (3.4, 8.6), (1, real_pct.loc[1])))
    for series, colour, name, text_xy, peak_xy in peaks:
        h = int(series.idxmax())
        ax.scatter([h], [series.loc[h]], s=170, facecolors="none",
                   edgecolors=colour, linewidths=2.2, zorder=5)
        ax.annotate(f"{name} {h:02d}:00（{series.loc[h]:.1f}%）",
                    xy=peak_xy, xytext=text_xy,
                    fontsize=10, color=colour, fontweight="bold",
                    arrowprops=dict(arrowstyle="->", color=colour, lw=1.4,
                                    connectionstyle="arc3,rad=-0.2"))

    ax.set_xlabel("一天中的小时")
    ax.set_ylabel("占全天电量的比例（%）")
    ax.set_title("仿真预测傍晚高峰，真实数据是凌晨高峰\n"
                 "—— 同一个城市，两套完全不同的负荷时间结构",
                 fontsize=12.5, fontweight="bold", pad=14)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlim(-0.5, 23.5)
    ax.set_ylim(0, 17.5)
    ax.legend(fontsize=10, loc="upper left")
    ax.axvspan(0, 6, color="#C4453C", alpha=0.05, zorder=0)
    # Placed in the empty band between the simulated curve (~1.5% by night) and
    # the observed one (~6%), rather than at the bottom where the curves are.
    ax.text(3.2, 3.1, "真实数据：夜间 00-06 点占 40%",
            ha="center", fontsize=9.5, color="#C4453C")

    fig.subplots_adjust(top=0.84, bottom=0.13, left=0.09, right=0.98)
    # Both datasets appear here, so the caveat names both rather than claiming
    # the whole figure is synthetic.
    _stamp(fig, "曲线：仿真（模型） vs 真实观测（UrbanEV 深圳）· 两者刻度为占比")
    return _save(fig, out / "07_synthetic_vs_real.png")


def fig_real_daily(zone_hourly: pd.DataFrame, out: Path) -> Path:
    """Observed load and observed utilisation on the same hourly axis.

    Two different questions, deliberately paired: "how much energy" and "how
    full is the network" are not the same curve, and the gap between them is
    what a tariff or an expansion plan has to work with.
    """
    ok = zone_hourly[zone_hourly["power_implausible"] == 0]
    days = max(1, ok["timestamp"].dt.normalize().nunique())

    by_hour = ok.groupby("hour").agg(
        volume=("volume_kwh", "sum"),
        occ=("occupancy", "sum"),
        piles=("n_piles", "sum"),
    )
    by_hour["kwh_per_day"] = by_hour["volume"] / days
    by_hour["util"] = 100.0 * by_hour["occ"] / by_hour["piles"]

    fig, ax = plt.subplots(figsize=(10.0, 4.8))
    ax.bar(by_hour.index, by_hour["kwh_per_day"], width=0.72,
           color="#C4453C", alpha=0.85, label="充电电量（kWh/日）")
    ax.set_xlabel("一天中的小时")
    ax.set_ylabel("充电电量（kWh/日）", color="#C4453C")
    ax.tick_params(axis="y", colors="#C4453C")
    ax.yaxis.set_major_formatter(FuncFormatter(_thousands))

    ax2 = ax.twinx()
    ax2.plot(by_hour.index, by_hour["util"], color="#222222", lw=2.4,
             marker="o", ms=5, label="桩利用率（%）")
    ax2.set_ylabel("桩利用率（%）", color="#222222")
    ax2.set_ylim(0, max(by_hour["util"].max() * 1.35, 40))
    ax2.grid(False)

    peak = int(by_hour["kwh_per_day"].idxmax())
    ax.set_title(f"深圳公共充电：电量峰值 {peak:02d}:00，日均 "
                 f"{by_hour['kwh_per_day'].sum():,.0f} kWh",
                 fontsize=12.5, fontweight="bold")
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlim(-0.7, 23.7)

    handles1, labels1 = ax.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(handles1 + handles2, labels1 + labels2, fontsize=9.5, loc="upper right")

    fig.tight_layout()
    _stamp(fig, REAL_CAVEAT)
    return _save(fig, out / "08_real_daily_profile.png")


def fig_real_spatial(zone_hourly: pd.DataFrame, out: Path) -> Path:
    """How concentrated demand is across zones -- the basis for siting decisions."""
    by_zone = (zone_hourly.groupby("zone_id")["volume_kwh"].sum()
               .sort_values(ascending=False))
    cumulative = 100.0 * by_zone.cumsum() / by_zone.sum()
    ranks = np.arange(1, len(by_zone) + 1)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.4, 4.5))

    ax1.plot(ranks, cumulative.to_numpy(), lw=2.4, color="#C4453C")
    for k, colour in ((10, "#333333"), (50, "#7F7F7F")):
        ax1.axvline(k, color=colour, ls="--", lw=1.1)
        ax1.scatter([k], [cumulative.iloc[k - 1]], s=60, color=colour, zorder=5)
        ax1.annotate(f"前 {k} 个：{cumulative.iloc[k - 1]:.0f}%",
                     xy=(k, cumulative.iloc[k - 1]),
                     xytext=(k + 12, cumulative.iloc[k - 1] - 12),
                     fontsize=9.5, color=colour)
    ax1.set_xlabel("小区排名（按电量）")
    ax1.set_ylabel("累计电量占比（%）")
    ax1.set_title(f"{len(by_zone)} 个交通小区：电量高度集中",
                  fontsize=11.5)
    ax1.set_ylim(0, 102)
    ax1.set_xlim(0, len(by_zone))

    top = by_zone.head(20)
    ax2.barh(np.arange(len(top)), (top / 1e6).to_numpy(), color="#4C72B0", alpha=0.9)
    ax2.set_yticks(np.arange(len(top)), [f"Z{z}" for z in top.index], fontsize=7.5)
    ax2.invert_yaxis()
    ax2.set_xlabel("期内总电量（GWh）")
    ax2.set_title("前 20 个小区", fontsize=11.5)

    fig.suptitle("选址该看分布，不该看平均值", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0.02, 1, 0.93))
    _stamp(fig, REAL_CAVEAT)
    return _save(fig, out / "09_real_spatial.png")


def generate_real(data_dir: Path, synthetic_dir: Path,
                  out_dir: Path, dpi: int = 110) -> list[Path]:
    """Build every real-data figure that the available inputs allow."""
    from . import real_data as rd

    font = configure_fonts()
    print(f"using CJK font: {font}")
    plt.rcParams["figure.dpi"] = dpi

    zone_hourly = rd.load_zone_hourly(data_dir)
    paths: list[Path] = []

    sessions_path = synthetic_dir / "sessions.csv"
    if sessions_path.exists():
        sessions, _stations, _daily = load(synthetic_dir)
        paths.append(fig_synthetic_vs_real(sessions, zone_hourly, out_dir))
    else:
        print(f"  (skipping synthetic-vs-real: {sessions_path} not found)")

    paths.append(fig_real_daily(zone_hourly, out_dir))
    paths.append(fig_real_spatial(zone_hourly, out_dir))
    return paths


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def generate_all(data_dir: Path, out_dir: Path, dpi: int = 110) -> list[Path]:
    font = configure_fonts()
    print(f"using CJK font: {font}")
    plt.rcParams["figure.dpi"] = dpi

    sessions, _stations, daily = load(data_dir)
    paths = [
        fig_hourly_load(sessions, out_dir),
        fig_charge_curve(sessions, out_dir),
        fig_temperature(sessions, out_dir),
        fig_access(sessions, out_dir),
        fig_occupancy(sessions, out_dir),
        fig_daily_energy(daily, sessions, out_dir),
    ]
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate charts")
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--real-data", type=Path, default=Path("data/real/urbanev"),
                        help="UrbanEV directory (used with --real)")
    parser.add_argument("--out", type=Path, default=Path("figures"))
    parser.add_argument("--dpi", type=int, default=110)
    parser.add_argument("--real", action="store_true",
                        help="render figures from the real dataset instead")
    args = parser.parse_args(argv)

    paths = (generate_real(args.real_data, args.data, args.out, args.dpi)
             if args.real else generate_all(args.data, args.out, args.dpi))
    for p in paths:
        print(f"  wrote {p}  ({p.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
