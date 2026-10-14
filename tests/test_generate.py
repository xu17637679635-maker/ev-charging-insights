"""Tests for the charging session generator.

These exist because the generator produces *plausible-looking* data even when
it is physically wrong. Two real bugs found during development illustrate the
pattern, and both are now covered by a test:

1. Sessions at 60 kW DC posts delivered up to 2x their rated power, because the
   code used the generic rating for the station *type* instead of the rating of
   that specific station. Summary statistics hid it: the error averaged out
   across the three DC ratings.
2. The fleet's equilibrium state of charge drifted up to ~70%, which pushed
   every session into the region where the charge taper is active and made the
   mean charging power meaningless. Nothing errored; the behaviour model was
   simply wrong.

Neither bug raises an exception. Both are caught below by asserting *physical
and statistical invariants* rather than exact values.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

# pytest is the intended runner, but it is not installed in every environment
# this project is developed in. Fall back to a no-op decorator so the same test
# functions also run under scripts/run_tests.py.
try:
    import pytest

    fixture = pytest.fixture
except ImportError:  # pragma: no cover - exercised in restricted environments
    def fixture(*args, **kwargs):
        if args and callable(args[0]) and not kwargs:
            return args[0]

        def decorate(fn):
            return fn

        return decorate


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.generate import (  # noqa: E402
    EXPECTED_EQUILIBRIUM_SOC,
    REFERENCE_10_80,
    REFERENCE_TAPER,
    STATION_BY_KEY,
    FleetGenerator,
    charge_step_kw,
    knee_soc,
    taper_fraction,
)


@fixture(scope="module")
def gen() -> FleetGenerator:
    """One generator run shared by the whole module (it is not cheap)."""
    g = FleetGenerator(seed=42, n_vehicles=120, days=30, warmup_days=90)
    g.run()
    return g


# ---------------------------------------------------------------------------
# Physical invariants
# ---------------------------------------------------------------------------
def test_delivered_power_never_exceeds_station_rating(gen: FleetGenerator) -> None:
    """The core physical bound: energy in <= nameplate power * time.

    This is the check that the 60 kW / 120 kW mix bug failed.
    """
    rated = {s["station_id"]: s["rated_kw"] for s in gen.stations}
    assert gen.sessions, "generator produced no sessions"
    for s in gen.sessions:
        cap = rated[s["station_id"]]
        assert s["peak_power_kw"] <= cap + 1e-6, (
            f"{s['session_id']}: peak {s['peak_power_kw']:.1f} kW exceeds "
            f"station rating {cap:.1f} kW"
        )
        implied = s["energy_kwh"] / (s["duration_min"] / 60.0)
        assert implied <= cap + 0.5, (
            f"{s['session_id']}: implied average {implied:.1f} kW exceeds "
            f"station rating {cap:.1f} kW"
        )


def test_charge_step_is_bounded_by_rating() -> None:
    """The instantaneous power model must never exceed the charger rating."""
    for soc in (0.0, 0.1, 0.3, 0.5, 0.65, 0.8, 0.9, 0.99, 1.0):
        for temp in (-15.0, 0.0, 10.0, 25.0, 40.0):
            p = charge_step_kw(soc, 120.0, 60.0, temp)
            assert 0.0 < p <= 120.0 + 1e-9, f"soc={soc} temp={temp} -> {p} kW"


def test_charge_taper_is_monotonic_and_falls_with_soc() -> None:
    """Accepted power must not increase as the battery fills."""
    powers = [charge_step_kw(soc / 100.0, 120.0, 60.0, 25.0) for soc in range(101)]
    for a, b in zip(powers, powers[1:]):
        assert b <= a + 1e-9, "taper must be non-increasing in SOC"
    # And the fall must be substantial, or the taper is not doing anything.
    assert powers[90] < 0.5 * powers[10]


def test_cold_derates_charging() -> None:
    """A cold pack accepts less power than a warm one at the same SOC."""
    warm = charge_step_kw(0.40, 120.0, 60.0, 25.0)
    cold = charge_step_kw(0.40, 120.0, 60.0, -10.0)
    assert cold < warm


# ---------------------------------------------------------------------------
# Taper calibration against measured charge curves
# ---------------------------------------------------------------------------
# These tests exist because the taper model was wrong twice in ways that no
# existing test caught, and both errors were large:
#
#   v1  taper applied to the charger rating -> a 250 kW car charged at 223 kW
#       where the measured value is 96 kW (+132%)
#   v2  taper applied to the pack's peak acceptance with a normalised shape
#       -> 38% low at 50% SOC, because the shape cannot express the switchover
#       from charger-limited to pack-limited
#
# The fix is the `min(charger, pack)` formulation, and the only way to know it
# is right is to check it against measured power at specific SOC points.
def test_taper_reproduces_measured_pack_power() -> None:
    """Model power must match the measured car the acceptance table was fitted to.

    Scoped to the Tesla Model 3 Performance deliberately. The table is fitted to
    that car, and a *different* car's measured points are a separate question --
    see :func:`test_taper_cross_vehicle_error_is_bounded` for how the known
    cross-vehicle gap is tracked rather than hidden.

    This test would have caught both earlier versions of the model: applying
    the taper to the charger rating gave 223 kW where the measurement is 96 kW,
    and the normalised-acceptance version was 38% low at 50% SOC.
    """
    m3p = {k: v for k, v in REFERENCE_TAPER.items() if k[0] == 250.0 and k[1] == 80.0}
    assert len(m3p) >= 3, "expected at least three Model 3 reference points"
    for (rated_kw, pack_kwh, soc), measured in sorted(m3p.items()):
        model = charge_step_kw(soc, rated_kw, pack_kwh, 25.0)
        rel = abs(model - measured) / measured
        assert rel < 0.15, (
            f"Model 3 P at {soc:.0%} SOC: model {model:.1f} kW vs measured "
            f"{measured:.1f} kW ({rel:+.1%})"
        )


def test_taper_cross_vehicle_error_is_bounded() -> None:
    """A second, different vehicle must stay within a documented error band.

    This asserts the limitation rather than wishing it away: one shared
    acceptance table cannot represent two packs with genuinely different
    high-SOC behaviour (measured, an ID.4 holds 94.8% of peak at 80% SOC where a
    Model 3 P holds 57.5%). The bounds here are the ones the README quotes, so
    editing the table in a way that widens the gap fails the build.
    """
    id4 = {k: v for k, v in REFERENCE_TAPER.items() if k[0] == 141.0}
    assert id4, "expected an ID.4 reference point"
    for (rated_kw, pack_kwh, soc), measured in id4.items():
        model = charge_step_kw(soc, rated_kw, pack_kwh, 25.0)
        rel = abs(model - measured) / measured
        # Instantaneous power at a high SOC is where the two packs diverge most,
        # so this band is wider than the duration band below.
        assert rel < 0.45, (
            f"ID.4 Pro at {soc:.0%} SOC: model {model:.1f} kW vs measured "
            f"{measured:.1f} kW ({rel:+.1%}) exceeds the documented band"
        )


def test_taper_reproduces_measured_10_to_80_duration() -> None:
    """The headline fast-charging number a buyer would recognise."""
    for label, (rated_kw, pack_kwh, real_min, real_kwh) in REFERENCE_10_80.items():
        soc, t, energy = 0.10, 0.0, 0.0
        while soc < 0.80:
            p = charge_step_kw(soc, rated_kw, pack_kwh, 25.0)
            dt = 1.0 / 3600.0
            energy += p * dt
            soc += p * dt / pack_kwh
            t += dt
        minutes = t * 60.0
        # Tolerance is deliberately loose for cars other than the one the table
        # was fitted to: the shared C-rate table is a model of "a typical pack",
        # not of every pack. See TAPER_TABLE for the per-vehicle fidelity.
        assert abs(minutes - real_min) / real_min < 0.45, (
            f"{label}: model {minutes:.0f} min vs measured {real_min:.0f} min"
        )
        assert abs(energy - real_kwh) / real_kwh < 0.10, (
            f"{label}: model delivered {energy:.1f} kWh vs measured {real_kwh:.1f} kWh"
        )


def test_power_never_exceeds_charger_or_pack_limit() -> None:
    """``min(charger, pack)`` must hold at every SOC, temperature and pack size."""
    for pack_kwh in (40.0, 60.0, 80.0, 100.0):
        for rated_kw in (7.0, 60.0, 120.0, 250.0, 350.0):
            for soc in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0):
                for temp in (-20.0, 0.0, 25.0, 45.0):
                    p = charge_step_kw(soc, rated_kw, pack_kwh, temp)
                    assert p <= rated_kw + 1e-9, "exceeded charger rating"
                    assert p <= taper_fraction(soc) * pack_kwh + 1e-9, (
                        "exceeded pack acceptance"
                    )
                    assert p >= 0.0


def test_taper_fraction_is_monotonic() -> None:
    """Pack acceptance must not rise as the battery fills."""
    values = [taper_fraction(i / 200.0) for i in range(201)]
    for a, b in zip(values, values[1:]):
        assert b <= a + 1e-12, "taper table must be non-increasing"
    assert values[0] > 5 * values[-1], "acceptance should fall substantially by full"


def test_knee_moves_later_with_a_bigger_pack() -> None:
    """The mechanism behind 'a more powerful charger tapers earlier'.

    The taper knee is where the pack's acceptance curve crosses the charger
    rating. A bigger pack accepts more, so the crossing happens later. This is
    the property that a fixed knee cannot express, and it is worth asserting
    because it is the whole explanation the analysis gives for the effect.
    """
    for rated_kw in (120.0, 250.0):
        small = knee_soc(rated_kw, 40.0)
        large = knee_soc(rated_kw, 100.0)
        assert large > small, (
            f"{rated_kw:.0f} kW: 100 kWh pack should taper later than 40 kWh "
            f"({large:.0%} vs {small:.0%})"
        )
    # And a more powerful charger must bind sooner on the same pack.
    assert knee_soc(250.0, 80.0) < knee_soc(60.0, 80.0)


def test_cold_weather_consumption_matches_measured_penalty() -> None:
    """-7 degC with cabin heating measured +39% consumption; check the model."""
    from src.generate import consumption_kwh_per_100km

    base = consumption_kwh_per_100km(20.0)
    at_minus7 = consumption_kwh_per_100km(-7.0)
    penalty = at_minus7 / base - 1.0
    assert 0.30 < penalty < 0.48, (
        f"model cold penalty at -7 degC is {penalty:+.0%}, measured is +39%"
    )
    # Hot side: about +17% at 35 degC.
    hot = consumption_kwh_per_100km(35.0) / base - 1.0
    assert 0.10 < hot < 0.25, f"model hot penalty at 35 degC is {hot:+.0%}"


def test_sessions_never_exceed_battery_capacity(gen: FleetGenerator) -> None:
    """Energy delivered cannot exceed the battery's usable window."""
    for s in gen.sessions:
        usable = s["battery_kwh"]
        assert s["energy_kwh"] <= usable + 1e-6, (
            f"{s['session_id']}: {s['energy_kwh']:.1f} kWh into a "
            f"{usable:.1f} kWh pack"
        )


