"""Input directory format (docs/data_pipeline.md):

    <dir>/timeseries.csv    hour, it_load_mw, price_usd_per_mwh, carbon_g_per_kwh, wet_bulb_c[, gas_usd_per_mmbtu]
    <dir>/parameters.json   nested blocks, see parameters.to_dict
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

import pandas as pd

from .parameters import Parameters, ParameterError, from_dict, to_dict
from .timeseries import SeriesError, TimeSeries


def write_inputs(directory, params: Parameters, ts: TimeSeries) -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    ts.to_frame().to_csv(d / "timeseries.csv", index=False)
    (d / "parameters.json").write_text(json.dumps(to_dict(params), indent=2, allow_nan=False) + "\n")
    return d


def read_inputs(directory, label: str = "") -> Tuple[Parameters, TimeSeries]:
    """Read and validate an input directory. Raises ParameterError or SeriesError with the file name."""
    d = Path(directory)
    ts_path, p_path = d / "timeseries.csv", d / "parameters.json"
    for p in (ts_path, p_path):
        if not p.is_file():
            raise FileNotFoundError(f"missing input file {p}")
    try:
        ts = TimeSeries.from_frame(pd.read_csv(ts_path), label=label or d.name)
    except (SeriesError, ValueError) as exc:
        raise SeriesError(f"{ts_path.name}: {exc}") from exc
    try:
        data = json.loads(p_path.read_text(), parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ParameterError(f"{p_path.name}: not valid JSON ({exc})") from exc
    try:
        params = from_dict(data)
    except ParameterError as exc:
        raise ParameterError(f"{p_path.name}: {exc}") from exc
    return params, ts


def _reject_constant(name: str):
    raise ParameterError(f"parameters.json: {name} is not allowed")


__all__ = ["write_inputs", "read_inputs"]
