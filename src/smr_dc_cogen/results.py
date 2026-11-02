"""Result of one case: hourly dispatch, the seven cost buckets of Eq. (9), annual totals and balance checks.

Costs are computed from the hourly dispatch by one function, :func:`account`, for the closed-form and the
optimized cases alike, so every case is reported on the same accounting basis. For optimized cases the result
also keeps the solver's objective; ``tests/`` check that both agree.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

from .cases import CaseSpec, asset_sizes, fixed_costs
from .parameters import THETA_T_PER_KG, Parameters
from .timeseries import TimeSeries

#: hourly columns of every result (zero where a block is absent)
HOURLY_COLUMNS = [
    "it_load_mw", "cooling_demand_mwc",
    "reactor_mwth", "turbine_gross_mw", "turbine_net_mw", "extraction_mwth",
    "absorber_mwc", "absorber_cop", "absorber_available", "absorber_parasitic_mw",
    "chiller_mwc", "chiller_mw",
    "grid_import_mw", "grid_export_mw",
    "battery_charge_mw", "battery_discharge_mw", "battery_soc_mwh",
    "gas_plant_mw", "gas_heat_rate_multiplier",
    "price_usd_per_mwh", "carbon_g_per_kwh", "wet_bulb_c", "gas_delivered_usd_per_mmbtu", "outage",
]

COST_BUCKETS = ["capex", "fom", "vom", "fuel", "grid", "carbon", "ptc"]


@dataclass
class CaseResult:
    """Outcome of one case. ``costs`` are signed contributions to the total annualized cost (TAC), $/yr:
    revenues and credits (net export revenue, the 45Y credit, a negative footprint under a carbon price) are
    negative. ``costs["tac"]`` is their sum."""
    case: int
    name: str
    label: str
    hourly: pd.DataFrame
    costs: Dict[str, float]
    totals: Dict[str, float]
    sizes: Dict[str, float]
    assets: Dict[str, Dict[str, float]]
    battery: bool = False
    solver: Optional[Dict[str, object]] = None
    meta: Dict[str, object] = field(default_factory=dict)

    @property
    def tac(self) -> float:
        return self.costs["tac"]

    def summary(self) -> Dict[str, object]:
        """Flat dictionary of the scalar results (strict-JSON safe: NaN becomes None)."""
        def clean(v):
            if isinstance(v, float) and not math.isfinite(v):
                return None
            if isinstance(v, (np.floating, np.integer)):
                return clean(v.item())
            return v
        out: Dict[str, object] = {"case": self.case, "name": self.name, "label": self.label, "battery": self.battery}
        out.update({f"cost_{k}_usd_per_yr": clean(v) for k, v in self.costs.items()})
        out.update({k: clean(v) for k, v in self.totals.items()})
        out.update({f"size_{k}": clean(v) for k, v in self.sizes.items()})
        if self.solver:
            out.update({f"solver_{k}": clean(v) for k, v in self.solver.items()})
        out.update({k: clean(v) for k, v in self.meta.items()})
        return out

    def to_files(self, out_dir, hourly: bool = True) -> None:
        """Write ``summary_case<N>.json`` and, optionally, ``hourly_case<N>.csv``."""
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        tag = f"case{self.case}" + ("_battery" if self.battery else "")
        (d / f"summary_{tag}.json").write_text(json.dumps(self.summary(), indent=2, allow_nan=False) + "\n")
        if hourly:
            self.hourly.to_csv(d / f"hourly_{tag}.csv", index_label="hour")


def empty_hourly(ts: TimeSeries) -> pd.DataFrame:
    df = pd.DataFrame(0.0, index=pd.RangeIndex(ts.hours, name="hour"), columns=HOURLY_COLUMNS)
    df["it_load_mw"] = ts.it_load_mw
    df["price_usd_per_mwh"] = ts.price_usd_per_mwh
    df["carbon_g_per_kwh"] = ts.carbon_g_per_kwh
    df["wet_bulb_c"] = ts.wet_bulb_c
    df["outage"] = False
    return df


def account(spec: CaseSpec, params: Parameters, ts: TimeSeries, hourly: pd.DataFrame, battery: bool,
            label: str = "") -> CaseResult:
    """Cost buckets of Eq. (9) with Eqs. (10), (11), (12), (S11)-(S14) and annual totals, from hourly dispatch."""
    w = ts.weight
    h = hourly
    fin = params.finance

    assets = fixed_costs(spec, params, battery)
    capex = sum(a["capex"] for a in assets.values())
    fom = sum(a["fom"] for a in assets.values())

    vom_rate_net = 0.0
    if spec.reactor:
        vom_rate_net = params.reactor.vom_usd_per_mwh + params.turbine.vom_usd_per_mwh
    vom = w * float(np.sum(vom_rate_net * h["turbine_net_mw"] + params.chiller.vom_usd_per_mwhc * h["chiller_mwc"]))
    if spec.absorber:
        vom += w * float(np.sum(params.absorber.vom_usd_per_mwhc * h["absorber_mwc"]))
    if battery:
        vom += w * float(np.sum(params.battery.vom_usd_per_mwh * (h["battery_charge_mw"] + h["battery_discharge_mw"])))
    if spec.gas_plant:
        vom += w * float(np.sum(params.gas_plant.vom_usd_per_mwh * h["gas_plant_mw"]))

    fuel = 0.0
    if spec.reactor:
        fuel += w * params.fuel_usd_per_mwh_th * float(np.sum(h["reactor_mwth"]))                       # Eq. (S12)
    if spec.gas_plant:
        gp = params.gas_plant
        fuel += w * float(np.sum(h["gas_delivered_usd_per_mmbtu"] * gp.heat_rate_mmbtu_per_mwh
                                 * h["gas_heat_rate_multiplier"] * h["gas_plant_mw"]))                  # Eq. (S13)

    net_grid = h["grid_import_mw"] - h["grid_export_mw"]
    grid = w * float(np.sum(h["price_usd_per_mwh"] * net_grid))                                         # Eq. (10)

    footprint_kg = float(np.sum(h["carbon_g_per_kwh"] * net_grid))                                      # g/kWh = kg/MWh
    if spec.reactor:
        footprint_kg += params.reactor.lifecycle_g_per_kwh * float(np.sum(h["turbine_net_mw"]))
    if spec.gas_plant:
        footprint_kg += params.gas_plant.lifecycle_g_per_kwh * float(
            np.sum(h["gas_heat_rate_multiplier"] * h["gas_plant_mw"]))
    footprint_t = w * THETA_T_PER_KG * footprint_kg
    carbon = fin.carbon_price_usd_per_t * footprint_t                                                   # Eqs. (11), (S14)

    ptc_rate = 0.0
    if spec.reactor and params.reactor.ptc_eligible:
        ptc_rate = fin.levelized_ptc_usd_per_mwh                                                         # Eq. (12)
    ptc = -w * ptc_rate * float(np.sum(h["turbine_net_mw"]))

    costs = {"capex": capex, "fom": fom, "vom": vom, "fuel": fuel, "grid": grid, "carbon": carbon, "ptc": ptc}
    costs["tac"] = float(sum(costs.values()))

    def annual(col):
        return w * float(np.sum(h[col]))

    cool = annual("cooling_demand_mwc")
    totals = {
        "hours": ts.hours,
        "annualization_weight": w,
        "it_energy_mwh": annual("it_load_mw"),
        "cooling_demand_mwhc": cool,
        "absorber_cooling_mwhc": annual("absorber_mwc"),
        "chiller_cooling_mwhc": annual("chiller_mwc"),
        "absorber_share_of_cooling": annual("absorber_mwc") / cool if cool > 0 else float("nan"),
        "chiller_electricity_mwh": annual("chiller_mw"),
        "absorber_parasitic_mwh": annual("absorber_parasitic_mw"),
        "reactor_thermal_mwh": annual("reactor_mwth"),
        "extraction_mwh_th": annual("extraction_mwth"),
        "net_generation_mwh": annual("turbine_net_mw") + annual("gas_plant_mw"),
        "grid_import_mwh": annual("grid_import_mw"),
        "grid_export_mwh": annual("grid_export_mw"),
        "battery_charge_mwh": annual("battery_charge_mw"),
        "battery_discharge_mwh": annual("battery_discharge_mw"),
        "footprint_t_co2": footprint_t,
        "levelized_ptc_usd_per_mwh": ptc_rate,
        "battery_simultaneous_hours": int(np.sum((h["battery_charge_mw"] > 1e-6) & (h["battery_discharge_mw"] > 1e-6))),
    }
    return CaseResult(case=spec.number, name=spec.name, label=label or ts.label, hourly=h, costs=costs,
                      totals=totals, sizes=asset_sizes(spec, params, battery), assets=assets, battery=battery)


def _mx(values) -> float:
    arr = np.asarray(values, dtype=float)
    return float(np.max(arr)) if arr.size else 0.0


def balance_residuals(result: CaseResult, params: Parameters) -> Dict[str, float]:
    """Largest absolute residual of each equation, recomputed from the hourly result (MW or MWh).

    Covers Eqs. (2), (3), (6), (7), (8), the cooling balance, (S7)-(S10) and the cyclic battery end state.
    Values near zero (solver tolerance) mean the dispatch satisfies the formulation.
    """
    h = {c: result.hourly[c].to_numpy(dtype=float) for c in HOURLY_COLUMNS if c != "outage"}
    outage = result.hourly["outage"].to_numpy(dtype=bool)
    online = ~outage
    out: Dict[str, float] = {}
    out["cooling_balance"] = _mx(np.abs(h["absorber_mwc"] + h["chiller_mwc"] - h["cooling_demand_mwc"]))
    out["eq8_electric_balance"] = _mx(np.abs(
        h["turbine_net_mw"] + h["grid_import_mw"] + h["battery_discharge_mw"] + h["gas_plant_mw"]
        - h["it_load_mw"] - h["chiller_mw"] - h["absorber_parasitic_mw"] - h["grid_export_mw"]
        - h["battery_charge_mw"]))
    out["eq7_import_export_overlap"] = _mx(np.minimum(h["grid_import_mw"], h["grid_export_mw"]))
    if result.sizes.get("reactor_mwe"):
        tb, rx = params.turbine, params.reactor
        willans = (tb.willans_slope * h["reactor_mwth"] - tb.willans_intercept_mw
                   - tb.extraction_penalty * h["extraction_mwth"])
        out["eq2_willans"] = _mx(np.abs(h["turbine_gross_mw"] - willans)[online])
        out["eq2_outage_zero"] = _mx(np.abs(h["turbine_gross_mw"])[outage])
        out["eq3_auxiliary"] = _mx(np.abs(h["turbine_net_mw"] - (1 - tb.aux_fraction) * h["turbine_gross_mw"]))
        pmin = rx.min_load_fraction * rx.thermal_mw
        out["s7_reactor_bounds"] = max(_mx(np.maximum(pmin - h["reactor_mwth"], 0)[online]),
                                       _mx(np.maximum(h["reactor_mwth"] - rx.thermal_mw, 0)))
        out["s8_outage"] = _mx(np.abs(h["reactor_mwth"])[outage])
        both = online[1:] & online[:-1]
        out["s9_ramp"] = _mx(np.maximum(np.abs(np.diff(h["reactor_mwth"])) - rx.ramp_mw_per_h, 0)[both])
    if result.sizes.get("absorber_mwc"):
        out["eq6_absorber_yield"] = _mx(np.abs(
            h["absorber_mwc"] - h["absorber_cop"] * h["absorber_available"] * h["extraction_mwth"]))
    if result.battery:
        bt = params.battery
        e0 = bt.initial_soc_fraction * bt.energy_mwh
        soc = h["battery_soc_mwh"]
        prev = np.concatenate([[e0], soc[:-1]])
        s = math.sqrt(bt.round_trip_efficiency)
        dyn = soc - (1 - bt.self_discharge_per_h) * prev - (s * h["battery_charge_mw"] - h["battery_discharge_mw"] / s)
        out["s10_soc_dynamics"] = _mx(np.abs(dyn))
        out["s10_cyclic_end_state"] = float(abs(soc[-1] - e0))
    return out


def cost_table(results: Iterable[CaseResult], unit: float = 1e6) -> pd.DataFrame:
    """Cost buckets of several results side by side (default in million $/yr)."""
    cols = {}
    for r in results:
        key = f"case{r.case}" + ("+battery" if r.battery else "") + (f" [{r.label}]" if r.label else "")
        cols[key] = {k: v / unit for k, v in r.costs.items()}
    return pd.DataFrame(cols).loc[COST_BUCKETS + ["tac"]]


def bucket_difference(a: CaseResult, b: CaseResult) -> Dict[str, float]:
    """Per-bucket difference a - b of two results ($/yr), e.g. Case 2 minus Case 1."""
    return {k: a.costs[k] - b.costs[k] for k in COST_BUCKETS + ["tac"]}


def strict_json(data: Mapping[str, object]) -> str:
    """JSON text with NaN and infinities written as null."""
    def clean(v):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        if isinstance(v, (np.floating, np.integer)):
            return clean(v.item())
        return v
    return json.dumps(clean(dict(data)), indent=2, allow_nan=False)


__all__ = ["HOURLY_COLUMNS", "COST_BUCKETS", "CaseResult", "empty_hourly", "account", "balance_residuals",
           "cost_table", "bucket_difference", "strict_json"]