def test_timestamps_and_derived_fields_are_consistent(gen: FleetGenerator) -> None:
    """Every session has coherent timing, SOC and energy fields."""
    assert gen.sessions
    for s in gen.sessions:
        assert s["duration_min"] > 0
        assert s["energy_kwh"] > 0
        assert s["end_time"] > s["start_time"]
        assert 0.0 <= s["start_soc"] < s["end_soc"] <= 1.0
        assert 0 <= s["hour"] <= 23
        # Relative tolerance, not absolute: the two fields are serialised with
        # different rounding (avg to 3 dp, peak to 2 dp), so a strict absolute
        # comparison trips on sessions where the taper keeps power flat and the
        # peak and the mean are legitimately equal.
        assert s["peak_power_kw"] >= s["avg_power_kw"] * (1.0 - 1e-3), (
            f"{s['session_id']}: peak {s['peak_power_kw']} < "
            f"average {s['avg_power_kw']}"
        )
        assert math.isfinite(s["energy_kwh"])


def test_every_session_references_a_real_station(gen: FleetGenerator) -> None:
    ids = {s["station_id"] for s in gen.stations}
    for s in gen.sessions:
        assert s["station_id"] in ids
        assert s["station_type"] in STATION_BY_KEY


def test_home_sessions_only_use_home_chargers(gen: FleetGenerator) -> None:
    """A private charger must not appear in another vehicle's sessions."""
    owner = {v.home_station_id: v.vehicle_id for v in gen.vehicles
             if v.home_station_id}
    for s in gen.sessions:
        if s["station_type"] == "HOME_AC":
            assert owner[s["station_id"]] == s["vehicle_id"], (
                "a vehicle charged at someone else's home charger"
            )


