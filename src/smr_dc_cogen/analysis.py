"""Figures of merit, secondary endpoints and boundary tools.

* grid-cost margin, Eq. (13)
* NLCS, LCOC, EPBT and the three water tiers, Eqs. (S1)-(S6)
* dispatch-realized PUE (Section 2.1)
* break-even capital of a dispatch-neutral asset (Supplementary Notes 3 and 5)
* crossover carbon price from solved TAC curves (Section 3.5 of the article uses solved and extrapolated ranges)
* diagnostics of the steam extraction (derived from Eqs. 2, 3, 10 and 12; not article equations)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from .parameters import Parameters, _finite, crf
from .results import CaseResult


def grid_cost_margin(tac_case: float, tac_grid_only: float) -> float:
    """Eq. (13): M_c = 1 - TAC_c / TAC_0, both at the matched scenario. Positive: cheaper than grid supply."""
    if not tac_grid_only > 0:
        raise ValueError("the grid-only TAC must be positive")
    return 1.0 - tac_case / tac_grid_only


def nlcs(result: CaseResult) -> float:
    """Eq. (S1): net levelized cost of IT supply, TAC / E_IT ($/MWh_IT)."""
    return result.tac / result.totals["it_energy_mwh"]


def lcoc_grid_only(result: CaseResult, params: Parameters) -> float:
    """Eq. (S2): levelized cost of cooling of the grid-only Case 0 ($/MWh_c).

    (chiller annualized capital + fixed O&M + sum_t [v_v Q_v + pi_g P_v]) / sum_t Q_v. Only Case 0 separates the
    cooling cost without an allocation rule.
    """
    if result.case != 0:
        raise ValueError("LCOC is defined for the grid-only Case 0 only")
    h = result.hourly
    w = result.totals["annualization_weight"]
    ch = params.chiller
    fixed = result.assets["chiller"]["capex"] + result.assets["chiller"]["fom"]
    variable = w * float(np.sum(ch.vom_usd_per_mwhc * h["chiller_mwc"] + h["price_usd_per_mwh"] * h["chiller_mw"]))
    return (fixed + variable) / result.totals["chiller_cooling_mwhc"]


@dataclass(frozen=True)
class EndpointFactors:
    """Water-consumption factors (L/kWh, consumption basis) and embodied-energy intensities (MWh_e-eq per kW)."""
    water_chiller_l_per_kwhc: float   # w_v
    water_absorber_l_per_kwhc: float  # w_a
    water_reactor_l_per_kwh: float    # w_rx, per kWh_e generated
    water_gas_l_per_kwh: float        # w_ng
    water_grid_l_per_kwh: float       # w_g, per kWh_e imported
    scarcity_factor: float            # f_sc (world mean = 1)
    embodied_reactor_mwh_per_kw: float  # e_rx
    embodied_gas_mwh_per_kw: float      # e_ng

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _finite(name, getattr(self, name), 0.0)


def absorber_water_factor(water_chiller_l_per_kwhc: float, mean_absorber_cop: float) -> float:
    """w_a = w_v (1 + 1/COP_a): an absorber rejects its cooling duty plus its driving heat (Supplementary Note 7)."""
    _finite("mean_absorber_cop", mean_absorber_cop, 0.0, low_open=True)
    return water_chiller_l_per_kwhc * (1.0 + 1.0 / mean_absorber_cop)


def water_footprint(result: CaseResult, f: EndpointFactors) -> Dict[str, float]:
    """Eqs. (S4)-(S6), per MWh of IT energy delivered.

    W_dir and W_ind in L/MWh_IT; W_tot = W_dir + W_ind; W_sc = f_sc W_tot / 1000 in m3 world-equivalent per MWh_IT.
    Generation water is charged on all net generation, including exports (site attribution).
    """
    t = result.totals
    e_it = t["it_energy_mwh"]
    reactor_mwh = t["net_generation_mwh"] if result.sizes.get("reactor_mwe") else 0.0
    gas_mwh = t["net_generation_mwh"] if result.sizes.get("gas_plant_mwe") else 0.0
    # factors are per kWh; energies are MWh: L/kWh * MWh * 1000 = L
    w_dir = 1e3 * (f.water_chiller_l_per_kwhc * t["chiller_cooling_mwhc"]
                   + f.water_absorber_l_per_kwhc * t["absorber_cooling_mwhc"]) / e_it
    w_ind = 1e3 * (f.water_reactor_l_per_kwh * reactor_mwh + f.water_gas_l_per_kwh * gas_mwh
                   + f.water_grid_l_per_kwh * t["grid_import_mwh"]) / e_it
    w_tot = w_dir + w_ind
    return {"W_dir_l_per_mwh": w_dir, "W_ind_l_per_mwh": w_ind, "W_tot_l_per_mwh": w_tot,
            "W_sc_m3eq_per_mwh": f.scarcity_factor * w_tot / 1e3}


def epbt(result: CaseResult, f: EndpointFactors) -> float:
    """Eq. (S3): energy payback time e_k S_k / E_gen (years), for the on-site plant. NaN for Case 0."""
    gen = result.totals["net_generation_mwh"]
    if result.sizes.get("reactor_mwe"):
        return f.embodied_reactor_mwh_per_kw * result.sizes["reactor_mwe"] * 1e3 / gen
    if result.sizes.get("gas_plant_mwe"):
        return f.embodied_gas_mwh_per_kw * result.sizes["gas_plant_mwe"] * 1e3 / gen
    return float("nan")


def realized_pue(result: CaseResult) -> float:
    """Dispatch-realized annual PUE, 1 + (chiller electricity + absorber parasitics) / IT energy.

    Counts cooling electricity only; the steam that drives the absorber is not counted (electric-only metric).
    """
    t = result.totals
    return 1.0 + (t["chiller_electricity_mwh"] + t["absorber_parasitic_mwh"]) / t["it_energy_mwh"]


def breakeven_capex(current_capex: float, delta_tac: float, rate: float, life_yr: int, size_kw: float) -> float:
    """Capital cost per kW at which a TAC gap closes, for an asset whose capital is dispatch-neutral.

    If the asset's capital enters the objective only as CRF * CAPEX * S and appears in no constraint, the optimal
    dispatch does not depend on it, and TAC is linear in CAPEX with slope CRF * S. Then
    CAPEX* = CAPEX - delta_tac / (CRF * S), where delta_tac = TAC(with asset) - TAC(reference) at CAPEX.
    A negative value means that no non-negative capital cost closes the gap.
    """
    _finite("size_kw", size_kw, 0.0, low_open=True)
    return current_capex - delta_tac / (crf(rate, life_yr) * size_kw)


def reactor_parity_capex(result: CaseResult, tac_grid_only: float, params: Parameters) -> float:
    """Reactor capital ($/kW_e) at which a reactor case reaches the grid-only TAC (margin of Eq. 13 equal to zero).

    Uses TAC = K(capital) + D(dispatch): reactor capital is dispatch-neutral. A separate turbine cost, if any, is
    held fixed.
    """
    if not result.sizes.get("reactor_mwe"):
        raise ValueError("not a reactor case")
    return breakeven_capex(params.reactor.capex_usd_per_kwe, result.tac - tac_grid_only, params.finance.wacc,
                           params.reactor.lifetime_yr, result.sizes["reactor_mwe"] * 1e3)


def absorber_breakeven_capex(case2: CaseResult, case1: CaseResult, params: Parameters) -> float:
    """Absorber capital ($/kW_c) at which Case 2 costs the same as Case 1 at otherwise matched settings
    (Case 1 installs no absorber, so the Case 2 - Case 1 gap is linear in the absorber's capital)."""
    if case2.case != 2 or case1.case != 1:
        raise ValueError("pass a Case 2 and a Case 1 result")
    ab = params.absorber
    return breakeven_capex(ab.capex_usd_per_kwc, case2.tac - case1.tac, params.finance.wacc, ab.lifetime_yr,
                           ab.nameplate_mwc * 1e3)


def crossover_price(prices: Sequence[float], tac_case: Sequence[float], tac_reference: Sequence[float],
                    extrapolate: bool = False) -> Optional[Tuple[float, bool]]:
    """Carbon price at which ``tac_case`` crosses ``tac_reference``, by linear interpolation between solved prices.

    Returns ``(price, extrapolated)`` for the first sign change of tac_case - tac_reference within the solved range.
    Without a sign change, and if ``extrapolate`` is True, the line through the two highest solved prices is
    extended upward and a crossing above the solved range is returned with ``extrapolated=True``; otherwise None.
    (TAC is concave in the carbon price, so an extrapolated crossing is indicative only.)
    """
    p = np.asarray(prices, dtype=float)
    d = np.asarray(tac_case, dtype=float) - np.asarray(tac_reference, dtype=float)
    if p.size < 2 or p.size != d.size or np.any(np.diff(p) <= 0):
        raise ValueError("need at least two strictly increasing prices with matching TAC values")
    if d[0] == 0.0:
        return float(p[0]), False
    for i in range(p.size - 1):
        if d[i + 1] == 0.0:
            return float(p[i + 1]), False
        if d[i] * d[i + 1] < 0:
            return float(p[i] - d[i] * (p[i + 1] - p[i]) / (d[i + 1] - d[i])), False
    if not extrapolate:
        return None
    slope = (d[-1] - d[-2]) / (p[-1] - p[-2])
    if slope != 0:
        x = p[-1] - d[-1] / slope
        if x > p[-1]:
            return float(x), True
    return None


def extraction_diagnostics(result: CaseResult, params: Parameters) -> Dict[str, float]:
    """Annual quantities of the steam extraction in a Case 2 result (derived from Eqs. 2, 3, 10, 12):

    * ``forgone_net_generation_mwh``: (1 - xi_aux) alpha_w sum_t Q_ext(t), the net output the extraction removes;
    * ``forgone_generation_value_usd``: the same, valued at the hourly price pi_g(t);
    * ``forgone_ptc_usd``: the same energy times the levelized credit c_45Y.
    """
    if result.case != 2:
        raise ValueError("pass a Case 2 result")
    tb = params.turbine
    w = result.totals["annualization_weight"]
    lost = (1.0 - tb.aux_fraction) * tb.extraction_penalty * result.hourly["extraction_mwth"]
    rate = result.totals["levelized_ptc_usd_per_mwh"]
    return {"forgone_net_generation_mwh": w * float(lost.sum()),
            "forgone_generation_value_usd": w * float((lost * result.hourly["price_usd_per_mwh"]).sum()),
            "forgone_ptc_usd": w * rate * float(lost.sum())}


__all__ = ["grid_cost_margin", "nlcs", "lcoc_grid_only", "EndpointFactors", "absorber_water_factor",
           "water_footprint", "epbt", "realized_pue", "breakeven_capex", "reactor_parity_capex",
           "absorber_breakeven_capex", "crossover_price", "extraction_diagnostics"]
