"""Hourly dispatch model of Cases 1 and 2 (and, for checking, Case 0) as a linopy mixed-integer linear program.

Constraint names carry the article's equation numbers (docs/formulation.md). Dimensions: ``t`` (hour) and ``k``
(segment of the chiller part-load map).

Integer structure, as in the article:
  * the chiller part-load map f_v is non-convex and is represented with SOS2 logic; here it is written with the
    incremental (delta) formulation and one binary per segment boundary and hour, which is an exact MILP
    equivalent that any MILP solver accepts;
  * the hourly import-export interlock y(t) of Eq. (7).
No unit-commitment binaries are used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import linopy
import numpy as np
import pandas as pd
import xarray as xr

from .cases import CaseSpec, case_spec, check_blocks, fixed_costs
from .parameters import THETA_T_PER_KG, Parameters, ParameterError
from .physics import (absorber_available, absorber_cop, chiller_breakpoints, chiller_relief, cooling_demand,
                      outage_mask)
from .timeseries import TimeSeries


@dataclass
class CaseModel:
    """A built (and, after :func:`smr_dc_cogen.solve.solve`, solved) dispatch model."""
    spec: CaseSpec
    params: Parameters
    ts: TimeSeries
    battery: bool
    model: linopy.Model
    data: Dict[str, np.ndarray]
    fixed_cost: float
    objective: float = float("nan")
    solver: Dict[str, object] = field(default_factory=dict)


def build_model(case: int, params: Parameters, ts: TimeSeries, battery: bool = False) -> CaseModel:
    """Build the dispatch MILP of ``case`` (0, 1 or 2) on the hours of ``ts``.

    The objective holds the dispatch-dependent part of the TAC of Eq. (9); the dispatch-neutral capital and
    fixed O&M are kept in ``CaseModel.fixed_cost``.
    """
    spec = case_spec(case)
    if spec.gas_plant:
        raise ParameterError("Case 3 has no dispatch choice; use evaluate_case3")
    check_blocks(spec, params, battery)
    H = ts.hours
    w = ts.weight
    t = pd.RangeIndex(H, name="t")

    def da(values) -> xr.DataArray:
        return xr.DataArray(np.asarray(values, dtype=float), coords=[t])

    m = linopy.Model()
    data: Dict[str, np.ndarray] = {}
    fin = params.finance
    cp_theta = fin.carbon_price_usd_per_t * THETA_T_PER_KG  # $/kg CO2

    q_dem = cooling_demand(ts.it_load_mw, params.site)                                           # Eq. (1)
    data["cooling_demand_mwc"] = q_dem
    out = outage_mask(params.outage, H) if spec.reactor else np.zeros(H, dtype=bool)
    data["outage"] = out
    online = ~out

    obj = 0
    q_a = p_tn = None

    # ---- reactor and turbine (Eqs. 2, 3, S7-S9) -------------------------------------------------------------
    if spec.reactor:
        rx, tb = params.reactor, params.turbine
        pmin = rx.min_load_fraction * rx.thermal_mw
        p_rx = m.add_variables(lower=da(np.where(out, 0.0, pmin)), upper=da(np.where(out, 0.0, rx.thermal_mw)),
                               name="P_rx")                                                       # (S7), (S8)
        if H > 1:
            both_online = np.zeros(H, dtype=bool)
            both_online[1:] = online[1:] & online[:-1]
            if both_online.any():
                ramp = p_rx - p_rx.roll(t=1)
                m.add_constraints(ramp <= rx.ramp_mw_per_h, name="S9_ramp_up", mask=xr.DataArray(both_online, coords=[t]))
                m.add_constraints(ramp >= -rx.ramp_mw_per_h, name="S9_ramp_down",
                                  mask=xr.DataArray(both_online, coords=[t]))
        p_tg = m.add_variables(lower=0.0, upper=da(np.where(out, 0.0, np.inf)), name="P_tg")   # P_tg = 0 in T_out
        willans = p_tg - tb.willans_slope * p_rx
        if spec.absorber:
            ab = params.absorber
            cop = absorber_cop(ts.wet_bulb_c, ab)                                                 # Eq. (4)
            avail = absorber_available(ts.wet_bulb_c, ab, out)                                    # Eq. (5)
            data["absorber_cop"], data["absorber_available"] = cop, avail
            q_ext = m.add_variables(lower=0.0, upper=da(ab.steam_capacity_mw * avail), name="Q_ext")
            q_a = m.add_variables(lower=0.0, upper=ab.nameplate_mwc, coords=[t], name="Q_a")
            m.add_constraints(q_a - da(cop * avail) * q_ext == 0, name="eq6_absorber_yield")       # Eq. (6)
            willans = willans + tb.extraction_penalty * q_ext
        m.add_constraints(willans == -tb.willans_intercept_mw, name="eq2_willans", mask=xr.DataArray(online, coords=[t]))
        p_tn = m.add_variables(lower=0.0, coords=[t], name="P_tn")
        m.add_constraints(p_tn - (1.0 - tb.aux_fraction) * p_tg == 0, name="eq3_auxiliary")       # Eq. (3)
        ptc = fin.levelized_ptc_usd_per_mwh if rx.ptc_eligible else 0.0                            # Eq. (12)
        # VOM (S11) + carbon (11) - credit, per MWh of net generation; fuel (S12) per MWh_th
        obj = obj + w * (rx.vom_usd_per_mwh + tb.vom_usd_per_mwh + cp_theta * rx.lifecycle_g_per_kwh - ptc) * p_tn.sum()
        obj = obj + w * params.fuel_usd_per_mwh_th * p_rx.sum()

    # ---- vapor-compression chiller: f_v with SOS2 logic (incremental formulation) --------------------------
    ch = params.chiller
    qk, pk = chiller_breakpoints(ch)
    relief = chiller_relief(ts.wet_bulb_c, ch)
    data["chiller_relief"] = relief
    K = len(qk) - 1
    k = pd.RangeIndex(K, name="k")
    dq = xr.DataArray(np.diff(qk), coords=[k])
    dp = xr.DataArray(np.diff(pk)[None, :] / relief[:, None], coords=[t, k])
    delta = m.add_variables(lower=0.0, upper=1.0, coords=[t, k], name="vcc_delta")
    q_v = m.add_variables(lower=0.0, upper=ch.nameplate_mwc, coords=[t], name="Q_v")
    p_v = m.add_variables(lower=0.0, coords=[t], name="P_v")
    m.add_constraints(q_v - (dq * delta).sum("k") == 0, name="vcc_load")
    m.add_constraints(p_v - (dp * delta).sum("k") == 0, name="vcc_power")
    if K > 1:
        # segment k+1 may be used only if segment k is full: delta[k+1] <= z[k] <= delta[k], z binary
        z = m.add_variables(coords=[t, k], name="vcc_z", binary=True)
        inner = xr.DataArray(np.tile(np.arange(K) < K - 1, (H, 1)), coords=[t, k])
        m.add_constraints(delta.roll(k=-1) - z <= 0, name="vcc_order_next", mask=inner)
        m.add_constraints(z - delta <= 0, name="vcc_order_this", mask=inner)
        m.add_constraints(z <= 0, name="vcc_unused_binary", mask=~inner)
    # left-hand side of Eq. (8): supply minus every demand term other than P_IT
    elec = -1.0 * p_v
    if p_tn is not None:
        elec = elec + p_tn
    obj = obj + w * ch.vom_usd_per_mwhc * q_v.sum()                                                  # (S11)

    # ---- cooling balance ------------------------------------------------------------------------------------
    cool = q_v if q_a is None else q_v + q_a
    m.add_constraints(cool == da(q_dem), name="cooling_balance")
    if q_a is not None:
        ab = params.absorber
        elec = elec - ab.parasitic_kwe_per_kwc * q_a
        obj = obj + w * ab.vom_usd_per_mwhc * q_a.sum()                                              # (S11)

    # ---- grid (Eqs. 7, 10, 11) -----------------------------------------------------------------------------
    pcc = params.grid.pcc_mw
    price = ts.price_usd_per_mwh
    grid_coef = da(w * (price + cp_theta * ts.carbon_g_per_kwh))
    p_imp = m.add_variables(lower=0.0, upper=pcc, coords=[t], name="P_g_import")
    elec = elec + p_imp
    obj = obj + (grid_coef * p_imp).sum()
    if spec.grid_export:
        p_exp = m.add_variables(lower=0.0, upper=pcc, coords=[t], name="P_g_export")
        y = m.add_variables(coords=[t], name="y", binary=True)
        m.add_constraints(p_imp - pcc * y <= 0, name="eq7_import")
        m.add_constraints(p_exp + pcc * y <= pcc, name="eq7_export")
        elec = elec - p_exp
        obj = obj - (grid_coef * p_exp).sum()

    # ---- battery (Eq. S10) ---------------------------------------------------------------------------------
    if battery:
        bt = params.battery
        s = float(np.sqrt(bt.round_trip_efficiency))
        e0 = bt.initial_soc_fraction * bt.energy_mwh
        b_ch = m.add_variables(lower=0.0, upper=bt.power_mw, coords=[t], name="B_charge")
        b_dis = m.add_variables(lower=0.0, upper=bt.power_mw, coords=[t], name="B_discharge")
        soc = m.add_variables(lower=bt.soc_min_fraction * bt.energy_mwh, upper=bt.soc_max_fraction * bt.energy_mwh,
                              coords=[t], name="E_B")
        first = np.zeros(H, dtype=bool)
        first[0] = True
        flow = s * b_ch - (1.0 / s) * b_dis
        if H > 1:
            m.add_constraints(soc - (1.0 - bt.self_discharge_per_h) * soc.roll(t=1) - flow == 0, name="S10_soc",
                              mask=xr.DataArray(~first, coords=[t]))
        m.add_constraints(soc - flow == (1.0 - bt.self_discharge_per_h) * e0, name="S10_soc_first",
                          mask=xr.DataArray(first, coords=[t]))
        m.add_constraints(soc == e0, name="S10_cyclic_end", mask=xr.DataArray(first[::-1].copy(), coords=[t]))
        elec = elec + b_dis - b_ch
        obj = obj + w * bt.vom_usd_per_mwh * (b_ch.sum() + b_dis.sum())                             # (S11)

    # ---- electric balance (Eq. 8) and objective (Eq. 9) -----------------------------------------------------
    m.add_constraints(elec == da(ts.it_load_mw), name="eq8_electric_balance")
    m.add_objective(obj)
    fixed = fixed_costs(spec, params, battery)
    fixed_cost = sum(a["capex"] + a["fom"] for a in fixed.values())
    return CaseModel(spec=spec, params=params, ts=ts, battery=battery, model=m, data=data, fixed_cost=fixed_cost)


def solution_frame(cm: CaseModel) -> pd.DataFrame:
    """Hourly solution in the layout of :data:`smr_dc_cogen.results.HOURLY_COLUMNS`."""
    from .results import empty_hourly

    h = empty_hourly(cm.ts)
    v = cm.model.variables

    def sol(name):
        return np.asarray(v[name].solution.values, dtype=float)

    names = set(v)
    h["cooling_demand_mwc"] = cm.data["cooling_demand_mwc"]
    h["outage"] = cm.data["outage"]
    pairs = {"P_rx": "reactor_mwth", "P_tg": "turbine_gross_mw", "P_tn": "turbine_net_mw", "Q_ext": "extraction_mwth",
             "Q_a": "absorber_mwc", "Q_v": "chiller_mwc", "P_v": "chiller_mw", "P_g_import": "grid_import_mw",
             "P_g_export": "grid_export_mw", "B_charge": "battery_charge_mw", "B_discharge": "battery_discharge_mw",
             "E_B": "battery_soc_mwh"}
    for var, col in pairs.items():
        if var in names:
            h[col] = sol(var)
    if "absorber_cop" in cm.data:
        h["absorber_cop"] = cm.data["absorber_cop"]
        h["absorber_available"] = cm.data["absorber_available"]
        h["absorber_parasitic_mw"] = cm.params.absorber.parasitic_kwe_per_kwc * h["absorber_mwc"]
    return h


__all__ = ["CaseModel", "build_model", "solution_frame"]
