"""The four system configurations (article Table 1) and their dispatch-neutral fixed costs.

| Case | supply                                   | cooling                          | grid                 | method        |
|------|------------------------------------------|----------------------------------|----------------------|---------------|
| 0    | grid purchases                           | VCC                              | import               | closed form   |
| 1    | reactor + turbine (no heat recovery)     | VCC                              | import and export    | MILP          |
| 2    | reactor + turbine with steam extraction  | absorber and VCC share dispatch  | import and export    | MILP          |
| 3    | on-site NGCC (islanded)                  | VCC                              | none                 | closed form   |

A battery can be added to Cases 1 and 2 (the article's battery sensitivity).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .parameters import Parameters, ParameterError, annualized_capital


@dataclass(frozen=True)
class CaseSpec:
    number: int
    name: str
    reactor: bool
    absorber: bool
    gas_plant: bool
    grid_import: bool
    grid_export: bool
    optimized: bool  # True: mixed-integer dispatch problem; False: closed-form evaluation


CASES: Dict[int, CaseSpec] = {
    0: CaseSpec(0, "grid-only", reactor=False, absorber=False, gas_plant=False,
                grid_import=True, grid_export=False, optimized=False),
    1: CaseSpec(1, "reactor, no heat recovery", reactor=True, absorber=False, gas_plant=False,
                grid_import=True, grid_export=True, optimized=True),
    2: CaseSpec(2, "reactor with absorption cooling", reactor=True, absorber=True, gas_plant=False,
                grid_import=True, grid_export=True, optimized=True),
    3: CaseSpec(3, "on-site NGCC, islanded", reactor=False, absorber=False, gas_plant=True,
                grid_import=False, grid_export=False, optimized=False),
}


def case_spec(case: int) -> CaseSpec:
    if case not in CASES:
        raise ParameterError(f"case must be one of {sorted(CASES)}, got {case!r}")
    return CASES[case]


def check_blocks(spec: CaseSpec, params: Parameters, battery: bool) -> None:
    """Raise if a block the case needs is missing."""
    need = ["chiller"]
    if spec.reactor:
        need += ["reactor", "turbine"]
    if spec.absorber:
        need.append("absorber")
    if spec.gas_plant:
        need.append("gas_plant")
    if spec.grid_import or spec.grid_export:
        need.append("grid")
    if battery:
        if not spec.optimized:
            raise ParameterError(f"the battery is optimized only in Cases 1 and 2 (got Case {spec.number})")
        need.append("battery")
    missing = [b for b in need if getattr(params, b) is None]
    if missing:
        raise ParameterError(f"Case {spec.number} needs the parameter block(s) {missing}")


def asset_sizes(spec: CaseSpec, params: Parameters, battery: bool) -> Dict[str, float]:
    """Installed size S_k of every asset of the case (MW_e, MW_c or MWh)."""
    sizes = {"chiller_mwc": params.chiller.nameplate_mwc}
    if spec.reactor:
        sizes["reactor_mwe"] = params.reactor_net_rating_mwe
    if spec.absorber:
        sizes["absorber_mwc"] = params.absorber.nameplate_mwc
    if spec.gas_plant:
        sizes["gas_plant_mwe"] = params.gas_plant.rating_mwe
    if battery:
        sizes["battery_mwh"] = params.battery.energy_mwh
        sizes["battery_mw"] = params.battery.power_mw
    return sizes


def fixed_costs(spec: CaseSpec, params: Parameters, battery: bool) -> Dict[str, Dict[str, float]]:
    """Annualized capital sum_k CRF_k CAPEX_k S_k and fixed O&M sum_k FOM_k S_k per asset ($/yr; Eq. 9).

    Every asset is annualized over its own life at the case WACC. The turbine, if it has its own cost, is
    annualized over the reactor life (it belongs to the reactor island). The battery's salvage credit uses the
    sinking-fund convention of :func:`smr_dc_cogen.parameters.annualized_capital`.
    """
    i = params.finance.wacc
    out: Dict[str, Dict[str, float]] = {}
    ch = params.chiller
    kwc = ch.nameplate_mwc * 1e3
    out["chiller"] = {"capex": annualized_capital(ch.capex_usd_per_kwc * kwc, i, ch.lifetime_yr),
                      "fom": ch.fom_usd_per_kwc_yr * kwc}
    if spec.reactor:
        rx, tb = params.reactor, params.turbine
        kwe = params.reactor_net_rating_mwe * 1e3
        out["reactor"] = {"capex": annualized_capital(rx.capex_usd_per_kwe * kwe, i, rx.lifetime_yr),
                          "fom": rx.fom_usd_per_kwe_yr * kwe}
        out["turbine"] = {"capex": annualized_capital(tb.capex_usd_per_kwe * kwe, i, rx.lifetime_yr),
                          "fom": tb.fom_usd_per_kwe_yr * kwe}
    if spec.absorber:
        ab = params.absorber
        kwc = ab.nameplate_mwc * 1e3
        out["absorber"] = {"capex": annualized_capital(ab.capex_usd_per_kwc * kwc, i, ab.lifetime_yr),
                           "fom": ab.fom_usd_per_kwc_yr * kwc}
    if spec.gas_plant:
        gp = params.gas_plant
        kwe = gp.rating_mwe * 1e3
        out["gas_plant"] = {"capex": annualized_capital(gp.capex_usd_per_kwe * kwe, i, gp.lifetime_yr),
                            "fom": gp.fom_usd_per_kwe_yr * kwe}
    if battery:
        bt = params.battery
        out["battery"] = {"capex": annualized_capital(bt.capex_usd_per_kwh * bt.energy_mwh * 1e3, i, bt.lifetime_yr,
                                                      bt.salvage_fraction),
                          "fom": bt.fom_usd_per_kw_yr * bt.power_mw * 1e3}
    return out


__all__ = ["CaseSpec", "CASES", "case_spec", "check_blocks", "asset_sizes", "fixed_costs"]
