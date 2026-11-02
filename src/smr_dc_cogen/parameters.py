"""Techno-economic and technical parameters, with validation and the finance relations.

Every parameter carries its unit in its name. Symbols refer to docs/formulation.md, which follows the
article's nomenclature. Blocks that a case does not use may be left out (``None``).

Finance relations implemented here:
  * capital recovery factor  CRF_k = i (1+i)^N_k / ((1+i)^N_k - 1)           (Supplementary Note 9)
  * annuity factor           A(i, n) = (1 - (1+i)^-n) / i                     (Eq. 12)
  * levelized 45Y credit     c_45Y = c_bar A(i, n_credit) / A(i, n_window)    (Eq. 12)
  * salvage credit           P [CRF (1 - s) + s i]  (sinking-fund convention; see formulation.md, conventions)
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, fields
from typing import Any, Dict, Mapping, Optional, Tuple

MMBTU_PER_MWH = 3.412  # higher-heating-value energy content of one MWh, MMBtu/MWh
THETA_T_PER_KG = 1e-3  # Θ in Eq. (11): kg-to-tonne conversion
HOURS_PER_YEAR = 8760


class ParameterError(ValueError):
    """A parameter is missing, out of range or contradicts another parameter."""


def _finite(name: str, value: float, low: Optional[float] = None, high: Optional[float] = None,
            low_open: bool = False, high_open: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParameterError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ParameterError(f"{name} must be finite, got {value!r}")
    if low is not None and (value < low or (low_open and value == low)):
        raise ParameterError(f"{name} must be {'>' if low_open else '>='} {low}, got {value!r}")
    if high is not None and (value > high or (high_open and value == high)):
        raise ParameterError(f"{name} must be {'<' if high_open else '<='} {high}, got {value!r}")


def _positive_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ParameterError(f"{name} must be a positive integer, got {value!r}")


def _curve(name: str, x: Tuple[float, ...], y: Tuple[float, ...], x_from_zero: bool) -> None:
    if len(x) != len(y) or len(x) < 2:
        raise ParameterError(f"{name}: breakpoint lists must have the same length (at least 2)")
    for i, (a, b) in enumerate(zip(x, y)):
        _finite(f"{name} load fraction [{i}]", a, 0.0, 1.0)
        _finite(f"{name} value [{i}]", b, 0.0)
    if any(b <= a for a, b in zip(x, x[1:])):
        raise ParameterError(f"{name}: load fractions must be strictly increasing")
    if x[-1] != 1.0:
        raise ParameterError(f"{name}: the last load fraction must be 1.0 (full load)")
    if x_from_zero and x[0] != 0.0:
        raise ParameterError(f"{name}: the first load fraction must be 0.0")


# ----------------------------------------------------------------------------- finance relations

def crf(rate: float, years: int) -> float:
    """Capital recovery factor CRF = i (1+i)^N / ((1+i)^N - 1) (Supplementary Note 9)."""
    _finite("rate", rate, 0.0, low_open=True)
    _positive_int("years", years)
    g = (1.0 + rate) ** years
    return rate * g / (g - 1.0)


def annuity_factor(rate: float, years: int) -> float:
    """Annuity present-value factor A(i, n) = (1 - (1+i)^-n) / i (Eq. 12)."""
    _finite("rate", rate, 0.0, low_open=True)
    _positive_int("years", years)
    return (1.0 - (1.0 + rate) ** -years) / rate


def annualized_capital(overnight_usd: float, rate: float, years: int, salvage_fraction: float = 0.0) -> float:
    """Annual capital charge of an asset recovered over its own life.

    With no salvage this is ``overnight_usd * CRF``. A salvage value F = s P at the end of life is credited
    through the sinking-fund factor SFF = CRF - i, giving P CRF - s P SFF = P [CRF (1 - s) + s i].
    """
    _finite("salvage_fraction", salvage_fraction, 0.0, 1.0, high_open=True)
    return overnight_usd * (crf(rate, years) * (1.0 - salvage_fraction) + salvage_fraction * rate)


def levelized_ptc(full_credit_usd_per_mwh: float, rate: float, credit_years: int, window_years: int) -> float:
    """Eq. (12): c_45Y = c_bar A(i, credit_years) / A(i, window_years).

    A credit paid for ``credit_years`` is spread evenly (in present-value terms) over the
    ``window_years`` cost-comparison window. If the credit lasts the whole window it is not reduced.
    """
    _finite("full_credit_usd_per_mwh", full_credit_usd_per_mwh, 0.0)
    if credit_years >= window_years:
        _positive_int("credit_years", credit_years)
        _positive_int("window_years", window_years)
        return float(full_credit_usd_per_mwh)
    return full_credit_usd_per_mwh * annuity_factor(rate, credit_years) / annuity_factor(rate, window_years)


def full_load_pue(cop_full_load: float, chain_efficiency: float) -> float:
    """Full-load power usage effectiveness under vapor-compression cooling, 1 + 1/(eta_ch COP_v) (Section 2.1)."""
    _finite("cop_full_load", cop_full_load, 0.0, low_open=True)
    _finite("chain_efficiency", chain_efficiency, 0.0, 1.0, low_open=True)
    return 1.0 + 1.0 / (chain_efficiency * cop_full_load)


def cop_for_full_load_pue(pue: float, chain_efficiency: float) -> float:
    """Inverse of :func:`full_load_pue`: the design-point COP_v that gives a full-load PUE label."""
    _finite("pue", pue, 1.0, low_open=True)
    _finite("chain_efficiency", chain_efficiency, 0.0, 1.0, low_open=True)
    return 1.0 / ((pue - 1.0) * chain_efficiency)


def extraction_penalty_from_heat_balance(lost_lp_work_kj_per_kg: float, generator_heat_kj_per_kg: float,
                                         condensate_credit_kj_per_kg: float) -> float:
    """Willans extraction penalty alpha_w from a steam-table heat balance of the extraction tap (Section 2.2.1).

    Each kilogram of extracted steam forfeits ``lost_lp_work`` of low-pressure expansion work, delivers
    ``generator_heat`` to the absorber generator and returns ``condensate_credit`` of equivalent work through
    the hot-condensate return:  alpha_w = (lost - credit) / heat  (MW_e per MW_th).
    """
    _finite("lost_lp_work_kj_per_kg", lost_lp_work_kj_per_kg, 0.0)
    _finite("generator_heat_kj_per_kg", generator_heat_kj_per_kg, 0.0, low_open=True)
    _finite("condensate_credit_kj_per_kg", condensate_credit_kj_per_kg, 0.0)
    if condensate_credit_kj_per_kg > lost_lp_work_kj_per_kg:
        raise ParameterError("the condensate credit cannot exceed the lost low-pressure work")
    return (lost_lp_work_kj_per_kg - condensate_credit_kj_per_kg) / generator_heat_kj_per_kg


# ----------------------------------------------------------------------------- parameter blocks

@dataclass(frozen=True)
class Site:
    """Data-center side."""
    chain_efficiency: float  # eta_ch, chilled-water distribution (chain) efficiency, -  (Eq. 1)

    def validate(self) -> None:
        _finite("site.chain_efficiency", self.chain_efficiency, 0.0, 1.0, low_open=True)


@dataclass(frozen=True)
class Finance:
    wacc: float                       # i, weighted-average cost of capital, -
    ptc_usd_per_mwh: float            # c_bar, statutory 45Y credit, $/MWh_e
    ptc_credit_years: int             # credit duration from commissioning, yr
    ptc_window_years: int             # cost-comparison window over which the credit is levelized, yr (Eq. 12)
    carbon_price_usd_per_t: float = 0.0  # pi_CO2, $/t CO2

    def validate(self) -> None:
        _finite("finance.wacc", self.wacc, 0.0, 1.0, low_open=True)
        _finite("finance.ptc_usd_per_mwh", self.ptc_usd_per_mwh, 0.0)
        _positive_int("finance.ptc_credit_years", self.ptc_credit_years)
        _positive_int("finance.ptc_window_years", self.ptc_window_years)
        _finite("finance.carbon_price_usd_per_t", self.carbon_price_usd_per_t, 0.0)

    @property
    def levelized_ptc_usd_per_mwh(self) -> float:
        """c_45Y of Eq. (12)."""
        return levelized_ptc(self.ptc_usd_per_mwh, self.wacc, self.ptc_credit_years, self.ptc_window_years)


@dataclass(frozen=True)
class Grid:
    pcc_mw: float  # P_pcc, point-of-common-coupling capacity, MW_e (Eq. 7)

    def validate(self) -> None:
        _finite("grid.pcc_mw", self.pcc_mw, 0.0, low_open=True)


@dataclass(frozen=True)
class Chiller:
    """Water-cooled vapor-compression chiller (VCC): part-load map f_v and hourly wet-bulb relief (Section 2.2.3)."""
    nameplate_mwc: float              # S_v, installed cooling capacity, MW_c
    cop_design: float                 # COP_v, full-load COP at the design wet-bulb, -
    design_wet_bulb_c: float          # wet-bulb at which COP_v applies, degC
    relief_per_k: float               # slope of the wet-bulb relief multiplier, 1/K
    relief_cap: float                 # cap of the relief multiplier, -
    part_load_fraction: Tuple[float, ...]     # breakpoints of the cooling-load fraction, 0 ... 1
    part_load_relative_cop: Tuple[float, ...]  # COP relative to full load at each breakpoint (value at 0 unused)
    capex_usd_per_kwc: float
    fom_usd_per_kwc_yr: float
    vom_usd_per_mwhc: float           # v_v
    lifetime_yr: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "part_load_fraction", tuple(float(x) for x in self.part_load_fraction))
        object.__setattr__(self, "part_load_relative_cop", tuple(float(x) for x in self.part_load_relative_cop))

    def validate(self) -> None:
        _finite("chiller.nameplate_mwc", self.nameplate_mwc, 0.0, low_open=True)
        _finite("chiller.cop_design", self.cop_design, 0.0, low_open=True)
        _finite("chiller.design_wet_bulb_c", self.design_wet_bulb_c)
        _finite("chiller.relief_per_k", self.relief_per_k, 0.0)
        _finite("chiller.relief_cap", self.relief_cap, 0.0, low_open=True)
        _curve("chiller part-load curve", self.part_load_fraction, self.part_load_relative_cop, x_from_zero=True)
        if any(c <= 0.0 for c in self.part_load_relative_cop[1:]):
            raise ParameterError("chiller.part_load_relative_cop must be positive above zero load")
        if self.part_load_relative_cop[-1] != 1.0:
            raise ParameterError("chiller.part_load_relative_cop must be 1.0 at full load (it is relative to COP_v)")
        _finite("chiller.capex_usd_per_kwc", self.capex_usd_per_kwc, 0.0)
        _finite("chiller.fom_usd_per_kwc_yr", self.fom_usd_per_kwc_yr, 0.0)
        _finite("chiller.vom_usd_per_mwhc", self.vom_usd_per_mwhc, 0.0)
        _positive_int("chiller.lifetime_yr", self.lifetime_yr)


@dataclass(frozen=True)
class Reactor:
    thermal_mw: float                 # P_rx_max, rated thermal output, MW_th
    min_load_fraction: float          # P_rx_min / P_rx_max, -
    ramp_mw_per_h: float              # Delta P_rx^max, MW_th/h  (Eq. S9)
    capex_usd_per_kwe: float          # CAPEX_rx per kW_e of net rating
    fom_usd_per_kwe_yr: float
    vom_usd_per_mwh: float            # v_rx, per MWh_e of net generation
    fuel_usd_per_mwh_e: float         # pi_f^e, fuel cost on the net-electric basis
    lifetime_yr: int
    lifecycle_g_per_kwh: float        # gamma_rx, g CO2-eq/kWh_e
    net_rating_mwe: Optional[float] = None  # S_rx; None: Eqs. (2)-(3) at rated thermal output, no extraction
    ptc_eligible: bool = True

    def validate(self) -> None:
        _finite("reactor.thermal_mw", self.thermal_mw, 0.0, low_open=True)
        _finite("reactor.min_load_fraction", self.min_load_fraction, 0.0, 1.0)
        _finite("reactor.ramp_mw_per_h", self.ramp_mw_per_h, 0.0, low_open=True)
        _finite("reactor.capex_usd_per_kwe", self.capex_usd_per_kwe, 0.0)
        _finite("reactor.fom_usd_per_kwe_yr", self.fom_usd_per_kwe_yr, 0.0)
        _finite("reactor.vom_usd_per_mwh", self.vom_usd_per_mwh, 0.0)
        _finite("reactor.fuel_usd_per_mwh_e", self.fuel_usd_per_mwh_e, 0.0)
        _positive_int("reactor.lifetime_yr", self.lifetime_yr)
        _finite("reactor.lifecycle_g_per_kwh", self.lifecycle_g_per_kwh, 0.0)
        if self.net_rating_mwe is not None:
            _finite("reactor.net_rating_mwe", self.net_rating_mwe, 0.0, low_open=True)
        if not isinstance(self.ptc_eligible, bool):
            raise ParameterError("reactor.ptc_eligible must be true or false")


@dataclass(frozen=True)
class Turbine:
    """Cascaded high-pressure / low-pressure turbine with a mid-pressure extraction tap (Eqs. 2-3)."""
    willans_slope: float              # a_w, MW_e/MW_th
    willans_intercept_mw: float       # b_w, MW_e
    extraction_penalty: float         # alpha_w, MW_e/MW_th
    aux_fraction: float               # xi_aux, -
    capex_usd_per_kwe: float = 0.0    # 0: bundled into the reactor's overnight cost
    fom_usd_per_kwe_yr: float = 0.0
    vom_usd_per_mwh: float = 0.0

    def validate(self) -> None:
        _finite("turbine.willans_slope", self.willans_slope, 0.0, low_open=True)
        _finite("turbine.willans_intercept_mw", self.willans_intercept_mw, 0.0)
        _finite("turbine.extraction_penalty", self.extraction_penalty, 0.0)
        _finite("turbine.aux_fraction", self.aux_fraction, 0.0, 1.0, high_open=True)
        _finite("turbine.capex_usd_per_kwe", self.capex_usd_per_kwe, 0.0)
        _finite("turbine.fom_usd_per_kwe_yr", self.fom_usd_per_kwe_yr, 0.0)
        _finite("turbine.vom_usd_per_mwh", self.vom_usd_per_mwh, 0.0)


@dataclass(frozen=True)
class Outage:
    """Scheduled refueling outage T_out: ``length_h`` contiguous hours from ``start_hour`` (0-based)."""
    start_hour: int
    length_h: int

    def validate(self) -> None:
        if isinstance(self.start_hour, bool) or not isinstance(self.start_hour, int) or self.start_hour < 0:
            raise ParameterError(f"outage.start_hour must be a non-negative integer, got {self.start_hour!r}")
        if isinstance(self.length_h, bool) or not isinstance(self.length_h, int) or self.length_h < 0:
            raise ParameterError(f"outage.length_h must be a non-negative integer, got {self.length_h!r}")

    @staticmethod
    def from_capacity_factor(capacity_factor: float, start_hour: int, horizon_h: int = HOURS_PER_YEAR) -> "Outage":
        """Window length round((1 - CF) * horizon) (Supplementary Note 9), so that a plant at full power in every
        online hour realizes the capacity factor CF."""
        _finite("capacity_factor", capacity_factor, 0.0, 1.0)
        _positive_int("horizon_h", horizon_h)
        return Outage(start_hour=int(start_hour), length_h=int(round((1.0 - capacity_factor) * horizon_h)))


@dataclass(frozen=True)
class Absorber:
    """Double-effect LiBr-H2O absorption chiller driven by extracted steam (Eqs. 4-6)."""
    nameplate_mwc: float              # S_a, delivered-cooling cap, MW_c
    cop_design: float                 # COP_a^0 at the design wet-bulb, -
    design_wet_bulb_c: float          # T_wb,0, degC
    derating_per_k: float             # delta, 1/K
    cop_cap: float                    # COP_a nameplate cap, -
    tower_approach_k: float           # Delta T_cw, K
    crystallization_c: float          # T_cry, cooling-water inlet threshold, degC
    parasitic_kwe_per_kwc: float      # beta_a
    capex_usd_per_kwc: float
    fom_usd_per_kwc_yr: float
    vom_usd_per_mwhc: float           # v_a
    lifetime_yr: int
    cop_floor: float = 0.5            # numerical lower bound written into Eq. (4)

    def validate(self) -> None:
        _finite("absorber.nameplate_mwc", self.nameplate_mwc, 0.0, low_open=True)
        _finite("absorber.cop_design", self.cop_design, 0.0, low_open=True)
        _finite("absorber.design_wet_bulb_c", self.design_wet_bulb_c)
        _finite("absorber.derating_per_k", self.derating_per_k, 0.0)
        _finite("absorber.cop_cap", self.cop_cap, 0.0, low_open=True)
        _finite("absorber.cop_floor", self.cop_floor, 0.0, low_open=True)
        if self.cop_floor > self.cop_cap:
            raise ParameterError("absorber.cop_floor must not exceed absorber.cop_cap")
        _finite("absorber.tower_approach_k", self.tower_approach_k, 0.0)
        _finite("absorber.crystallization_c", self.crystallization_c)
        _finite("absorber.parasitic_kwe_per_kwc", self.parasitic_kwe_per_kwc, 0.0)
        _finite("absorber.capex_usd_per_kwc", self.capex_usd_per_kwc, 0.0)
        _finite("absorber.fom_usd_per_kwc_yr", self.fom_usd_per_kwc_yr, 0.0)
        _finite("absorber.vom_usd_per_mwhc", self.vom_usd_per_mwhc, 0.0)
        _positive_int("absorber.lifetime_yr", self.lifetime_yr)

    @property
    def steam_capacity_mw(self) -> float:
        """Generator steam-side capacity: nameplate cooling at the design-point COP, S_a / COP_a^0 (MW_th)."""
        return self.nameplate_mwc / self.cop_design


def relief_matching_absorber(ab: "Absorber") -> Tuple[float, float]:
    """VCC wet-bulb relief (slope per K, cap) equal to the absorber's response relative to its design point:
    slope = delta / COP_a^0 and cap = COP_a_cap / COP_a^0 (Section 2.2.3: both chillers respond alike)."""
    return ab.derating_per_k / ab.cop_design, ab.cop_cap / ab.cop_design


@dataclass(frozen=True)
class Battery:
    """Lithium-ion battery (Eq. S10)."""
    energy_mwh: float                 # Cap_E
    power_mw: float                   # charge and discharge limit
    round_trip_efficiency: float      # eta_rt
    self_discharge_per_h: float       # rho_B
    soc_min_fraction: float
    soc_max_fraction: float
    initial_soc_fraction: float       # E_B(0) / Cap_E; the end state is restored to it
    capex_usd_per_kwh: float
    fom_usd_per_kw_yr: float
    vom_usd_per_mwh: float            # v_B, per MWh charged or discharged
    lifetime_yr: int
    salvage_fraction: float = 0.0     # end-of-life value as a share of overnight cost

    def validate(self) -> None:
        _finite("battery.energy_mwh", self.energy_mwh, 0.0, low_open=True)
        _finite("battery.power_mw", self.power_mw, 0.0, low_open=True)
        _finite("battery.round_trip_efficiency", self.round_trip_efficiency, 0.0, 1.0, low_open=True)
        _finite("battery.self_discharge_per_h", self.self_discharge_per_h, 0.0, 1.0, high_open=True)
        _finite("battery.soc_min_fraction", self.soc_min_fraction, 0.0, 1.0)
        _finite("battery.soc_max_fraction", self.soc_max_fraction, 0.0, 1.0)
        _finite("battery.initial_soc_fraction", self.initial_soc_fraction, 0.0, 1.0)
        if not self.soc_min_fraction <= self.initial_soc_fraction <= self.soc_max_fraction:
            raise ParameterError("battery: need soc_min_fraction <= initial_soc_fraction <= soc_max_fraction")
        _finite("battery.capex_usd_per_kwh", self.capex_usd_per_kwh, 0.0)
        _finite("battery.fom_usd_per_kw_yr", self.fom_usd_per_kw_yr, 0.0)
        _finite("battery.vom_usd_per_mwh", self.vom_usd_per_mwh, 0.0)
        _positive_int("battery.lifetime_yr", self.lifetime_yr)
        _finite("battery.salvage_fraction", self.salvage_fraction, 0.0, 1.0, high_open=True)


@dataclass(frozen=True)
class GasPlant:
    """On-site natural gas combined cycle (Case 3; Eqs. S13-S14)."""
    rating_mwe: float                 # P_ng_max
    hhv_efficiency: float             # eta_hhv at full load
    co2_direct_g_per_kwh: float       # combustion, at full load
    co2_upstream_g_per_kwh: float     # upstream methane as CO2-eq, at full load; gamma_ng = direct + upstream
    part_load_fraction: Tuple[float, ...]          # breakpoints of the electrical load fraction, ... 1
    part_load_heat_rate_multiplier: Tuple[float, ...]  # phi_ng at each breakpoint, 1.0 at full load
    gas_basis_usd_per_mmbtu: float    # added to the hub price to give the delivered price pi_ng
    capex_usd_per_kwe: float
    fom_usd_per_kwe_yr: float
    vom_usd_per_mwh: float            # v_ng
    lifetime_yr: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "part_load_fraction", tuple(float(x) for x in self.part_load_fraction))
        object.__setattr__(self, "part_load_heat_rate_multiplier",
                           tuple(float(x) for x in self.part_load_heat_rate_multiplier))

    def validate(self) -> None:
        _finite("gas_plant.rating_mwe", self.rating_mwe, 0.0, low_open=True)
        _finite("gas_plant.hhv_efficiency", self.hhv_efficiency, 0.0, 1.0, low_open=True)
        _finite("gas_plant.co2_direct_g_per_kwh", self.co2_direct_g_per_kwh, 0.0)
        _finite("gas_plant.co2_upstream_g_per_kwh", self.co2_upstream_g_per_kwh, 0.0)
        _curve("gas_plant part-load curve", self.part_load_fraction, self.part_load_heat_rate_multiplier,
               x_from_zero=False)
        if self.part_load_heat_rate_multiplier[-1] != 1.0:
            raise ParameterError("gas_plant.part_load_heat_rate_multiplier must be 1.0 at full load")
        if any(v < 1.0 for v in self.part_load_heat_rate_multiplier):
            raise ParameterError("gas_plant.part_load_heat_rate_multiplier must be >= 1 (phi_ng >= 1)")
        _finite("gas_plant.gas_basis_usd_per_mmbtu", self.gas_basis_usd_per_mmbtu)
        _finite("gas_plant.capex_usd_per_kwe", self.capex_usd_per_kwe, 0.0)
        _finite("gas_plant.fom_usd_per_kwe_yr", self.fom_usd_per_kwe_yr, 0.0)
        _finite("gas_plant.vom_usd_per_mwh", self.vom_usd_per_mwh, 0.0)
        _positive_int("gas_plant.lifetime_yr", self.lifetime_yr)

    @property
    def heat_rate_mmbtu_per_mwh(self) -> float:
        """h_ng = 3.412 / eta_hhv, full-load HHV heat rate."""
        return MMBTU_PER_MWH / self.hhv_efficiency

    @property
    def lifecycle_g_per_kwh(self) -> float:
        """gamma_ng = direct + upstream, at full load."""
        return self.co2_direct_g_per_kwh + self.co2_upstream_g_per_kwh


@dataclass(frozen=True)
class Parameters:
    """All parameters of one configuration. Blocks a case does not use may be ``None``."""
    site: Site
    finance: Finance
    chiller: Chiller
    grid: Optional[Grid] = None
    reactor: Optional[Reactor] = None
    turbine: Optional[Turbine] = None
    outage: Optional[Outage] = None
    absorber: Optional[Absorber] = None
    battery: Optional[Battery] = None
    gas_plant: Optional[GasPlant] = None

    def validate(self) -> "Parameters":
        for f in fields(self):
            block = getattr(self, f.name)
            if block is not None:
                block.validate()
        if self.reactor is not None and self.turbine is not None:
            # Eq. (2) must give a non-negative gross output over the online range (no extraction)
            tb, rx = self.turbine, self.reactor
            if tb.willans_slope * rx.min_load_fraction * rx.thermal_mw < tb.willans_intercept_mw:
                raise ParameterError("the Willans line gives a negative gross output at the reactor's minimum load; "
                                     "raise reactor.min_load_fraction or check willans_intercept_mw")
            if self.reactor_net_rating_mwe <= 0.0:
                raise ParameterError("the net rating must be positive")
        return self

    # ---- derived quantities -------------------------------------------------
    @property
    def reactor_net_rating_mwe(self) -> float:
        """S_rx: net electric rating. Given, or Eqs. (2)-(3) at rated thermal output with no extraction."""
        if self.reactor is None or self.turbine is None:
            raise ParameterError("reactor and turbine blocks are required")
        if self.reactor.net_rating_mwe is not None:
            return float(self.reactor.net_rating_mwe)
        tb = self.turbine
        return (1.0 - tb.aux_fraction) * (tb.willans_slope * self.reactor.thermal_mw - tb.willans_intercept_mw)

    @property
    def fuel_usd_per_mwh_th(self) -> float:
        """pi_f = pi_f^e * (P_tn_rated / P_rx_rated): the net-electric fuel price on the thermal basis (Section 2.2.5)."""
        if self.reactor is None:
            raise ParameterError("reactor block is required")
        return self.reactor.fuel_usd_per_mwh_e * self.reactor_net_rating_mwe / self.reactor.thermal_mw


# ----------------------------------------------------------------------------- overrides and records

_BLOCKS: Dict[str, type] = {"site": Site, "finance": Finance, "chiller": Chiller, "grid": Grid, "reactor": Reactor,
                            "turbine": Turbine, "outage": Outage, "absorber": Absorber, "battery": Battery,
                            "gas_plant": GasPlant}


def with_changes(params: Parameters, changes: Mapping[str, Any]) -> Parameters:
    """Return a copy with ``{"block.field": value}`` changes applied together, then validated.

    All changes to one block are applied in a single step, so that a valid final state is accepted even if an
    intermediate state would not be. Unknown blocks or fields raise :class:`ParameterError`.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for key, value in changes.items():
        if key.count(".") != 1:
            raise ParameterError(f"change keys have the form 'block.field', got {key!r}")
        block, name = key.split(".")
        if block not in _BLOCKS:
            raise ParameterError(f"unknown parameter block {block!r}")
        if name not in {f.name for f in fields(_BLOCKS[block])}:
            raise ParameterError(f"unknown field {name!r} in block {block!r}")
        grouped.setdefault(block, {})[name] = value
    out = params
    for block, upd in grouped.items():
        current = getattr(out, block)
        if current is None:
            raise ParameterError(f"block {block!r} is not set in these parameters; set the whole block instead")
        out = dataclasses.replace(out, **{block: dataclasses.replace(current, **upd)})
    return out.validate()