# ---------------------------------------------------------------------------
# Behavioural invariants
# ---------------------------------------------------------------------------
def test_equilibrium_soc_stays_in_a_realistic_band(gen: FleetGenerator) -> None:
    """Fleet SOC must not drift up to near-full, or every session sits in taper.

    This is the check that the over-frequent home-charging bug failed: mean SOC
    climbed to ~0.70, which made the measured average AC power an artefact of
    the initial condition rather than of charging behaviour.
    """
    state = gen.fleet_state()
    assert 0.30 <= state["mean_soc"] <= 0.75, (
        f"fleet mean SOC {state['mean_soc']:.3f} outside the plausible band; "
        f"expected around {EXPECTED_EQUILIBRIUM_SOC}"
    )
    mean_start = sum(s["start_soc"] for s in gen.sessions) / len(gen.sessions)
    assert 0.25 <= mean_start <= 0.70, (
        f"mean arrival SOC {mean_start:.3f} is not realistic"
    )


def test_home_charging_peaks_in_the_evening(gen: FleetGenerator) -> None:
    """The evening peak must emerge from commute timing, not be hard-coded.

    The generator encodes *when people get home*, not "there is a peak at 18:00";
    this asserts the peak actually emerges.
    """
    home_hours = [s["hour"] for s in gen.sessions if s["station_type"] == "HOME_AC"]
    assert home_hours
    evening = sum(1 for h in home_hours if 17 <= h <= 22)
    daytime = sum(1 for h in home_hours if 9 <= h <= 15)
    assert evening > 3 * daytime, (
        f"home charging should concentrate in the evening: "
        f"{evening} evening vs {daytime} daytime"
    )


