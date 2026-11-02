"""Hourly technical relations of the article's Section 2: Eqs. (1), (4), (5), the VCC part-load map f_v with its
wet-bulb relief, the NGCC part-load multiplier phi_ng and the refueling-outage window T_out.

All functions are vectorized over hours and do not depend on a solver.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from .parameters import Absorber, Chiller, GasPlant, Outage, ParameterError, Site


def cooling_demand(it_load_mw, site: Site) -> np.ndarray:
    """Eq. (1): Q_cool^dem(t) = P_IT(t) / eta_ch  (MW_c)."""
    return np.asarray(it_load_mw, dtype=float) / site.chain_efficiency


def absorber_cop(wet_bulb_c, ab: Absorber) -> np.ndarray:
    """Eq. (4): COP_a(T_wb) = max{ min[ COP_a^0 - delta (T_wb - T_wb,0), COP_a_cap ], floor }."""
    t = np.asarray(wet_bulb_c, dtype=float)
    return np.maximum(np.minimum(ab.cop_design - ab.derating_per_k * (t - ab.design_wet_bulb_c), ab.cop_cap),
                      ab.cop_floor)


def absorber_available(wet_bulb_c, ab: Absorber, outage_mask: Optional[np.ndarray] = None) -> np.ndarray:
    """Eq. (5): a(t) = 1 if T_wb(t) + Delta T_cw <= T_cry and t is not an outage hour, else 0."""
    t = np.asarray(wet_bulb_c, dtype=float)
    a = (t + ab.tower_approach_k <= ab.crystallization_c).astype(float)
    if outage_mask is not None:
        a[np.asarray(outage_mask, dtype=bool)] = 0.0
    return a


def chiller_relief(wet_bulb_c, ch: Chiller) -> np.ndarray:
    """Hourly wet-bulb relief multiplier on the design-point COP: min[1 + slope (T_design - T_wb), cap].

    It scales the whole part-load curve, so the chiller electricity is f_v(Q_v) / relief(t).
    """
    t = np.asarray(wet_bulb_c, dtype=float)
    r = np.minimum(1.0 + ch.relief_per_k * (ch.design_wet_bulb_c - t), ch.relief_cap)
    if np.any(r <= 0.0):
        raise ParameterError("the chiller relief multiplier is not positive in some hours; "
                             "check relief_per_k against the wet-bulb range")
    return r


def chiller_breakpoints(ch: Chiller) -> Tuple[np.ndarray, np.ndarray]:
    """Breakpoints of the design-point part-load map f_v: cooling (MW_c) and electricity (MW_e).

    q_k = lambda_k S_v and p_k = q_k / (COP_v rho_k), with p_0 = 0 at zero load. Between breakpoints f_v is
    linear in the cooling delivered. The map is in general non-convex (the part-load COP rises and then falls),
    which is why the optimization represents it with special-ordered-set-of-type-2 logic.
    """
    lf = np.asarray(ch.part_load_fraction, dtype=float)
    rel = np.asarray(ch.part_load_relative_cop, dtype=float)
    q = lf * ch.nameplate_mwc
    p = np.zeros_like(q)
    p[1:] = q[1:] / (ch.cop_design * rel[1:])
    return q, p


def chiller_power(cooling_mwc, wet_bulb_c, ch: Chiller) -> np.ndarray:
    """P_v(t) = f_v(Q_v(t)) / relief(t): the part-load map evaluated directly at the cooling delivered (closed-form
    cases). Raises if any hour exceeds the chiller nameplate."""
    q = np.asarray(cooling_mwc, dtype=float)
    if np.any(q < -1e-9) or np.any(q > ch.nameplate_mwc * (1 + 1e-9)):
        raise ParameterError(f"chiller load outside 0..{ch.nameplate_mwc} MW_c "
                             f"(maximum {float(q.max()):.3f} MW_c)")
    qk, pk = chiller_breakpoints(ch)
    return np.interp(np.clip(q, 0.0, ch.nameplate_mwc), qk, pk) / chiller_relief(wet_bulb_c, ch)


def gas_heat_rate_multiplier(load_fraction, gp: GasPlant) -> np.ndarray:
    """phi_ng(l): linear interpolation of the part-load heat-rate curve, held at its end values outside it."""
    lf = np.asarray(load_fraction, dtype=float)
    return np.interp(lf, gp.part_load_fraction, gp.part_load_heat_rate_multiplier)


def outage_mask(outage: Optional[Outage], hours: int) -> np.ndarray:
    """Boolean mask of the hours in T_out (the window is cut at the end of the horizon)."""
    mask = np.zeros(hours, dtype=bool)
    if outage is not None and outage.length_h > 0:
        if outage.start_hour >= hours:
            raise ParameterError(f"outage starts at hour {outage.start_hour}, after the {hours}-hour horizon")
        mask[outage.start_hour:outage.start_hour + outage.length_h] = True
    return mask


__all__ = ["cooling_demand", "absorber_cop", "absorber_available", "chiller_relief", "chiller_breakpoints",
           "chiller_power", "gas_heat_rate_multiplier", "outage_mask"]