def to_dict(params: Parameters) -> Dict[str, Any]:
    """Nested plain dictionary (the layout of ``parameters.json``); unset blocks are left out."""
    out: Dict[str, Any] = {}
    for f in fields(params):
        block = getattr(params, f.name)
        if block is not None:
            d = dataclasses.asdict(block)
            out[f.name] = {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}
    return out


def from_dict(data: Mapping[str, Any]) -> Parameters:
    """Build and validate :class:`Parameters` from the nested layout of :func:`to_dict`.

    Unknown blocks or fields, and missing required fields, raise :class:`ParameterError`.
    """
    if not isinstance(data, Mapping):
        raise ParameterError("parameters must be a mapping of blocks")
    kwargs: Dict[str, Any] = {}
    for block, content in data.items():
        if block not in _BLOCKS:
            raise ParameterError(f"unknown parameter block {block!r}")
        if not isinstance(content, Mapping):
            raise ParameterError(f"block {block!r} must be a mapping of fields")
        cls = _BLOCKS[block]
        known = {f.name: f for f in fields(cls)}
        unknown = set(content) - set(known)
        if unknown:
            raise ParameterError(f"unknown field(s) in block {block!r}: {sorted(unknown)}")
        required = {n for n, f in known.items()
                    if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING}
        missing = required - set(content)
        if missing:
            raise ParameterError(f"missing field(s) in block {block!r}: {sorted(missing)}")
        try:
            kwargs[block] = cls(**content)
        except (TypeError, ValueError) as exc:
            raise ParameterError(f"block {block!r}: {exc}") from exc
    for block in ("site", "finance", "chiller"):
        if block not in kwargs:
            raise ParameterError(f"block {block!r} is required")
    return Parameters(**kwargs).validate()


__all__ = [
    "MMBTU_PER_MWH", "THETA_T_PER_KG", "HOURS_PER_YEAR", "ParameterError",
    "crf", "annuity_factor", "annualized_capital", "levelized_ptc", "full_load_pue", "cop_for_full_load_pue",
    "extraction_penalty_from_heat_balance",
    "Site", "Finance", "Grid", "Chiller", "Reactor", "Turbine", "Outage", "Absorber", "Battery", "GasPlant",
    "Parameters", "with_changes", "to_dict", "from_dict", "relief_matching_absorber",
]
