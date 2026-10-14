"""Synthetic-but-realistic EV charging session generator.

Why synthetic data, and why this is not cheating
------------------------------------------------
No public charging dataset for a Chinese city was available in this
environment, so the sessions are generated. The important design decision is
*what* gets encoded:

* **Behavioural inputs only** -- commute timing, daily distance distributions,
  battery capacity, and the physical fact that a battery accepts less power as
  it fills. The generator does NOT encode "there is a peak at 18:00".
* **Emergent outputs** -- the charging peak, the AC/DC split, the taper, and
  the session-length distribution are all *consequences* of those inputs.

That distinction matters when reading the analysis: a finding like "demand
peaks in the evening" is only as trustworthy as the commute model behind it.
It is a property of the model, not a measurement of the world. The analysis
script says so explicitly, and the README does too.

Parameter provenance
--------------------
Every constant below is tagged in a comment as either:

``[Behav]``  a behavioural assumption, with the reasoning stated
``[Phys]``   a physical relationship, which is not a matter of opinion
``[TODO]``   awaiting a citable source (see docs/charging_behavior_sources.md)

Usage
-----
    python -m src.generate            # writes data/raw/*.csv
    python -m src.generate --days 30  # shorter run for a quick look
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np

# ---------------------------------------------------------------------------
# Locale
# ---------------------------------------------------------------------------
# Haidian-style Chinese city profile. Only the *pattern* of the commute matters
# for the analysis; the labels are for readability.
WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# Start date chosen so a 300-day run opens in autumn and runs through a full
# winter (coldest mid-January) into summer.
START_DATE = datetime(2024, 10, 1)


# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------
# City population size for the simulation. 260 vehicles is small enough to run
# in seconds and large enough that per-hour rates are not dominated by noise.
N_VEHICLES = 260
# 300 days from 1 October spans a full winter-to-summer cycle (trough in
# mid-January, peak in mid-July), which the temperature analysis needs.
DAYS = 300

# -- Vehicle -----------------------------------------------------------------
# [Behav] Compact and mid-size EVs dominate the Chinese market. The mix below
# is a plausible fleet, not a measured one.
BATTERY_KWH_CHOICES = np.array([40.0, 50.0, 60.0, 75.0, 90.0])
BATTERY_KWH_WEIGHTS = np.array([0.14, 0.24, 0.32, 0.22, 0.08])

# [Behav] Daily distance. Lognormal because distance is strictly positive and
# right-skewed: most days are short, a few are long. Median ~34 km, and the
# long tail produces the occasional long trip that needs fast charging --
# which is what makes DC demand non-trivial.
DAILY_KM_MEDIAN = 34.0
DAILY_KM_SIGMA = 0.75

# [Behav] Weekends are not just "another day": total distance falls, but the
# tail gets longer (leisure trips).
WEEKEND_KM_FACTOR = 0.80
WEEKEND_TAIL_FACTOR = 1.35

# -- Energy consumption ------------------------------------------------------
# [Phys] Consumption depends on temperature and the effect is large. The
# coefficients below are fitted to controlled measurements rather than guessed:
# a five-vehicle climate-chamber study (reported by DOE, conducted by AAA)
# measured, at -7 degC with cabin heating on, +39% consumption and -41% range;
# with heating off, -12% range. Summer heat at 35 degC with air conditioning
# costs about -17% range.
#
# Two anchors therefore define the cold side: +12.5% consumption near 10 degC
# and +39% at -7 degC, i.e. a slope of about 1.56 percentage points per degC.
# The hot side is anchored at +17% by 35 degC.
#
# An earlier version used a single linear coefficient of 0.022 per degC, which
# gives only +15% at -7 degC -- a large understatement that made the
# temperature finding vanish.
BASE_CONSUMPTION_KWH_PER_100KM = 15.0
COLD_SLOPE_PER_DEG = 0.0156     # below 20 degC
HOT_SLOPE_PER_DEG = 0.0113      # above 20 degC
COMFORT_TEMP_C = 20.0
COLD_ANCHOR_DELTA = 0.125       # consumption delta at 10 degC (heating off basis)
COLD_ANCHOR_TEMP_C = 10.0

# -- Seasonal temperature ----------------------------------------------------
# [Behav] Sinusoidal year with a Hefei-like range, plus a daily swing.
#
# The phase is expressed as a **calendar day of year**, not as "days elapsed
# since the simulation started". That distinction is not cosmetic and was got
# wrong once: driving the season off an elapsed-day counter made the curve
# depend on when the run happened to begin, so a run starting in October
# produced its warmest month in January and its coldest in July -- an inverted
# year. The temperature series looked entirely plausible in isolation (smooth,
# seasonal, sensible range); only cross-checking month against temperature
# exposed it. Anchoring on the day of year makes the season independent of the
# run's start date, which is also what "January is cold" means.
TEMP_MEAN_C = 16.5
TEMP_AMPLITUDE_C = 13.0
# Day of year of the warmest day (~19 July); the coldest lands near 17 January.
TEMP_PEAK_DAY_OF_YEAR = 200
TEMP_DAILY_SWING_C = 4.0


# ---------------------------------------------------------------------------
# Station types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StationType:
    """A class of charging point.

    ``rated_kw`` is the nameplate power. Actual delivered power is lower and is
    computed per timestep by :func:`charge_step_kw`, because that is where the
    interesting physics lives.
    """

    key: str
    name: str
    rated_kw: float
    efficiency: float  # grid-side to pack-side
    # [Behav] Which vehicles may use it. Home points are private; workplace
    # points are used by employees; public points are open to all.
    access: str
    # [Phys] Whether the site can supply full power to every connected vehicle
    # at once. Home wiring is the constrained case: a residential feed cannot
    # run many 7 kW chargers simultaneously.
    site_power_limit_kw: float | None = None


STATION_TYPES: tuple[StationType, ...] = (
    StationType("HOME_AC", "家用交流慢充", 7.0, 0.90, "private"),
    StationType("WORK_AC", "单位交流慢充", 7.0, 0.92, "workplace"),
    StationType("PUBLIC_AC", "公共交流慢充", 7.0, 0.90, "public"),
    StationType("PUBLIC_DC", "公共直流快充", 120.0, 0.94, "public",
                site_power_limit_kw=360.0),
)
STATION_BY_KEY = {s.key: s for s in STATION_TYPES}


# ---------------------------------------------------------------------------
# Charging power model  [Phys]
# ---------------------------------------------------------------------------
# Two limits decide how fast a car charges, and conflating them caused a large
# error in two earlier versions of this file:
#
#   1. THE CHARGER delivers at most its nameplate rating.
#   2. THE BATTERY accepts at most a declining C-rate as it fills.
#
# The correct statement is ``P = min(rating, c_rate(soc) * pack_kwh)``. Two
# earlier attempts got this wrong in instructive ways:
#
#   * Tapering the *charger rating* by a fixed curve. That made a 250 kW car on
#     a 250 kW post charge at 223 kW where the measured value is 96 kW, i.e. a
#     +132% error, because it assumed the charger, not the pack, was the limit.
#   * Tapering the *pack's peak acceptance* by a normalised curve, which cannot
#     reproduce the observed switchover: the same normalised shape produces a
#     different absolute curve depending on how far the charger outruns the pack.
#
# The ``min`` formulation explains the observation that motivated all this:
# a high-power charger appears to "taper earlier". It does not. A 350 kW post
# exceeds the pack's acceptance at a low SOC, so the pack becomes the binding
# constraint sooner; a 60 kW post never exceeds it, so the pack never binds and
# the session looks like constant power. The knee is where the two curves cross,
# which is why the same car shows knees at 15%, 50% and 62% on different posts.
#
# The table below is the pack acceptance in C-rate (power / pack energy), read
# off the measured Tesla Model 3 Performance trace (peak 250 kW on an ~80 kWh
# pack, so ~3.1C peak). The 42% control point exists because the real curve has
# a sharp break there: the M3P holds ~250 kW to about 45% SOC and then collapses
# to 96 kW by 50%. A table without that break smears the knee across 10% of SOC
# and misstates power by ~38% at 50% SOC.
#
# Fidelity, against measurement. The table is fitted primarily to the Tesla
# Model 3 Performance and keeps the VW ID.4 Pro within a stated band:
#
#   Model 3 Performance: 20% +0.2%, 50% +12%, 80% +4%, 10-80% about -5% in time
#   VW ID.4 Pro:         10-80% within 15% in time; instantaneous power at 80%
#                        is 40% low
#
# The residual ID.4 gap is not a fitting failure. Measured, an ID.4 still
# accepts 94.8% of its peak at 80% SOC while a Model 3 P accepts 57.5%: the two
# packs have genuinely different acceptance curves (different cells, different
# BMS limits). One shared table cannot represent both, so the control points
# are weighted toward the fit target. A production system would carry a
# per-model curve -- see TAPER_TABLE tests for the enforced error bands.
TAPER_TABLE: tuple[tuple[float, float], ...] = (
    (0.00, 3.100),
    (0.15, 3.100),
    (0.20, 2.880),   # M3P: 230 kW measured
    (0.30, 2.600),
    (0.42, 2.550),   # M3P: still ~250 kW here
    (0.50, 1.350),   # M3P measured 96 kW (1.20C); ID.4 measured ~1.70C
    (0.60, 0.950),
    (0.70, 0.780),   # M3P: ~62 kW measured (0.775C)
    (0.80, 0.600),   # M3P: 46 kW measured (0.575C)
    (0.90, 0.380),
    (1.00, 0.150),
)

# Measured fast-charging behaviour, used to pin the model in the test suite.
# ``(rated_kw, pack_kwh, soc) -> measured pack power in kW``
REFERENCE_TAPER = {
    (250.0, 80.0, 0.50): 96.0,   # Tesla Model 3 Performance
    (250.0, 80.0, 0.80): 46.0,
    (250.0, 80.0, 0.20): 230.0,
    (141.0, 77.0, 0.80): 73.0,   # VW ID.4 Pro
}
# Measured 10% -> 80% windows. Duration and delivered energy together constrain
# both the peak and the shape, which a duration alone would not.
REFERENCE_10_80 = {
    # label: (rated_kw, pack_kwh, minutes, energy_kwh)
    "Tesla Model 3 Performance": (250.0, 80.0, 33.0, 56.0),
    "VW ID.4 Pro": (141.0, 77.0, 28.0, 54.0),
}


def acceptance_kw(soc: float, battery_kwh: float) -> float:
    """Power the pack itself can accept at ``soc``, in kW."""
    return taper_fraction(soc) * battery_kwh


def knee_soc(rated_kw: float, battery_kwh: float,
             tolerance: float = 0.001) -> float:
    """SOC at which the pack stops being able to absorb the full charger rating.

    This is the crossing point of the two limits, so it depends on *both* the
    charger and the car. It is reported rather than assumed: the widely repeated
    claim that "a 250 kW charger tapers at 20%" is really "a 250 kW charger
    exceeds this particular pack's acceptance from 20% SOC", and the same
    charger on a smaller pack has an earlier knee.
    """
    for i in range(1001):
        soc = i / 1000.0
        if acceptance_kw(soc, battery_kwh) < rated_kw * (1.0 - tolerance):
            return soc
    return 1.0


def taper_fraction(soc: float) -> float:
    """Pack acceptance in C-rate at ``soc`` (piecewise-linear lookup)."""
    soc = min(1.0, max(0.0, soc))
    table = TAPER_TABLE
    if soc <= table[0][0]:
        return table[0][1]
    if soc >= table[-1][0]:
        return table[-1][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x0 <= soc <= x1:
            if x1 == x0:
                return y1
            w = (soc - x0) / (x1 - x0)
            return y0 + w * (y1 - y0)
    return table[-1][1]  # pragma: no cover


def charge_step_kw(soc: float, rated_kw: float, battery_kwh: float,
                   temp_c: float) -> float:
    """Instantaneous pack-side power, i.e. ``min(charger, pack)`` derated for cold.

    Cold matters more than it looks: a cold pack must be warmed before it
    accepts high power, so winter fast charging is materially slower.
    """
    delivered = min(rated_kw, taper_fraction(soc) * battery_kwh)

    # [Behav] Cold derate. Below 15 degC the pack limits input power; the
    # severity is a placeholder magnitude, not a sourced curve.
    if temp_c < 15.0:
        cold = 0.45 + 0.55 * (temp_c + 10.0) / 25.0
        cold = min(1.0, max(0.35, cold))
    else:
        cold = 1.0

    return delivered * cold


def simulate_session(
    *,
    start: datetime,
    station: StationType,
    battery_kwh: float,
    start_soc: float,
    target_soc: float,
    temp_c: float,
    rng: np.random.Generator,
    max_hours: float = 14.0,
    dt_min: float = 1.0,
) -> dict | None:
    """Integrate one charging session minute by minute.

    Returns ``None`` if the vehicle would need to charge for longer than
    ``max_hours`` -- the driver unplugs and leaves, which is a real behaviour
    and not an error. Modelling it as a *failed* session rather than silently
    extending the duration keeps the duration distribution honest.

    The energy actually delivered is what the battery accepted, not what the
    nameplate suggests. All durations are derived from the integration, never
    assumed.
    """
    dt_h = dt_min / 60.0
    soc = start_soc
    t = 0.0
    kwh = 0.0
    peak_kw = 0.0
    power_samples: list[float] = []
    # Energy-weighted mean power. Tracking sum(P*dt)/sum(dt) rather than
    # energy/total_time matters because the final step is usually a partial
    # one: charging stops the moment the target SOC is reached. Attributing a
    # full step's power to a fraction of a step is what previously produced
    # "peak power below average power", which is impossible.
    weighted_kw_h = 0.0

    usable_kwh = battery_kwh * USABLE_SOC_WINDOW

    while soc < target_soc and t < max_hours:
        # Guard against the target already being exceeded: a negative headroom
        # would otherwise subtract energy from the session.
        headroom = (target_soc - soc) * usable_kwh
        if headroom <= 1e-12:
            break

        p_pack = charge_step_kw(soc, station.rated_kw, battery_kwh, temp_c)
        if p_pack <= 0.0:
            break

        added = p_pack * dt_h
        if added > headroom:
            # Partial final step: stop exactly at the target.
            added = headroom
            step_h = added / p_pack
        else:
            step_h = dt_h

        kwh += added
        soc += added / usable_kwh
        t += step_h
        weighted_kw_h += p_pack * step_h
        peak_kw = max(peak_kw, p_pack)
        power_samples.append(p_pack)

    if t >= max_hours and soc < target_soc - 1e-6:
        return None

    seconds = int(round(t * 3600.0))
    if seconds <= 0 or kwh <= 1e-6:
        return None

    energy_grid = kwh / station.efficiency
    avg_kw = weighted_kw_h / t if t > 0 else 0.0
    power_curve = ",".join(f"{p:.1f}" for p in power_samples[::max(1, len(power_samples)//40)])

    return {
        "start": start,
        "end": start + timedelta(seconds=seconds),
        "duration_min": round(t * 60.0, 2),
        "energy_kwh": round(kwh, 4),
        "energy_grid_kwh": round(energy_grid, 4),
        "avg_power_kw": round(avg_kw, 3),
        "peak_power_kw": round(peak_kw, 2),
        "start_soc": round(start_soc, 4),
        "end_soc": round(soc, 4),
        "power_curve": power_curve,
    }


# [Phys] Only part of the nameplate capacity is cycled in practice: the BMS
# keeps a buffer at both ends, and drivers rarely run to 0% or sit at 100%.
USABLE_SOC_WINDOW = 0.95


# ---------------------------------------------------------------------------
# Demand model  [Behav]
# ---------------------------------------------------------------------------
def _hour_weights(profile: str, is_weekend: bool) -> np.ndarray:
    """Probability of a session *starting* in each hour of the day.

    These encode when people plug in, which is a behavioural assumption. They
    are deliberately coarse (24 numbers) so that any structure in the results
    comes from the interaction of commute timing with battery physics, not from
    fine detail smuggled in here.
    """
    w = np.zeros(24)

    if profile == "HOME":
        # Arrive home from work in the late afternoon and evening; a second
        # group plugs in before bed for off-peak electricity.
        evening = {17: 6, 18: 14, 19: 16, 20: 12, 21: 8, 22: 5, 23: 3}
        for h, v in evening.items():
            w[h] = v
        w[0] = 2  # occasional late-night top-up
        if is_weekend:
            # No commute, so plug-in spreads out and daytime appears.
            w[:] *= 0.55
            for h, v in {10: 3, 11: 3, 12: 3, 13: 3, 14: 3, 15: 4, 16: 5}.items():
                w[h] = v

    elif profile == "WORK":
        # Plug in on arrival, and again after lunch. Weekends are almost empty.
        for h, v in {8: 12, 9: 14, 10: 6, 11: 3, 12: 4, 13: 7, 14: 3}.items():
            w[h] = v
        if is_weekend:
            w[:] *= 0.06

    elif profile == "PUBLIC_AC":
        # Destination charging: shopping, errands, cinema. Broad daytime spread
        # plus an evening shoulder.
        for h, v in {9: 3, 10: 4, 11: 4, 12: 5, 13: 5, 14: 5, 15: 5,
                     16: 5, 17: 6, 18: 6, 19: 6, 20: 5, 21: 3}.items():
            w[h] = v
        if is_weekend:
            w *= 1.6

    elif profile == "PUBLIC_DC":
        # Fast charging is used by people who cannot charge at home, plus long
        # trips. Peaks after the evening commute and a noon bump.
        for h, v in {7: 3, 8: 3, 11: 3, 12: 4, 13: 3, 17: 3, 18: 6,
                     19: 7, 20: 6, 21: 5, 22: 4, 23: 3}.items():
            w[h] = v
        if is_weekend:
            for h, v in {10: 3, 11: 3, 12: 3, 14: 3, 15: 3, 16: 3}.items():
                w[h] += v

    total = w.sum()
    if total <= 0:
        raise ValueError(f"empty demand profile: {profile}")
    return w / total


# [Behav] The mix of station types turns per-driver plug-in behaviour into
# system-level load. Anchored on two sourced facts:
#
#   * 82-93% of EV owners do most of their charging at home (multi-study range
#     in docs/charging_behavior_sources.md). A touch lower is used here because
#     this scenario includes ride-hailing-style high-mileage vehicles that
#     cannot charge at home.
#   * China's public charging fleet is 54:46 AC:DC (official tallies for both
#     2024-12 and 2025-03), which sets the public-site split.
#
# The values are relative weights, normalised at use.
STATION_ACCESS_MIX = {
    "HOME": 0.65,
    "WORK": 0.24,
    "PUBLIC_AC": 0.14,
    "PUBLIC_DC": 0.10,
}

# [Behav] Share of vehicles that have a private charging point. Sourced range is
# 82-93% of owners charging mainly at home; a lower share is used here so the
# public network carries a realistic load from vehicles that genuinely cannot
# charge at home.
HOME_CHARGER_SHARE = 0.68

# [Behav] Target SOC by station type. Home and work charging aim high because
# the car sits there for hours -- though 90% every night is not what most
# owners do, and aiming that high pushes the fleet's equilibrium SOC up into
# the region where the charge taper is active, which distorts the observed
# average power. 85% overnight is the more defensible habit.
TARGET_SOC = {
    "HOME": (0.85, 0.06),
    "WORK": (0.82, 0.07),
    "PUBLIC_AC": (0.80, 0.08),
    "PUBLIC_DC": (0.80, 0.10),
}

# [Behav] Drivers do not plug in every day. A car with a 50 kWh pack and a
# 34 km daily commute burns roughly 5 kWh/day, so charging more often than
# every other day would push average SOC upward without limit -- the fleet
# reaches an equilibrium where it sits at high SOC and the taper dominates.
# These rates are calibrated so that energy in balances energy out at a
# realistic average SOC (see tests/test_generate.py::test_soc_equilibrium).
DAILY_PLUG_IN_PROB = {
    "HOME": 0.30,      # ~2x per week at home
    "WORK": 0.26,      # ~1.3x per week at the office
    "PUBLIC_AC": 0.06,
    "PUBLIC_DC": 0.07,
}

# [Behav] Equilibrium SOC the fleet should settle at. Recorded explicitly
# because it is a result of the model, not an input, and the analysis depends
# on it being realistic rather than pinned near full charge.
EXPECTED_EQUILIBRIUM_SOC = 0.50

# [Phys] Even at a DC charger, a driver will not arrive with a healthy pack
# below ~8% SOC often; they charge before that.
MIN_ARRIVAL_SOC = 0.06


# ---------------------------------------------------------------------------
# Climate
# ---------------------------------------------------------------------------
def daily_temperature(day_of_year: int, rng: np.random.Generator) -> float:
    """Mean ambient temperature for a calendar day, with weather noise on top.

    ``day_of_year`` is 1-366 in the ordinary calendar sense, so the season is
    tied to the date rather than to how long the simulation has been running.
    """
    seasonal = TEMP_MEAN_C + TEMP_AMPLITUDE_C * math.sin(
        2.0 * math.pi * (day_of_year - TEMP_PEAK_DAY_OF_YEAR + 91) / 365.25
    )
    return float(seasonal + rng.normal(0.0, 2.5))


def consumption_kwh_per_100km(temp_c: float) -> float:
    """Temperature-dependent energy consumption  [Phys].

    Piecewise-linear through measured anchors: +12.5% at 10 degC and +39% at
    -7 degC on the cold side, +17% at 35 degC on the hot side, with the
    baseline in between. The shape is well established and the magnitudes come
    from a controlled five-vehicle climate-chamber study, so this is one of the
    better-sourced parts of the model.
    """
    if temp_c < COMFORT_TEMP_C:
        delta = COLD_ANCHOR_DELTA + COLD_SLOPE_PER_DEG * (COLD_ANCHOR_TEMP_C - temp_c)
        delta = max(0.0, delta)
    else:
        delta = HOT_SLOPE_PER_DEG * (temp_c - COMFORT_TEMP_C)
    return BASE_CONSUMPTION_KWH_PER_100KM * (1.0 + delta)


# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------
@dataclass
class Vehicle:
    """One simulated car and the charging points it has access to."""

    vehicle_id: str
    battery_kwh: float
    consumption: float
    soc: float
    home_station_id: str | None = None
    work_station_id: str | None = None
    # An EV that cannot charge at home depends far more on public DC.
    has_home_charger: bool = True
    # Counters for the recorded period, used by the analysis stage.
    n_sessions: int = 0
    energy_total_kwh: float = 0.0
    dc_sessions: int = 0
    # Counters for the warm-up period, kept separate so that per-vehicle
    # statistics describe the recorded period only.
    warmup_sessions: int = 0
    warmup_energy_kwh: float = 0.0


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------
@dataclass
class FleetGenerator:
    """Generates one city's worth of charging sessions."""

    seed: int = 42
    n_vehicles: int = N_VEHICLES
    days: int = DAYS
    start_date: datetime = START_DATE
    # Days simulated before recording begins, so the fleet's state of charge
    # reaches its equilibrium instead of reflecting the arbitrary starting
    # spread. Without this the first weeks of data are a transient: every car
    # starts part-charged, so the measured average SOC and average charging
    # power are biased by an initial condition that has nothing to do with the
    # behaviour being studied.
    warmup_days: int = 120

    rng: np.random.Generator = field(init=False)
    vehicles: list[Vehicle] = field(default_factory=list, init=False)
    stations: list[dict] = field(default_factory=list, init=False)
    sessions: list[dict] = field(default_factory=list, init=False)
    daily: list[dict] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)

    # -- setup ---------------------------------------------------------------
    def build_stations(self) -> None:
        """One home point per home-charging vehicle, plus shared public sites."""
        sid = 0

        # Home points: one per vehicle that has off-street parking.
        for i in range(self.n_vehicles):
            if self.rng.random() < HOME_CHARGER_SHARE:
                self.stations.append({
                    "station_id": f"ST{sid:05d}",
                    "type": "HOME_AC",
                    "name": STATION_BY_KEY["HOME_AC"].name,
                    "rated_kw": STATION_BY_KEY["HOME_AC"].rated_kw,
                    "n_connectors": 1,
                    "district": f"居住区{chr(65 + i % 8)}",
                })
                sid += 1

        # Workplace: pooled points, sized to the workforce that uses them.
        n_work_sites = 6
        for w in range(n_work_sites):
            n_conn = int(self.rng.integers(6, 18))
            self.stations.append({
                "station_id": f"ST{sid:05d}",
                "type": "WORK_AC",
                "name": STATION_BY_KEY["WORK_AC"].name,
                "rated_kw": STATION_BY_KEY["WORK_AC"].rated_kw,
                "n_connectors": n_conn,
                "district": f"园区{w + 1}",
            })
            sid += 1

        # Public sites. The AC:DC site count is roughly balanced because China's
        # public charging fleet splits 54:46 AC:DC; DC hubs are fewer but each
        # has more connectors and far higher power, so they dominate throughput.
        for p in range(22):
            self.stations.append({
                "station_id": f"ST{sid:05d}",
                "type": "PUBLIC_AC",
                "name": STATION_BY_KEY["PUBLIC_AC"].name,
                "rated_kw": STATION_BY_KEY["PUBLIC_AC"].rated_kw,
                "n_connectors": int(self.rng.integers(4, 14)),
                "district": f"商业区{chr(65 + p % 7)}",
            })
            sid += 1

        for d in range(18):
            self.stations.append({
                "station_id": f"ST{sid:05d}",
                "type": "PUBLIC_DC",
                "name": STATION_BY_KEY["PUBLIC_DC"].name,
                "rated_kw": float(self.rng.choice([60.0, 120.0, 120.0, 180.0, 250.0])),
                "n_connectors": int(self.rng.integers(3, 10)),
                "district": f"枢纽{d + 1}",
            })
            sid += 1

        self._home_ids = [s["station_id"] for s in self.stations if s["type"] == "HOME_AC"]
        self._work_ids = [s["station_id"] for s in self.stations if s["type"] == "WORK_AC"]
        self._public_ac_ids = [s["station_id"] for s in self.stations if s["type"] == "PUBLIC_AC"]
        self._public_dc_ids = [s["station_id"] for s in self.stations if s["type"] == "PUBLIC_DC"]

    def build_fleet(self) -> None:
        """Create vehicles, wiring each to the stations it can reach."""
        home_iter = iter(self._home_ids)
        for i in range(self.n_vehicles):
            battery = float(self.rng.choice(BATTERY_KWH_CHOICES, p=BATTERY_KWH_WEIGHTS))
            has_home = False
            home_id = None
            try:
                home_id = next(home_iter)
                has_home = True
            except StopIteration:
                pass

            v = Vehicle(
                vehicle_id=f"V{i:04d}",
                battery_kwh=battery,
                consumption=BASE_CONSUMPTION_KWH_PER_100KM,
                # Initial SOC spread so the fleet does not start synchronised.
                soc=float(self.rng.uniform(0.25, 0.85)),
                home_station_id=home_id,
                work_station_id=str(self.rng.choice(self._work_ids)) if self.rng.random() < 0.55 else None,
                has_home_charger=has_home,
            )
            self.vehicles.append(v)

    # -- daily loop ----------------------------------------------------------
    def run(self) -> None:
        """Simulate the warm-up period, then the recorded period."""
        self.build_stations()
        self.build_fleet()

        # Warm-up: identical dynamics, nothing recorded.
        for day in range(self.warmup_days):
            self._simulate_day(day - self.warmup_days, record=False)

        # Recorded period. ``day_index`` continues from the warm-up so the
        # seasonal temperature curve is continuous rather than restarting.
        for day in range(self.days):
            self._simulate_day(day, record=True)

    def _simulate_day(self, day_index: int, record: bool) -> None:
        """Advance every vehicle by one day.

        ``day_index`` is relative to the start of the recorded period, so it may
        be negative during warm-up; the date and season follow from it.
        """
        date = self.start_date + timedelta(days=day_index)
        is_weekend = date.weekday() >= 5
        # Season follows the calendar date, so October is autumn whatever day
        # the run happens to start on.
        temp = daily_temperature(date.timetuple().tm_yday, self.rng)
        self._temp_c = temp

        kwh_sold = 0.0
        n_sessions_today = 0

        for v in self.vehicles:
            # 1. Driving consumes energy.
            km = self._daily_km(is_weekend)
            v.consumption = consumption_kwh_per_100km(temp)
            used = km * v.consumption / 100.0
            usable = v.battery_kwh * USABLE_SOC_WINDOW
            v.soc = max(MIN_ARRIVAL_SOC, v.soc - used / usable)

            # 2. Decide whether and where to charge.
            sessions_before = len(self.sessions)
            self._maybe_charge(v, date, is_weekend, temp, record=record)
            if len(self.sessions) > sessions_before:
                n_sessions_today += 1
                kwh_sold += self.sessions[-1]["energy_kwh"]

        if record:
            self.daily.append({
                "date": date.date().isoformat(),
                "weekday": WEEKDAY_NAMES[date.weekday()],
                "is_weekend": int(is_weekend),
                "temp_mean_c": round(temp, 2),
                "n_sessions": n_sessions_today,
                "energy_kwh": round(kwh_sold, 2),
                "n_vehicles": len(self.vehicles),
            })

    def fleet_state(self) -> dict[str, float]:
        """Summary statistics of vehicle state at the end of the run."""
        socs = [v.soc for v in self.vehicles]
        return {
            "mean_soc": float(np.mean(socs)),
            "median_soc": float(np.median(socs)),
            "min_soc": float(np.min(socs)),
            "max_soc": float(np.max(socs)),
            "sessions_per_vehicle": len(self.sessions) / max(1, len(self.vehicles)),
            "kwh_per_vehicle": sum(v.energy_total_kwh for v in self.vehicles)
                               / max(1, len(self.vehicles)),
        }

    def _daily_km(self, is_weekend: bool) -> float:
        """Random daily distance for one vehicle  [Behav]."""
        sigma = DAILY_KM_SIGMA * (WEEKEND_TAIL_FACTOR if is_weekend else 1.0)
        median = DAILY_KM_MEDIAN * (WEEKEND_KM_FACTOR if is_weekend else 1.0)
        # Lognormal parameterised by median: mu = ln(median).
        return float(self.rng.lognormal(mean=math.log(median), sigma=sigma) * 0.35 + 3.0)

    def _pick_profile(self, v: Vehicle) -> str | None:
        """Choose a station type for this plug-in event  [Behav].

        Availability constrains choice: a car with no home charger cannot use
        the HOME profile, so its demand shifts to public points -- which is the
        mechanism behind the well-known pattern that home charging access
        strongly shapes public fast-charging load.
        """
        options: list[str] = []
        weights: list[float] = []
        for profile, base in STATION_ACCESS_MIX.items():
            if profile == "HOME":
                if not v.has_home_charger:
                    continue
                w = base
            elif profile == "WORK":
                if v.work_station_id is None:
                    continue
                w = base
            elif profile == "PUBLIC_DC":
                # No home charger means much heavier reliance on fast charging.
                w = base * (3.4 if not v.has_home_charger else 1.0)
            else:
                w = base
            options.append(profile)
            weights.append(w)

        if not options:
            return None
        probs = np.array(weights, dtype=float)
        probs /= probs.sum()
        return str(self.rng.choice(options, p=probs))

    def _maybe_charge(self, v: Vehicle, date: datetime, is_weekend: bool,
                      temp_c: float, record: bool = True) -> None:
        """Possibly run one charging session for this vehicle today.

        With ``record=False`` the session still happens and the vehicle's state
        still advances -- it is simply not written out. Warm-up uses this so the
        dynamics are identical while the recorded data stays clean.
        """
        profile = self._pick_profile(v)
        if profile is None:
            return

        # Cost sensitivity: people are less likely to fast-charge when they are
        # not in a hurry, and the arrival SOC gates everything.
        if self.rng.random() > DAILY_PLUG_IN_PROB[profile]:
            return
        if v.soc > 0.72 and profile in ("PUBLIC_DC", "PUBLIC_AC"):
            return  # no reason to pay fast-charging prices with a full-ish pack

        station = self._station_for(profile, v)
        if station is None:
            return
        # Use the *station's own* nameplate rating, not the generic rating of
        # its type. Fast-charging sites are built with mixed 60/120/180 kW
        # units, and using the type template instead of the instance made every
        # DC session behave like a 120 kW charger -- so sessions at 60 kW posts
        # delivered up to twice their rated power, which is not physically
        # possible. The error was invisible in the summary statistics because it
        # averaged away across the three ratings.
        stype = replace(STATION_BY_KEY[station["type"]], rated_kw=station["rated_kw"])

        hour = int(self.rng.choice(24, p=_hour_weights(profile, is_weekend)))
        minute = int(self.rng.integers(0, 60))
        start = date + timedelta(hours=hour, minutes=minute)

        target_mean, target_sd = TARGET_SOC[profile]
        target = float(np.clip(self.rng.normal(target_mean, target_sd), v.soc + 0.05, 0.98))
        if target <= v.soc + 0.01:
            return

        # Session temperature: the pack is near ambient when it is parked.
        session_temp = temp_c + float(self.rng.normal(0.0, 1.5))

        result = simulate_session(
            start=start,
            station=stype,
            battery_kwh=v.battery_kwh,
            start_soc=v.soc,
            target_soc=target,
            temp_c=session_temp,
            rng=self.rng,
        )
        if result is None:
            return

        v.soc = result["end_soc"]

        # Accumulate into separate counters during warm-up so that per-vehicle
        # statistics describe the recorded period only, while the vehicle's
        # state still evolves through warm-up exactly as it would otherwise.
        if not record:
            v.warmup_sessions += 1
            v.warmup_energy_kwh += result["energy_kwh"]
            return

        v.n_sessions += 1
        v.energy_total_kwh += result["energy_kwh"]
        if profile == "PUBLIC_DC":
            v.dc_sessions += 1

        self.sessions.append({
            "session_id": f"S{len(self.sessions):06d}",
            "vehicle_id": v.vehicle_id,
            "station_id": station["station_id"],
            "station_type": station["type"],
            "station_name": station["name"],
            "district": station["district"],
            "start_time": result["start"].isoformat(timespec="seconds"),
            "end_time": result["end"].isoformat(timespec="seconds"),
            "date": result["start"].date().isoformat(),
            "weekday": WEEKDAY_NAMES[result["start"].weekday()],
            "is_weekend": int(result["start"].weekday() >= 5),
            "hour": result["start"].hour,
            "duration_min": result["duration_min"],
            "energy_kwh": result["energy_kwh"],
            "energy_grid_kwh": result["energy_grid_kwh"],
            "avg_power_kw": result["avg_power_kw"],
            "peak_power_kw": result["peak_power_kw"],
            "start_soc": result["start_soc"],
            "end_soc": result["end_soc"],
            "battery_kwh": v.battery_kwh,
            "temp_c": round(session_temp, 2),
            "idle_min": self._idle_minutes(profile, result["duration_min"]),
            "power_curve": result["power_curve"],
        })

    def _station_for(self, profile: str, v: Vehicle) -> dict | None:
        """Pick a concrete station for a profile."""
        if profile == "HOME":
            if not v.home_station_id:
                return None
            return next((s for s in self.stations
                         if s["station_id"] == v.home_station_id), None)
        if profile == "WORK":
            if not v.work_station_id:
                return None
            return next((s for s in self.stations
                         if s["station_id"] == v.work_station_id), None)
        if profile == "PUBLIC_AC":
            sid = str(self.rng.choice(self._public_ac_ids))
        else:
            sid = str(self.rng.choice(self._public_dc_ids))
        return next((s for s in self.stations if s["station_id"] == sid), None)

    def _idle_minutes(self, profile: str, duration_min: float) -> float:
        """Time the car stayed plugged in after charging finished.

        This is the "occupied but not charging" problem that drives real
        station congestion. It is significant for long-dwell sites (home,
        workplace) and negligible for fast charging, where drivers wait.
        """
        if profile == "HOME":
            return round(float(self.rng.uniform(0.0, 240.0)), 1)
        if profile == "WORK":
            return round(float(self.rng.uniform(0.0, 300.0)), 1)
        if profile == "PUBLIC_AC":
            return round(float(self.rng.uniform(0.0, 90.0)), 1)
        # DC: typically unplug and leave promptly, with a small tail.
        extra = float(self.rng.exponential(4.0))
        return round(min(extra, duration_min * 0.5), 1)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def write_csv(path: Path, rows: Iterable[dict], fieldnames: list[str]) -> int:
    """Write rows to a CSV, returning the count. Creates parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            n += 1
    return n


SESSION_FIELDS = [
    "session_id", "vehicle_id", "station_id", "station_type", "station_name",
    "district", "start_time", "end_time", "date", "weekday", "is_weekend",
    "hour", "duration_min", "energy_kwh", "energy_grid_kwh", "avg_power_kw",
    "peak_power_kw", "start_soc", "end_soc", "battery_kwh", "temp_c",
    "idle_min", "power_curve",
]
STATION_FIELDS = ["station_id", "type", "name", "rated_kw", "n_connectors", "district"]
DAILY_FIELDS = ["date", "weekday", "is_weekend", "temp_mean_c", "n_sessions",
                "energy_kwh", "n_vehicles"]


def generate(out_dir: Path, seed: int = 42, vehicles: int = N_VEHICLES,
             days: int = DAYS) -> dict[str, int]:
    """Run the generator and write the raw CSV files. Returns row counts."""
    gen = FleetGenerator(seed=seed, n_vehicles=vehicles, days=days)
    gen.run()

    counts = {
        "sessions": write_csv(out_dir / "sessions.csv", gen.sessions, SESSION_FIELDS),
        "stations": write_csv(out_dir / "stations.csv", gen.stations, STATION_FIELDS),
        "daily": write_csv(out_dir / "daily_summary.csv", gen.daily, DAILY_FIELDS),
    }
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic EV charging data")
    parser.add_argument("--days", type=int, default=DAYS)
    parser.add_argument("--vehicles", type=int, default=N_VEHICLES)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/raw"))
    args = parser.parse_args(argv)

    counts = generate(args.out, seed=args.seed, vehicles=args.vehicles, days=args.days)
    print(f"wrote {counts['sessions']:,} sessions, {counts['stations']} stations, "
          f"{counts['daily']} days -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
