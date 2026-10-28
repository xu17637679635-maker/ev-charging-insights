"""Loader for the UrbanEV open dataset (Shenzhen public charging).

Where the data comes from
-------------------------
UrbanEV, published by the Intelligent Systems Lab, is an openly-licensed
benchmark of Shenzhen public charging telemetry: 24,798 piles across 275
traffic-analysis zones, hourly, for six months from 2022-09-01. Fetch it with
``python scripts/fetch_data.py``. See ``data/real/urbanev/readme.md`` for the
upstream documentation and ``docs/real_data_notes.md`` for what this project
does with it.

Granularity -- the thing to understand before using this module
--------------------------------------------------------------
The files are **wide**: one row per hour, one column per zone. That is not the
same shape as the synthetic pipeline, which produces one row per charging
session. The real dataset contains no per-session record at all: no arrival
SOC, no power curve, no plug-in timestamp. What it exposes is:

    volume.csv      kWh delivered in that zone during that hour
    occupancy.csv   number of piles occupied during that hour
    duration.csv    mean charging duration, in hours
    inf.csv         station metadata, mapped to its traffic zone

So anything derived here is a **zone-hour aggregate**, and the analysis must be
built on aggregates rather than sessions. Two useful quantities fall out of
these columns that the raw files do not state:

    mean power per occupied pile = volume_kwh / occupancy        (kW)
    pile utilisation             = occupancy / n_piles           (fraction)

Both are computed in :func:`load_zone_hourly`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_DIR = Path("data/real/urbanev")

# Column meanings differ between the upstream files only in units, so they share
# one timestamp parser. The dataset is hourly, but the raw strings carry an
# explicit clock time and are parsed rather than assumed.
TIME_COLUMN = "time"

# --- Derived-power guards ---------------------------------------------------
# ``mean_power_kw = volume / occupancy`` divides by a small integer, so a single
# bad occupancy reading produces an absurd power. A real example in this data:
# zone 775 on 2022-11-30 00:00 reports 5,580 kWh with occupancy 1, implying
# 5,580 kW -- physically impossible, while the neighbouring hours at the same
# zone are a plausible ~250 kW DC hub. The energy reading is fine; the
# occupancy reading is the glitch.
#
# Two independent defences, both of which must be visible in any report:
#
#  1. Below MIN_OCCUPANCY_FOR_POWER the per-hour ratio is not defined at all.
#  2. Above MAX_PLAUSIBLE_PILE_KW the row is flagged rather than silently used.
#
# None of this makes the per-row ratio trustworthy for aggregation. Analyses in
# ``analyze_real`` must compute power as ``sum(volume) / sum(occupancy)`` over a
# group -- the ratio of sums is stable where the mean of ratios is not.
MIN_OCCUPANCY_FOR_POWER = 3
MAX_PLAUSIBLE_PILE_KW = 400.0  # generous ceiling: 350 kW DC + headroom


class RealDataError(RuntimeError):
    """Raised when the downloaded data is missing or internally inconsistent."""


@dataclass(frozen=True)
class DatasetSummary:
    """What was actually loaded, for reporting and for the README."""

    n_zones: int
    n_stations: int
    n_piles: int
    n_hours: int
    start: pd.Timestamp
    end: pd.Timestamp
    total_kwh: float
    n_missing_volume: int
    n_missing_occupancy: int


# ---------------------------------------------------------------------------
# Raw file access
# ---------------------------------------------------------------------------
def _require(path: Path) -> Path:
    if not path.exists():
        raise RealDataError(
            f"missing {path}. Run: python scripts/fetch_data.py --dataset urbanev"
        )
    return path


def load_station_meta(data_dir: Path = DEFAULT_DIR) -> pd.DataFrame:
    """Station-level metadata: coordinates, traffic zone, charger count.

    ``charge_count`` is the number of piles at the station, which is what makes
    a utilisation calculation possible: the denominator of
    ``occupancy / n_piles`` comes from summing this column per zone.
    """
    df = pd.read_csv(_require(data_dir / "inf.csv"))
    required = {"station_id", "longitude", "latitude", "charge_count", "TAZID"}
    missing = required - set(df.columns)
    if missing:
        raise RealDataError(f"inf.csv is missing columns: {sorted(missing)}")

    df = df.rename(columns={"TAZID": "zone_id"})
    df["zone_id"] = df["zone_id"].astype(int)
    df["station_id"] = df["station_id"].astype(int)
    if (df["charge_count"] <= 0).any():
        raise RealDataError("inf.csv contains non-positive charge_count")
    return df


def _load_wide(path: Path) -> pd.DataFrame:
    """Read a wide zone-hour file and normalise its columns.

    Columns arrive as strings from the header; they are cast to int zone ids so
    they join cleanly against ``inf.csv``. Zone ids are kept as int rather than
    treated as labels -- a string/int mismatch here is a silent join failure
    that produces an empty analysis rather than an error.
    """
    df = pd.read_csv(_require(path))
    if TIME_COLUMN not in df.columns:
        raise RealDataError(f"{path.name} has no {TIME_COLUMN!r} column")

    df[TIME_COLUMN] = pd.to_datetime(df[TIME_COLUMN], errors="coerce")
    if df[TIME_COLUMN].isna().any():
        bad = int(df[TIME_COLUMN].isna().sum())
        raise RealDataError(f"{path.name}: {bad} unparseable timestamps")

    # Every remaining column is a zone.
    zone_cols = [c for c in df.columns if c != TIME_COLUMN]
    try:
        df = df.rename(columns={c: int(c) for c in zone_cols})
    except ValueError as exc:
        raise RealDataError(
            f"{path.name}: non-numeric zone column encountered ({exc})"
        ) from exc

    df = df.set_index(TIME_COLUMN).sort_index()
    if df.index.has_duplicates:
        raise RealDataError(f"{path.name}: duplicate timestamps")
    # Renaming hundreds of columns leaves the frame fragmented, which makes the
    # later melt slow and emits a PerformanceWarning. Copying once here defrags
    # it; the copy is cheap relative to the melt that follows.
    return df.copy()


def load_wide(name: str, data_dir: Path = DEFAULT_DIR) -> pd.DataFrame:
    """One of the raw wide tables: ``volume``, ``occupancy`` or ``duration``."""
    return _load_wide(data_dir / f"{name}.csv")


# ---------------------------------------------------------------------------
# Zone-hour table
# ---------------------------------------------------------------------------
def load_zone_hourly(data_dir: Path = DEFAULT_DIR) -> pd.DataFrame:
    """Tidy zone-hour table with the two derived quantities that matter.

    Returns columns:
        zone_id, timestamp, hour, weekday, is_weekend,
        volume_kwh, occupancy, duration_h, n_piles,
        mean_power_kw          (volume / occupancy, kW)
        utilisation            (occupancy / n_piles, 0-1)

    Notes on construction rather than assumption:
    * ``n_piles`` comes from summing ``charge_count`` over the stations in each
      zone, so utilisation has a real denominator instead of a guess.
    * ``mean_power_kw`` is only defined when something was actually occupied.
      Leaving those rows as NaN (rather than filling with zero) matters: zero
      would pull every downstream average down and look like a real finding.
    """
    meta = load_station_meta(data_dir)
    piles_per_zone = (
        meta.groupby("zone_id")["charge_count"].sum().rename("n_piles").astype(int)
    )

    volume = load_wide("volume", data_dir)
    occupancy = load_wide("occupancy", data_dir)

    # Align zones present in both files. A zone missing from one of them cannot
    # contribute a utilisation figure, and silently broadcasting NaNs would
    # contaminate the aggregates.
    common_zones = sorted(set(volume.columns) & set(occupancy.columns))
    if not common_zones:
        raise RealDataError("volume.csv and occupancy.csv share no zone columns")
    volume = volume[common_zones]
    occupancy = occupancy[common_zones]

    unknown = [z for z in common_zones if z not in piles_per_zone.index]
    if unknown:
        raise RealDataError(
            f"{len(unknown)} zones in the time series have no station metadata, "
            f"e.g. {unknown[:5]}"
        )

    vol_long = volume.reset_index().melt(
        id_vars=TIME_COLUMN, var_name="zone_id", value_name="volume_kwh"
    )
    occ_long = occupancy.reset_index().melt(
        id_vars=TIME_COLUMN, var_name="zone_id", value_name="occupancy"
    )
    df = vol_long.merge(occ_long, on=[TIME_COLUMN, "zone_id"], how="outer")

    df = df.rename(columns={TIME_COLUMN: "timestamp"})
    df["n_piles"] = df["zone_id"].map(piles_per_zone)

    # Behavioural time features, computed once here so every analysis agrees.
    df["hour"] = df["timestamp"].dt.hour.astype("int16")
    df["weekday"] = df["timestamp"].dt.weekday.astype("int16")
    df["is_weekend"] = (df["weekday"] >= 5).astype("int8")
    df["date"] = df["timestamp"].dt.normalize()

    # Derived quantities. Guarded against division by zero: a zone with an
    # occupancy of zero is idle, not infinitely powerful.
    #
    # ``mean_power_kw`` is additionally suppressed below
    # MIN_OCCUPANCY_FOR_POWER. Dividing by one or two occupied piles turns an
    # occupancy glitch into a four-digit power figure -- see the guards comment
    # at the top of this module for a concrete example from this dataset.
    occupied = df["occupancy"].where(df["occupancy"] >= MIN_OCCUPANCY_FOR_POWER)
    df["mean_power_kw"] = (df["volume_kwh"] / occupied).astype("float64")
    df["utilisation"] = (df["occupancy"] / df["n_piles"].where(df["n_piles"] > 0)
                         ).clip(upper=1.0)
    # Keep an explicit flag instead of dropping rows, so quality can be reported.
    df["power_implausible"] = (
        df["mean_power_kw"] > MAX_PLAUSIBLE_PILE_KW
    ).fillna(False).astype("int8")

    dur_path = data_dir / "duration.csv"
    if dur_path.exists():
        dur = load_wide("duration", data_dir)
        dur = dur[[z for z in common_zones if z in dur.columns]]
        dur_long = dur.reset_index().melt(
            id_vars=TIME_COLUMN, var_name="zone_id", value_name="duration_h"
        ).rename(columns={TIME_COLUMN: "timestamp"})
        df = df.merge(dur_long, on=["timestamp", "zone_id"], how="left")
    else:
        df["duration_h"] = np.nan

    return df.sort_values(["timestamp", "zone_id"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------
# The upstream weather files use short Chinese column headers that are defined
# in a separate header file. Those names are discovered at runtime rather than
# hard-coded, because guessing a column name here would fail silently by
# producing an all-NaN temperature series.
TEMPERATURE_CANDIDATES = ("气温", "温度", "temperature", "temp", "TEM", "Temperature")


def load_weather(data_dir: Path = DEFAULT_DIR) -> pd.DataFrame | None:
    """Hourly weather at the central station, or ``None`` if unavailable.

    Returns a frame with ``timestamp`` and ``temp_c`` when it can find a
    temperature column. Returning ``None`` (rather than an empty frame) lets
    callers skip the temperature analysis explicitly instead of plotting
    nothing and looking broken.
    """
    candidates = [data_dir / "weather_central.csv", data_dir / "weather_airport.csv"]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return None

    df = pd.read_csv(path)
    time_col = next((c for c in df.columns
                     if str(c).strip().lower() in ("time", "时间", "datetime")), None)
    if time_col is None:
        return None

    temp_col = next((c for c in df.columns
                     if any(k in str(c) for k in TEMPERATURE_CANDIDATES)), None)
    if temp_col is None:
        return None

    out = pd.DataFrame({
        "timestamp": pd.to_datetime(df[time_col], errors="coerce"),
        "temp_c": pd.to_numeric(df[temp_col], errors="coerce"),
    }).dropna()
    if out.empty:
        return None
    return out.sort_values("timestamp").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Summary and validation
# ---------------------------------------------------------------------------
def summarise(df: pd.DataFrame, meta: pd.DataFrame) -> DatasetSummary:
    """One-paragraph description of the loaded slice, for the report header."""
    return DatasetSummary(
        n_zones=int(df["zone_id"].nunique()),
        n_stations=int(len(meta)),
        n_piles=int(meta["charge_count"].sum()),
        n_hours=int(df["timestamp"].nunique()),
        start=df["timestamp"].min(),
        end=df["timestamp"].max(),
        total_kwh=float(df["volume_kwh"].sum(skipna=True)),
        n_missing_volume=int(df["volume_kwh"].isna().sum()),
        n_missing_occupancy=int(df["occupancy"].isna().sum()),
    )


def validate(df: pd.DataFrame) -> list[str]:
    """Check the invariants the analyses rely on. Returns a list of problems.

    These are the failure modes that produce plausible-looking but wrong
    numbers rather than exceptions:

    * negative energy or occupancy would poison every average;
    * occupancy exceeding the installed pile count means the denominator is
      wrong, which silently caps utilisation and hides real congestion;
    * a non-hourly regular timestamp grid breaks any per-hour-of-day aggregate;
    * utilisation above 1 is physically impossible in this dataset's definition
      and would indicate a join error.
    """
    problems: list[str] = []

    neg_v = int((df["volume_kwh"] < 0).sum())
    if neg_v:
        problems.append(f"{neg_v} rows have negative volume_kwh")
    neg_o = int((df["occupancy"] < 0).sum())
    if neg_o:
        problems.append(f"{neg_o} rows have negative occupancy")

    over = df[df["occupancy"] > df["n_piles"]]
    if len(over):
        problems.append(
            f"{len(over)} rows have occupancy > installed piles "
            f"(max {int(over['occupancy'].max())} vs {int(over['n_piles'].max())})"
        )

    util = df["utilisation"].dropna()
    if len(util) and util.max() > 1.0 + 1e-9:
        problems.append(f"utilisation exceeds 1.0 (max {util.max():.3f})")

    unique_ts = pd.Series(sorted(df["timestamp"].unique()))
    if len(unique_ts) > 2:
        gaps = unique_ts.diff().dropna().dt.total_seconds() / 3600.0
        if not np.allclose(gaps, 1.0, atol=1e-6):
            problems.append(
                f"timestamp grid is not hourly (gaps {gaps.min():.2f}h..{gaps.max():.2f}h)"
            )

    return problems


def dataset_manifest(data_dir: Path = DEFAULT_DIR) -> dict:
    """Read the download manifest written by ``scripts/fetch_data.py``."""
    path = data_dir.parent / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------
def aggregate_power_kw(volume_kwh: pd.Series, occupancy: pd.Series) -> float:
    """Mean delivered power over a group, as **total energy / total occupied-hours**.

    This is deliberately *not* the mean of the per-row ``volume/occupancy``
    ratios. Per-row ratios are extremely heavy-tailed -- one hourly occupancy
    glitch produces a thousands-of-kW sample -- so their mean is dominated by
    outliers, while the ratio of sums is stable and is what the physical
    question actually asks: over this period, how much power did an occupied
    pile deliver on average?

    The same trap appeared in the synthetic pipeline, where a ratio of averages
    and an average of ratios gave 8.9x and 6.0x for the same finding.
    """
    occ_sum = float(occupancy.sum())
    if occ_sum <= 0:
        return float("nan")
    return float(volume_kwh.sum()) / occ_sum


def quality_report(df: pd.DataFrame) -> dict[str, float | int]:
    """Quantify the data problems that the analyses have to work around.

    Reported rather than silently cleaned: a reviewer needs to know how much of
    the data was excluded and why, otherwise the headline averages look more
    solid than they are.
    """
    total = len(df)
    low_occ = int((df["occupancy"] < MIN_OCCUPANCY_FOR_POWER).sum())
    implausible = int(df["power_implausible"].sum())
    zero_volume = int((df["volume_kwh"] == 0).sum())
    idle_hours = int((df["occupancy"] == 0).sum())
    return {
        "rows": total,
        "rows_below_min_occupancy": low_occ,
        "pct_below_min_occupancy": 100.0 * low_occ / total if total else float("nan"),
        "rows_implausible_power": implausible,
        "pct_implausible_power": 100.0 * implausible / total if total else float("nan"),
        "rows_zero_volume": zero_volume,
        "rows_fully_idle": idle_hours,
    }


__all__ = [
    "DATASET",
    "DatasetSummary",
    "RealDataError",
    "aggregate_power_kw",
    "dataset_manifest",
    "load_station_meta",
    "load_weather",
    "load_wide",
    "load_zone_hourly",
    "quality_report",
    "summarise",
    "validate",
]

DATASET = {
    "name": "UrbanEV (Shenzhen public charging)",
    "repo": "https://github.com/IntelligentSystemsLab/UrbanEV",
    "paper": "UrbanEV: An Open Benchmark Dataset for Urban Electric Vehicle "
             "Charging Demand Prediction, Scientific Data (2025)",
    "piles": 24798,
    "period": "2022-09-01 onward, hourly, ~6 months",
}
