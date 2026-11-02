"""Hourly input series and the generic preparation steps of the data pipeline (docs/data_pipeline.md).

A :class:`TimeSeries` holds one operating year (or a shorter contiguous block) of aligned hourly inputs:
IT load P_IT(t), electricity price pi_g(t), average grid carbon intensity theta(t), wet-bulb temperature
T_wb(t) and, for the gas case, the hub gas price. The preparation helpers turn public raw records into
these series.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, replace
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .parameters import HOURS_PER_YEAR


class SeriesError(ValueError):
    """An input series is missing, misaligned or contains invalid values."""


def _as_array(name: str, values, allow_negative: bool = True) -> np.ndarray:
    arr = np.asarray(values, dtype=float).reshape(-1)
    if arr.size == 0:
        raise SeriesError(f"{name} is empty")
    if not np.all(np.isfinite(arr)):
        raise SeriesError(f"{name} contains NaN or infinite values")
    if not allow_negative and np.any(arr < 0):
        raise SeriesError(f"{name} contains negative values")
    return arr


@dataclass(frozen=True)
class TimeSeries:
    """Aligned hourly inputs; all arrays have the same length (hours of the horizon)."""
    it_load_mw: np.ndarray            # P_IT(t), MW_e
    price_usd_per_mwh: np.ndarray     # pi_g(t), $/MWh_e (may be negative)
    carbon_g_per_kwh: np.ndarray      # theta(t), g CO2/kWh_e
    wet_bulb_c: np.ndarray            # T_wb(t), degC
    gas_usd_per_mmbtu: Optional[np.ndarray] = None  # hub gas price, $/MMBtu (Case 3 only)
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "it_load_mw", _as_array("it_load_mw", self.it_load_mw, allow_negative=False))
        object.__setattr__(self, "price_usd_per_mwh", _as_array("price_usd_per_mwh", self.price_usd_per_mwh))
        object.__setattr__(self, "carbon_g_per_kwh",
                           _as_array("carbon_g_per_kwh", self.carbon_g_per_kwh, allow_negative=False))
        object.__setattr__(self, "wet_bulb_c", _as_array("wet_bulb_c", self.wet_bulb_c))
        if self.gas_usd_per_mmbtu is not None:
            object.__setattr__(self, "gas_usd_per_mmbtu", _as_array("gas_usd_per_mmbtu", self.gas_usd_per_mmbtu))
        n = self.it_load_mw.size
        for name in ("price_usd_per_mwh", "carbon_g_per_kwh", "wet_bulb_c", "gas_usd_per_mmbtu"):
            arr = getattr(self, name)
            if arr is not None and arr.size != n:
                raise SeriesError(f"{name} has {arr.size} values, it_load_mw has {n}")
        if n > HOURS_PER_YEAR:
            raise SeriesError(f"at most {HOURS_PER_YEAR} hours, got {n} (trim leap-year records first)")
        for arr in (self.it_load_mw, self.price_usd_per_mwh, self.carbon_g_per_kwh, self.wet_bulb_c,
                    self.gas_usd_per_mmbtu):
            if arr is not None:
                arr.setflags(write=False)

    @property
    def hours(self) -> int:
        return int(self.it_load_mw.size)

    @property
    def weight(self) -> float:
        """Annualization weight w = 8760 / |T| applied to every hourly sum (1 for a full year)."""
        return HOURS_PER_YEAR / self.hours

    def scaled_load(self, multiplier: float) -> "TimeSeries":
        """The same series with the IT load multiplied by ``multiplier`` (size sweep)."""
        if not (math.isfinite(multiplier) and multiplier > 0):
            raise SeriesError("multiplier must be positive and finite")
        return replace(self, it_load_mw=self.it_load_mw * multiplier)

    def window(self, start: int, hours: int) -> "TimeSeries":
        """A contiguous block of ``hours`` hours starting at ``start``."""
        if start < 0 or hours <= 0 or start + hours > self.hours:
            raise SeriesError(f"window [{start}, {start + hours}) outside 0..{self.hours}")
        sl = slice(start, start + hours)
        gas = None if self.gas_usd_per_mmbtu is None else self.gas_usd_per_mmbtu[sl]
        return TimeSeries(self.it_load_mw[sl], self.price_usd_per_mwh[sl], self.carbon_g_per_kwh[sl],
                          self.wet_bulb_c[sl], gas, label=self.label)

    def to_frame(self) -> pd.DataFrame:
        df = pd.DataFrame({
            "hour": np.arange(self.hours),
            "it_load_mw": self.it_load_mw,
            "price_usd_per_mwh": self.price_usd_per_mwh,
            "carbon_g_per_kwh": self.carbon_g_per_kwh,
            "wet_bulb_c": self.wet_bulb_c,
        })
        if self.gas_usd_per_mmbtu is not None:
            df["gas_usd_per_mmbtu"] = self.gas_usd_per_mmbtu
        return df

    @staticmethod
    def from_frame(df: pd.DataFrame, label: str = "") -> "TimeSeries":
        required = ["hour", "it_load_mw", "price_usd_per_mwh", "carbon_g_per_kwh", "wet_bulb_c"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise SeriesError(f"missing column(s): {missing}")
        unknown = set(df.columns) - set(required) - {"gas_usd_per_mmbtu"}
        if unknown:
            raise SeriesError(f"unknown column(s): {sorted(unknown)}")
        hours = pd.to_numeric(df["hour"], errors="coerce").to_numpy()
        if not np.array_equal(hours, np.arange(len(df))):
            raise SeriesError("column 'hour' must run 0, 1, 2, ... without gaps")

        def col(name):
            values = pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=float)
            if np.isnan(values).any():
                raise SeriesError(f"column {name!r} has empty or non-numeric values")
            return values

        gas = col("gas_usd_per_mmbtu") if "gas_usd_per_mmbtu" in df.columns else None
        return TimeSeries(col("it_load_mw"), col("price_usd_per_mwh"), col("carbon_g_per_kwh"), col("wet_bulb_c"),
                          gas, label=label)


# ----------------------------------------------------------------------------- preparation helpers

def trim_to_year(values: Sequence[float], hours: int = HOURS_PER_YEAR) -> np.ndarray:
    """Keep the first ``hours`` records of an hourly year (a leap year loses its last day)."""
    arr = np.asarray(values, dtype=float).reshape(-1)
    if arr.size < hours:
        raise SeriesError(f"need at least {hours} records, got {arr.size}")
    return arr[:hours].copy()


def align_weekdays(trace: Sequence[float], trace_year: int, target_year: int) -> np.ndarray:
    """Circularly shift an hourly trace of ``trace_year`` by whole days so that day of week and hour of day line
    up with ``target_year`` (Section 2.1). Hour i of the result takes the trace hour of the same weekday."""
    arr = np.asarray(trace, dtype=float).reshape(-1)
    if arr.size % 24:
        raise SeriesError("the trace must contain whole days")
    offset = (_dt.date(target_year, 1, 1).weekday() - _dt.date(trace_year, 1, 1).weekday()) % 7
    return np.roll(arr, -24 * offset)


def resample_hourly_mean(values: pd.Series) -> pd.Series:
    """Mean of each clock hour of a sub-hourly series indexed by timestamps."""
    if not isinstance(values.index, pd.DatetimeIndex):
        raise SeriesError("a DatetimeIndex is required")
    return values.resample("1h").mean()


def scale_to_peak(values: Sequence[float], peak_mw: float) -> np.ndarray:
    """Scale a load shape so that its maximum equals ``peak_mw``."""
    arr = np.asarray(values, dtype=float)
    if arr.max() <= 0:
        raise SeriesError("the shape has no positive value")
    return arr * (peak_mw / arr.max())


def daily_to_hourly(daily: pd.Series, year: int, hours: int = HOURS_PER_YEAR) -> np.ndarray:
    """Broadcast daily prices (indexed by date; trading days only) to hours of ``year``.

    Days without a quote (weekends, holidays) take the most recent earlier quote, including quotes from before the
    year; days before the first available quote take the first quote. Each day's value is repeated 24 times and the
    result is cut to ``hours``.
    """
    idx = pd.to_datetime(daily.index)
    s = pd.Series(np.asarray(daily, dtype=float), index=idx).sort_index()
    if s.index.has_duplicates:
        raise SeriesError("duplicate dates in the daily series")
    if not np.all(np.isfinite(s.to_numpy())):
        raise SeriesError("the daily series has missing or non-finite values")
    start, end = pd.Timestamp(f"{year}-01-01"), pd.Timestamp(f"{year}-12-31")
    if s.empty or s.index.min() > end:
        raise SeriesError(f"no daily value in or before {year}")
    full = pd.date_range(min(s.index.min(), start), end, freq="D")
    filled = s.reindex(full).ffill().bfill().loc[start:end]
    return np.repeat(filled.to_numpy(), 24)[:hours]


def wet_bulb_stull(temperature_c, relative_humidity_pct):
    """Wet-bulb temperature from dry-bulb temperature and relative humidity (Stull 2011, J. Appl. Meteor.
    Climatol. 50, 2267-2269), valid near sea level for RH 5-99 % and T -20 to 50 degC."""
    t = np.asarray(temperature_c, dtype=float)
    rh = np.asarray(relative_humidity_pct, dtype=float)
    if np.any((rh < 0) | (rh > 100)):
        raise SeriesError("relative humidity must be within 0-100 %")
    return (t * np.arctan(0.151977 * np.sqrt(rh + 8.313659)) + np.arctan(t + rh) - np.arctan(rh - 1.676331)
            + 0.00391838 * rh ** 1.5 * np.arctan(0.023101 * rh) - 4.686035)


def average_carbon_intensity(generation_mw: pd.DataFrame, factors_g_per_kwh: Mapping[str, float]) -> np.ndarray:
    """Hourly average emission factor of a generation mix: sum_f gen_f EF_f / sum_f gen_f (g CO2/kWh).

    ``generation_mw`` has one column per fuel; every column needs a factor. Negative values (net consumption,
    e.g. storage charging) are treated as zero generation.
    """
    missing = [c for c in generation_mw.columns if c not in factors_g_per_kwh]
    if missing:
        raise SeriesError(f"no emission factor for: {missing}")
    ef = pd.Series({c: float(factors_g_per_kwh[c]) for c in generation_mw.columns})
    if not np.all(np.isfinite(ef.to_numpy())) or (ef < 0).any():
        raise SeriesError("emission factors must be finite and non-negative")
    raw = generation_mw.astype(float)
    if not np.all(np.isfinite(raw.to_numpy())):
        raise SeriesError("the generation mix has missing or non-finite values; fill them first")
    gen = raw.clip(lower=0.0)
    total = gen.sum(axis=1)
    if (total <= 0).any():
        raise SeriesError("an hour has no positive generation")
    return (gen.mul(ef, axis=1).sum(axis=1) / total).to_numpy()


def fit_willans(thermal_mw: Sequence[float], gross_mw: Sequence[float], design_thermal_mw: float,
                design_gross_mw: float):
    """Least-squares Willans line P_tg = a P_rx - b through the design point (Section 2.2.1).

    Returns ``(a, b, r2)``. The constraint a * design_thermal - b = design_gross is imposed exactly.
    """
    x = np.asarray(thermal_mw, dtype=float) - design_thermal_mw
    y = np.asarray(gross_mw, dtype=float) - design_gross_mw
    if x.size < 2 or not np.any(x != 0):
        raise SeriesError("need part-load points away from the design point")
    a = float(np.dot(x, y) / np.dot(x, x))
    b = a * design_thermal_mw - design_gross_mw
    pred = a * np.asarray(thermal_mw, dtype=float) - b
    obs = np.asarray(gross_mw, dtype=float)
    ss_res = float(np.sum((obs - pred) ** 2))
    ss_tot = float(np.sum((obs - obs.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return a, b, r2


def heat_rate_to_thermal(gross_mw: Sequence[float], heat_rate_kj_per_kwh: Sequence[float]) -> np.ndarray:
    """Thermal input (MW_th) from gross output and gross heat rate (kJ/kWh): P_rx = P_tg HR / 3600."""
    return np.asarray(gross_mw, dtype=float) * np.asarray(heat_rate_kj_per_kwh, dtype=float) / 3600.0


__all__ = ["SeriesError", "TimeSeries", "trim_to_year", "align_weekdays", "resample_hourly_mean", "scale_to_peak",
           "daily_to_hourly", "wet_bulb_stull", "average_carbon_intensity", "fit_willans", "heat_rate_to_thermal"]