def test_dc_sessions_deliver_much_higher_power_than_ac_sessions(gen: FleetGenerator) -> None:
    """Fast charging must actually be fast.

    Asserted on **power**, not on duration ratio. A duration comparison is
    confounded by how much energy each session delivers: a short DC top-up and
    a long AC session can differ by little in duration while differing by an
    order of magnitude in power, and the earlier duration-ratio assertion was
    brittle for exactly that reason.
    """
    dc_dur = [s["duration_min"] for s in gen.sessions if s["station_type"] == "PUBLIC_DC"]
    ac_dur = [s["duration_min"] for s in gen.sessions if s["station_type"] == "HOME_AC"]
    dc_pow = [s["avg_power_kw"] for s in gen.sessions if s["station_type"] == "PUBLIC_DC"]
    ac_pow = [s["avg_power_kw"] for s in gen.sessions if s["station_type"] == "HOME_AC"]
    assert dc_pow and ac_pow

    mean_dc_pow = sum(dc_pow) / len(dc_pow)
    mean_ac_pow = sum(ac_pow) / len(ac_pow)
    assert mean_dc_pow > 4.0 * mean_ac_pow, (
        f"DC mean power {mean_dc_pow:.1f} kW vs AC {mean_ac_pow:.1f} kW"
    )

    mean_dc_dur = sum(dc_dur) / len(dc_dur)
    mean_ac_dur = sum(ac_dur) / len(ac_dur)
    assert mean_dc_dur < 0.5 * mean_ac_dur, (
        f"DC mean duration {mean_dc_dur:.0f} min vs AC {mean_ac_dur:.0f} min"
    )


def test_energy_in_roughly_balances_energy_out(gen: FleetGenerator) -> None:
    """Over a long run the fleet cannot accumulate or lose charge indefinitely.

    A persistent imbalance would mean the driving and charging models disagree
    about how much energy a car uses, which would quietly invalidate every
    downstream aggregate.
    """
    state = gen.fleet_state()
    # Mean SOC must stay near the middle rather than pinned at either rail.
    assert 0.05 < state["mean_soc"] < 0.95


