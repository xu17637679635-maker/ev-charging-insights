"""Tests for the real-data loader.

Two kinds of test here:

* **Unit tests** on hand-built frames. These run anywhere, with no dataset
  needed, and pin the behaviours that are easy to get subtly wrong
  (ratio-of-sums vs mean-of-ratios, occupancy guards, validation catching
  impossible rows).
* **Integration tests** that need the 38 MB UrbanEV download. They return early
  when ``data/real/urbanev`` is absent, so the suite stays green on a fresh
  checkout; the README says how to fetch the data and re-enable them.

The unit tests exist because of a concrete failure found in this dataset: zone
775 reports 5,580 kWh against an occupancy of 1 for one hour, which yields
5,580 kW if the per-row ratio is trusted. Anything that averages per-row ratios
inherits that error.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import real_data as rd  # noqa: E402


def _has_real_data() -> bool:
    return (rd.DEFAULT_DIR / "volume.csv").exists()


def _frame(**overrides) -> pd.DataFrame:
    """A minimal zone-hour frame with sensible defaults."""
    base = pd.DataFrame({
        "zone_id": [1, 1, 1, 2, 2, 2],
        "timestamp": pd.to_datetime([
            "2022-09-01 00:00", "2022-09-01 01:00", "2022-09-01 02:00",
            "2022-09-01 00:00", "2022-09-01 01:00", "2022-09-01 02:00",
        ]),
        "volume_kwh": [100.0, 200.0, 300.0, 50.0, 60.0, 70.0],
        "occupancy": [10.0, 20.0, 30.0, 5.0, 6.0, 7.0],
        "n_piles": [50, 50, 50, 10, 10, 10],
    })
    base["utilisation"] = base["occupancy"] / base["n_piles"]
    base["power_implausible"] = 0
    base["mean_power_kw"] = base["volume_kwh"] / base["occupancy"]
    for key, value in overrides.items():
        base[key] = value
    return base


# ---------------------------------------------------------------------------
# aggregate_power_kw -- the estimator that matters
# ---------------------------------------------------------------------------
def test_aggregate_power_is_ratio_of_sums() -> None:
    """Total energy over total occupied-hours, not the mean of per-hour ratios."""
    volume = pd.Series([100.0, 200.0])
    occupancy = pd.Series([10.0, 20.0])
    # Both hours deliver 10 kW, so either estimator gives 10 here.
    assert rd.aggregate_power_kw(volume, occupancy) == 10.0


def test_aggregate_power_resists_a_single_outlier_row() -> None:
    """One absurd row must not drag the group estimate, as it would a mean.

    This is the zone-775 case in miniature: three normal hours plus one hour
    whose occupancy reading collapsed to 1.
    """
    volume = pd.Series([100.0, 200.0, 300.0, 5580.0])
    occupancy = pd.Series([10.0, 20.0, 30.0, 1.0])

    robust = rd.aggregate_power_kw(volume, occupancy)
    naive = float((volume / occupancy).mean())

    # The ratio of sums lands near the normal hours (about 120 kW because the
    # 5,580 kWh hour dominates the numerator), while the mean of ratios is
    # hundreds of kW. The exact values matter less than the ordering.
    assert robust < naive
    assert robust < 200.0, f"robust estimate {robust:.1f} kW should not explode"
    assert naive > 1000.0


def test_aggregate_power_returns_nan_when_nothing_was_occupied() -> None:
    result = rd.aggregate_power_kw(pd.Series([0.0, 0.0]), pd.Series([0.0, 0.0]))
    assert math.isnan(result)


def test_aggregate_power_ignores_zero_volume_hours() -> None:
    """An idle hour adds no energy and no occupied hours, so it changes nothing."""
    with_idle = rd.aggregate_power_kw(pd.Series([100.0, 0.0]), pd.Series([10.0, 0.0]))
    without = rd.aggregate_power_kw(pd.Series([100.0]), pd.Series([10.0]))
    assert with_idle == without == 10.0


# ---------------------------------------------------------------------------
# validate -- catching impossible rows
# ---------------------------------------------------------------------------
def test_validate_accepts_a_clean_frame() -> None:
    assert rd.validate(_frame()) == []


def test_validate_flags_negative_volume() -> None:
    df = _frame()
    df.loc[0, "volume_kwh"] = -1.0
    problems = rd.validate(df)
    assert any("negative volume" in p for p in problems)


def test_validate_flags_occupancy_above_installed_piles() -> None:
    """The denominator being wrong silently caps utilisation and hides congestion."""
    df = _frame()
    df.loc[0, "occupancy"] = 999.0
    problems = rd.validate(df)
    assert any("occupancy > installed piles" in p for p in problems)


def test_validate_flags_a_non_hourly_grid() -> None:
    df = _frame()
    df.loc[1, "timestamp"] = pd.Timestamp("2022-09-01 05:00")
    problems = rd.validate(df)
    assert any("not hourly" in p for p in problems)


# ---------------------------------------------------------------------------
# quality_report -- the numbers that must accompany any conclusion
# ---------------------------------------------------------------------------
def test_quality_report_counts_suppressed_rows() -> None:
    df = _frame()
    df["mean_power_kw"] = np.nan
    df.loc[0, "occupancy"] = 1.0       # below MIN_OCCUPANCY_FOR_POWER
    df.loc[1, "volume_kwh"] = 0.0
    df.loc[2, "occupancy"] = 0.0       # fully idle
    df.loc[3, "power_implausible"] = 1

    q = rd.quality_report(df)
    assert q["rows"] == 6
    assert q["rows_below_min_occupancy"] == 2   # occupancy 1 and 0
    assert q["rows_implausible_power"] == 1
    assert q["rows_zero_volume"] == 1
    assert q["rows_fully_idle"] == 1


# ---------------------------------------------------------------------------
# Integration -- needs the downloaded dataset
# ---------------------------------------------------------------------------
def test_real_dataset_loads_and_passes_validation() -> None:
    """End-to-end check on the actual download.

    Returns early (a no-op) when the data is absent, so a fresh clone still has
    a green suite. Run ``python scripts/fetch_data.py`` to enable it.
    """
    if not _has_real_data():
        return

    df = rd.load_zone_hourly()
    assert len(df) > 100_000
    assert rd.validate(df) == []

    # Columns every downstream analysis assumes.
    for column in ("zone_id", "timestamp", "hour", "volume_kwh", "occupancy",
                   "n_piles", "utilisation", "mean_power_kw", "power_implausible"):
        assert column in df.columns, f"missing column {column}"

    # The dataset has exactly 275 traffic zones.
    assert df["zone_id"].nunique() == 275

    # Utilisation is a fraction by construction.
    util = df["utilisation"].dropna()
    assert util.min() >= 0.0 and util.max() <= 1.0

    # Suppressed rows are suppressed: no per-row power below the occupancy floor.
    suppressed = df["occupancy"] < rd.MIN_OCCUPANCY_FOR_POWER
    assert df.loc[suppressed, "mean_power_kw"].isna().all()


def test_real_dataset_power_is_physically_plausible() -> None:
    """The robust aggregate must land in a range a real pile can deliver."""
    if not _has_real_data():
        return
    df = rd.load_zone_hourly()
    ok = df[df["power_implausible"] == 0]
    power = rd.aggregate_power_kw(ok["volume_kwh"], ok["occupancy"])
    assert 1.0 < power < rd.MAX_PLAUSIBLE_PILE_KW, (
        f"aggregate power {power:.1f} kW outside plausible range"
    )


def test_real_dataset_ids_match_station_metadata() -> None:
    """Zone ids in the time series must exist in inf.csv, or utilisation is NaN."""
    if not _has_real_data():
        return
    meta = rd.load_station_meta()
    df = rd.load_zone_hourly()
    assert set(df["zone_id"].unique()) == set(meta["zone_id"].unique())


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------
def test_temperature_column_is_read_from_the_shipped_header(tmp_path) -> None:
    """The temperature column is named ``T``; only the doc file explains that.

    Guessing from column names alone returns None here, which silently drops the
    temperature analysis. Parsing the dataset's own header file is the robust
    fix, and this test pins it.
    """
    header = tmp_path / "weather_header.txt"
    header.write_text(
        "Weather Data Table Header：\n"
        "T：Air temperature (degree Celsius) at 2 metre height\n"
        "U: Relative humidty (%)\n",
        encoding="utf-8",
    )
    assert rd._temperature_code_from_header(header) == "T"

    # ASCII colon variant, and a file without the line, must both behave.
    header2 = tmp_path / "h2.txt"
    header2.write_text("T: Air temperature (C)\n", encoding="utf-8")
    assert rd._temperature_code_from_header(header2) == "T"

    header3 = tmp_path / "h3.txt"
    header3.write_text("U: humidity only\n", encoding="utf-8")
    assert rd._temperature_code_from_header(header3) is None

    assert rd._temperature_code_from_header(tmp_path / "missing.txt") is None


def test_weather_loader_returns_none_without_a_weather_file(tmp_path) -> None:
    """Absence must be reported as None so callers skip the analysis cleanly."""
    assert rd.load_weather(tmp_path) is None


def test_weather_loader_parses_the_real_file() -> None:
    """Integration: the shipped weather file must yield a usable temperature."""
    if not (rd.DEFAULT_DIR / "weather_central.csv").exists():
        return
    weather = rd.load_weather()
    assert weather is not None
    assert {"timestamp", "temp_c"} <= set(weather.columns)
    assert len(weather) > 1000
    # Shenzhen, Sep-Feb: a plausible band. A mis-parsed column (pressure,
    # humidity) would immediately fall outside it.
    assert -5 < weather["temp_c"].min() and weather["temp_c"].max() < 45
    assert weather["timestamp"].is_monotonic_increasing
