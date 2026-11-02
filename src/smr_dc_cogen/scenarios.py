"""Scenario sets: parameter changes, the data-center size rule, batch runs and matched grid-cost margins.

A :class:`Scenario` names a case, an input series (for example an operating year) and parameter changes.
:func:`run_scenarios` evaluates a list of scenarios and :func:`add_margins` adds the grid-cost margin of Eq. (13)
against the Case 0 row of the same matching group (same series, carbon price and size, unless chosen otherwise).
docs/scenarios.md maps the article's scenario groups onto these tools.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .analysis import grid_cost_margin
from .parameters import Parameters, ParameterError, with_changes
from .results import COST_BUCKETS, CaseResult
from .solve import run_case
from .timeseries import TimeSeries


def scale_data_center(params: Parameters, ts: TimeSeries, multiplier: float) -> Tuple[Parameters, TimeSeries]:
    """Size rule of the data-center size sweep: the IT trace and the data-center-side capacities (chiller,
    absorber, gas plant) scale with ``multiplier``; the reactor stays fixed; the PCC scales only upward
    (multiplier > 1) and keeps its value below 1."""
    if not (np.isfinite(multiplier) and multiplier > 0):
        raise ParameterError("multiplier must be positive and finite")
    changes: Dict[str, Any] = {"chiller.nameplate_mwc": params.chiller.nameplate_mwc * multiplier}
    if params.absorber is not None:
        changes["absorber.nameplate_mwc"] = params.absorber.nameplate_mwc * multiplier
    if params.gas_plant is not None:
        changes["gas_plant.rating_mwe"] = params.gas_plant.rating_mwe * multiplier
    if params.grid is not None and multiplier > 1:
        changes["grid.pcc_mw"] = params.grid.pcc_mw * multiplier
    return with_changes(params, changes), ts.scaled_load(multiplier)


@dataclass(frozen=True)
class Scenario:
    """One run: ``case`` on input series ``series`` with parameter ``changes`` ({"block.field": value})."""
    name: str
    case: int
    series: str
    changes: Mapping[str, Any] = field(default_factory=dict)
    battery: bool = False
    size: float = 1.0
    group: str = ""


def run_scenarios(scenarios: Iterable[Scenario], params: Parameters, series: Mapping[str, TimeSeries],
                  solver: str = "highs", options: Optional[Mapping[str, object]] = None,
                  progress: Optional[Callable[[str], None]] = None) -> Tuple[pd.DataFrame, Dict[str, CaseResult]]:
    """Evaluate every scenario; return one table row per scenario (costs in $/yr) and the results by name."""
    rows: List[Dict[str, Any]] = []
    results: Dict[str, CaseResult] = {}
    for sc in scenarios:
        if sc.name in results:
            raise ParameterError(f"duplicate scenario name {sc.name!r}")
        if sc.series not in series:
            raise ParameterError(f"scenario {sc.name!r}: unknown series {sc.series!r}")
        start = time.perf_counter()
        p = with_changes(params, sc.changes) if sc.changes else params
        ts = series[sc.series]
        if sc.size != 1.0:
            p, ts = scale_data_center(p, ts, sc.size)
        res = run_case(sc.case, p, ts, battery=sc.battery, solver=solver, options=options, label=sc.series)
        results[sc.name] = res
        row = {"name": sc.name, "group": sc.group, "case": sc.case, "series": sc.series, "battery": sc.battery,
               "size": sc.size, "carbon_price": p.finance.carbon_price_usd_per_t,
               "changes": ";".join(f"{k}={v}" for k, v in sorted(sc.changes.items()))}
        row.update({k: res.costs[k] for k in COST_BUCKETS + ["tac"]})
        row.update({"net_generation_mwh": res.totals["net_generation_mwh"],
                    "grid_import_mwh": res.totals["grid_import_mwh"], "grid_export_mwh": res.totals["grid_export_mwh"],
                    "absorber_share_of_cooling": res.totals["absorber_share_of_cooling"],
                    "footprint_t_co2": res.totals["footprint_t_co2"],
                    "seconds": round(time.perf_counter() - start, 3)})
        rows.append(row)
        if progress:
            progress(f"{sc.name}: TAC {res.tac / 1e6:.3f} M$/yr ({row['seconds']} s)")
    return pd.DataFrame(rows), results


def add_margins(table: pd.DataFrame, match_on: Sequence[str] = ("series", "carbon_price", "size"),
                reference: Optional[Mapping[str, float]] = None) -> pd.DataFrame:
    """Add ``margin`` (Eq. 13) against the Case 0 row with the same ``match_on`` values.

    ``reference`` instead gives the grid-only TAC per value of a single matching column (for a fixed baseline,
    e.g. margins of a cooling-efficiency sweep against one conventionally cooled grid-only facility). Rows
    without a matching Case 0, or whose grid-only TAC is not positive (margin undefined), get NaN.
    """
    out = table.copy()
    if reference is not None:
        if len(match_on) != 1:
            raise ValueError("with a reference mapping, match on exactly one column")
        col = match_on[0]
        out["tac_grid_only"] = out[col].map(reference)
    else:
        base = out[(out["case"] == 0) & (~out["battery"])]
        if base.duplicated(list(match_on)).any():
            raise ValueError(f"several Case 0 rows share the same {list(match_on)}")
        key = base.set_index(list(match_on))["tac"]
        out["tac_grid_only"] = [key.get(tuple(r) if len(match_on) > 1 else r[0], np.nan)
                                for r in out[list(match_on)].itertuples(index=False, name=None)]
    out["margin"] = [grid_cost_margin(t, g) if np.isfinite(g) and g > 0 else np.nan
                     for t, g in zip(out["tac"], out["tac_grid_only"])]
    return out


__all__ = ["scale_data_center", "Scenario", "run_scenarios", "add_margins"]
