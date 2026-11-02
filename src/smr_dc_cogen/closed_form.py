"""Closed-form evaluation of Cases 0 and 3 (article Section 2.2).

Neither case has a dispatch choice: in Case 0 every hour's load is bought from the grid, and the islanded
Case 3 must follow load with its single gas plant. The chiller carries the whole cooling demand of Eq. (1)
and its electricity is the part-load map f_v evaluated directly at the realized load.
"""

from __future__ import annotations

import numpy as np

from .cases import case_spec, check_blocks
from .parameters import Parameters, ParameterError
from .physics import chiller_power, cooling_demand, gas_heat_rate_multiplier
from .results import CaseResult, account, empty_hourly
from .timeseries import TimeSeries


def _cooling(params: Parameters, ts: TimeSeries, h) -> np.ndarray:
    q = cooling_demand(ts.it_load_mw, params.site)
    if q.max() > params.chiller.nameplate_mwc * (1 + 1e-9):
        raise ParameterError(f"cooling demand peaks at {q.max():.3f} MW_c, above the chiller nameplate "
                             f"{params.chiller.nameplate_mwc} MW_c")
    p = chiller_power(q, ts.wet_bulb_c, params.chiller)
    h["cooling_demand_mwc"] = q
    h["chiller_mwc"] = q
    h["chiller_mw"] = p
    return p


def evaluate_case0(params: Parameters, ts: TimeSeries, label: str = "") -> CaseResult:
    """Case 0: grid imports P_g+(t) = P_IT(t) + P_v(t); the VCC carries all cooling."""
    spec = case_spec(0)
    check_blocks(spec, params, battery=False)
    h = empty_hourly(ts)
    p_v = _cooling(params, ts, h)
    imports = ts.it_load_mw + p_v
    if imports.max() > params.grid.pcc_mw * (1 + 1e-9):
        raise ParameterError(f"grid import peaks at {imports.max():.3f} MW, above the PCC capacity "
                             f"{params.grid.pcc_mw} MW")
    h["grid_import_mw"] = imports
    return account(spec, params, ts, h, battery=False, label=label)


def evaluate_case3(params: Parameters, ts: TimeSeries, label: str = "") -> CaseResult:
    """Case 3: the gas plant serves P_ng(t) = P_IT(t) + P_v(t) off-grid, at load fraction l(t) = P_ng / P_ng_max;
    fuel and emissions scale with phi_ng(l) (Eqs. S13, S14)."""
    spec = case_spec(3)
    check_blocks(spec, params, battery=False)
    if ts.gas_usd_per_mmbtu is None:
        raise ParameterError("Case 3 needs the gas price series (gas_usd_per_mmbtu)")
    gp = params.gas_plant
    h = empty_hourly(ts)
    p_v = _cooling(params, ts, h)
    p_ng = ts.it_load_mw + p_v
    if p_ng.max() > gp.rating_mwe * (1 + 1e-9):
        raise ParameterError(f"gas-plant output peaks at {p_ng.max():.3f} MW, above its rating {gp.rating_mwe} MW")
    h["gas_plant_mw"] = p_ng
    h["gas_heat_rate_multiplier"] = gas_heat_rate_multiplier(p_ng / gp.rating_mwe, gp)
    h["gas_delivered_usd_per_mmbtu"] = ts.gas_usd_per_mmbtu + gp.gas_basis_usd_per_mmbtu
    return account(spec, params, ts, h, battery=False, label=label)


__all__ = ["evaluate_case0", "evaluate_case3"]