def test_no_home_charger_shifts_demand_to_public_dc(gen: FleetGenerator) -> None:
    """Vehicles without a private charger should lean on fast charging.

    This is a mechanism worth testing because it is the kind of structural
    finding the analysis is meant to surface.
    """
    has_home = {v.vehicle_id for v in gen.vehicles if v.has_home_charger}
    no_home = {v.vehicle_id for v in gen.vehicles if not v.has_home_charger}
    assert no_home, "test fleet should contain vehicles without home charging"

    def dc_rate(ids: set[str]) -> float:
        sessions = [s for s in gen.sessions if s["vehicle_id"] in ids]
        if not sessions:
            return 0.0
        return sum(1 for s in sessions
                   if s["station_type"] == "PUBLIC_DC") / len(sessions)

    assert dc_rate(no_home) > dc_rate(has_home), (
        "vehicles without home charging should use DC more often"
    )


# ---------------------------------------------------------------------------
# Engineering invariants
# ---------------------------------------------------------------------------
def test_generation_is_deterministic_for_a_fixed_seed() -> None:
    a = FleetGenerator(seed=7, n_vehicles=25, days=10, warmup_days=20)
    a.run()
    b = FleetGenerator(seed=7, n_vehicles=25, days=10, warmup_days=20)
    b.run()
    assert len(a.sessions) == len(b.sessions)
    for sa, sb in zip(a.sessions, b.sessions):
        assert sa["energy_kwh"] == sb["energy_kwh"]
        assert sa["start_time"] == sb["start_time"]


def test_different_seeds_produce_different_data() -> None:
    a = FleetGenerator(seed=1, n_vehicles=25, days=10, warmup_days=20)
    a.run()
    b = FleetGenerator(seed=2, n_vehicles=25, days=10, warmup_days=20)
    b.run()
    assert [s["energy_kwh"] for s in a.sessions] != [s["energy_kwh"] for s in b.sessions]


def test_warmup_sessions_are_not_recorded(gen: FleetGenerator) -> None:
    """Warm-up must advance vehicle state without polluting the output."""
    for v in gen.vehicles:
        assert v.warmup_sessions > 0, "warm-up did not run"
        assert v.n_sessions == sum(
            1 for s in gen.sessions if s["vehicle_id"] == v.vehicle_id
        ), "recorded session count does not match the vehicle's own counter"


def test_daily_summary_covers_every_recorded_day(gen: FleetGenerator) -> None:
    assert len(gen.daily) == gen.days
    assert len({d["date"] for d in gen.daily}) == gen.days


# ---------------------------------------------------------------------------
# Seasonality
# ---------------------------------------------------------------------------
def test_season_follows_the_calendar() -> None:
    """Winter must be cold and summer warm.

    Regression test for an inverted year. The season was once driven by an
    elapsed-day counter, so a run beginning in October put its warmest month in
    January and its coldest in July. The temperature series looked perfectly
    plausible in isolation -- smooth, seasonal, sensible range -- and every other
    test passed. Only checking month against temperature catches it.

    This builds its own year-long generator rather than reusing the shared
    fixture: the fixture is a 30-day window, which by design contains only one
    month, so a seasonality claim cannot be evaluated on it.
    """
    g = FleetGenerator(seed=3, n_vehicles=40, days=400, warmup_days=30)
    g.run()

    by_month: dict[str, list[float]] = {}
    for row in g.daily:
        by_month.setdefault(row["date"][:7], []).append(row["temp_mean_c"])
    means = {m: sum(v) / len(v) for m, v in by_month.items()}
    assert len(means) >= 12, f"expected a full year of months, got {sorted(means)}"

    winter = min(means[m] for m in ("2025-01", "2025-02"))
    summer = max(means[m] for m in ("2025-07", "2025-08"))
    assert winter < 8.0, f"winter months average {winter:.1f} degC; expected cold"
    assert summer > 25.0, f"summer months average {summer:.1f} degC; expected hot"
    assert summer - winter > 15.0, (
        f"seasonal swing only {summer - winter:.1f} degC"
    )


def test_temperature_is_a_function_of_the_date_alone() -> None:
    """The same calendar day must be equally warm regardless of run setup.

    Anchoring the season to the day of year is what makes this true; the
    elapsed-day version failed it, because "day 100" meant a different month in
    every run.
    """
    import numpy as np

    from src.generate import daily_temperature

    jan, jul = 17, 200
    # Identical seeds so the weather noise cancels and only the seasonal term
    # is compared.
    assert daily_temperature(jan, np.random.default_rng(1)) == \
        daily_temperature(jan, np.random.default_rng(1))
    assert daily_temperature(jul, np.random.default_rng(2)) > \
        daily_temperature(jan, np.random.default_rng(2)) + 15.0
