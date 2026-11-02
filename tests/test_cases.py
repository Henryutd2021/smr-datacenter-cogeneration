import dataclasses
import importlib.util

import numpy as np
import pytest

from smr_dc_cogen import (Battery, Outage, ParameterError, annualized_capital, balance_residuals, build_model,
                          evaluate_case0, evaluate_case3, run_case, with_changes)
from smr_dc_cogen.parameters import MMBTU_PER_MWH
from smr_dc_cogen.physics import chiller_power, gas_heat_rate_multiplier
from smr_dc_cogen.solve import solve

from conftest import make_series

TOL = 1e-6


def assert_balanced(result, params):
    res = balance_residuals(result, params)
    assert max(res.values()) < TOL, res


# ----------------------------------------------------------------------------- closed-form cases

def test_case0_by_hand(params):
    ts = make_series([40.0, 50.0, 45.0], [20.0, -5.0, 80.0], carbon=[300.0, 200.0, 500.0],
                     wet_bulb=[22.0, 25.0, 28.0])
    p = with_changes(params, {"finance.carbon_price_usd_per_t": 30.0})
    r = evaluate_case0(p, ts)
    q = ts.it_load_mw / p.site.chain_efficiency
    pv = chiller_power(q, ts.wet_bulb_c, p.chiller)
    imp = ts.it_load_mw + pv
    w = 8760 / 3
    ch = p.chiller
    assert r.costs["capex"] == pytest.approx(annualized_capital(ch.capex_usd_per_kwc * ch.nameplate_mwc * 1e3,
                                                                p.finance.wacc, ch.lifetime_yr))
    assert r.costs["fom"] == pytest.approx(ch.fom_usd_per_kwc_yr * ch.nameplate_mwc * 1e3)
    assert r.costs["vom"] == pytest.approx(w * ch.vom_usd_per_mwhc * q.sum())
    assert r.costs["grid"] == pytest.approx(w * np.sum(ts.price_usd_per_mwh * imp))
    assert r.costs["carbon"] == pytest.approx(30.0 * 1e-3 * w * np.sum(ts.carbon_g_per_kwh * imp))
    assert r.costs["fuel"] == 0 and r.costs["ptc"] == 0
    assert r.tac == pytest.approx(sum(r.costs[k] for k in ("capex", "fom", "vom", "fuel", "grid", "carbon", "ptc")))
    assert_balanced(r, p)


def test_case3_by_hand(params):
    ts = make_series([40.0, 60.0], [20.0, 30.0], wet_bulb=[22.0, 27.0], gas=[3.0, 4.5])
    p = with_changes(params, {"finance.carbon_price_usd_per_t": 45.0})
    r = evaluate_case3(p, ts)
    gp = p.gas_plant
    q = ts.it_load_mw / p.site.chain_efficiency
    png = ts.it_load_mw + chiller_power(q, ts.wet_bulb_c, p.chiller)
    phi = gas_heat_rate_multiplier(png / gp.rating_mwe, gp)
    w = 8760 / 2
    fuel = w * np.sum((ts.gas_usd_per_mmbtu + gp.gas_basis_usd_per_mmbtu) * MMBTU_PER_MWH / gp.hhv_efficiency * phi * png)
    assert r.costs["fuel"] == pytest.approx(fuel)                                              # Eq. (S13)
    assert r.costs["carbon"] == pytest.approx(45.0 * 1e-3 * w * np.sum(gp.lifecycle_g_per_kwh * phi * png))  # (S14)
    assert r.costs["grid"] == 0 and r.costs["ptc"] == 0
    assert r.costs["vom"] == pytest.approx(w * np.sum(gp.vom_usd_per_mwh * png + p.chiller.vom_usd_per_mwhc * q))
    assert_balanced(r, p)


def test_closed_form_capacity_checks(params):
    ts = make_series([40.0], [20.0], gas=[3.0])
    with pytest.raises(ParameterError, match="chiller nameplate"):
        evaluate_case0(with_changes(params, {"chiller.nameplate_mwc": 30.0}), ts)
    with pytest.raises(ParameterError, match="PCC"):
        evaluate_case0(with_changes(params, {"grid.pcc_mw": 41.0}), ts)
    with pytest.raises(ParameterError, match="rating"):
        evaluate_case3(with_changes(params, {"gas_plant.rating_mwe": 41.0}), ts)
    with pytest.raises(ParameterError, match="gas price"):
        evaluate_case3(params, make_series([40.0], [20.0]))
    with pytest.raises(ParameterError):
        run_case(0, params, ts, battery=True)
    with pytest.raises(ParameterError):
        run_case(3, dataclasses.replace(params, gas_plant=None), ts)


def test_annualization_weight(params, series):
    twice = make_series(np.tile(series.it_load_mw, 2), np.tile(series.price_usd_per_mwh, 2),
                        np.tile(series.carbon_g_per_kwh, 2), np.tile(series.wet_bulb_c, 2))
    assert evaluate_case0(params, twice).tac == pytest.approx(evaluate_case0(params, series).tac)


# ----------------------------------------------------------------------------- optimized cases

def test_case0_milp_equals_closed_form(params, series):
    a = evaluate_case0(params, series)
    b = run_case(0, params, series, optimize_case0=True)
    assert b.tac == pytest.approx(a.tac, rel=1e-9)
    assert np.allclose(b.hourly["chiller_mw"], a.hourly["chiller_mw"], atol=1e-7)


@pytest.mark.parametrize("case", [1, 2])
def test_reactor_cases_satisfy_formulation(params, series, case):
    r = run_case(case, params, series)
    assert_balanced(r, params)
    assert r.solver["objective_usd_per_yr"] == pytest.approx(r.tac, rel=1e-9)     # accounting = objective
    assert r.solver["version"] and r.solver["mip_gap_requested"] == 1e-6
    assert r.solver["mip_gap"] is None or r.solver["mip_gap"] <= 1e-6
    h = r.hourly
    out = h["outage"].to_numpy()
    assert out.sum() == params.outage.length_h
    assert np.all(h["reactor_mwth"][out] == 0) and np.all(h["turbine_gross_mw"][out] == 0)
    assert np.all(h["reactor_mwth"][~out] >= params.reactor.min_load_fraction * params.reactor.thermal_mw - TOL)
    if case == 2:
        ab = params.absorber
        assert np.all(h["extraction_mwth"][h["absorber_available"] == 0] <= TOL)
        assert h["extraction_mwth"].max() <= ab.steam_capacity_mw + TOL
        assert h["absorber_mwc"].max() <= ab.nameplate_mwc + TOL
        assert (h["absorber_available"] == 0).sum() > out.sum()     # the hot spell closes the crystallization gate
    else:
        assert h["absorber_mwc"].abs().max() == 0


def test_absorber_follows_price_sign(linear_params):
    # absorber: 0.1 MW_e of forgone turbine output per MW_c; chiller: 1/3 MW_e per MW_c.
    # Positive price: steam cooling is cheaper. Negative price: the electric chiller is preferred.
    ts = make_series([30.0, 30.0, 30.0], [-20.0, 40.0, 5.0])
    r = run_case(2, linear_params, ts)
    h = r.hourly
    assert h["absorber_mwc"].tolist() == pytest.approx([0.0, 30.0, 30.0], abs=1e-6)
    assert h["chiller_mwc"].tolist() == pytest.approx([30.0, 0.0, 0.0], abs=1e-6)
    assert_balanced(r, linear_params)


def test_eq2_extraction_penalty(linear_params):
    ts = make_series([30.0], [40.0])
    h = run_case(2, linear_params, ts).hourly
    tb = linear_params.turbine
    assert h["turbine_gross_mw"][0] == pytest.approx(
        tb.willans_slope * h["reactor_mwth"][0] - tb.willans_intercept_mw - 0.1 * h["extraction_mwth"][0])
    assert h["extraction_mwth"][0] == pytest.approx(30.0)


def test_ramp_limit_binds(linear_params):
    p = with_changes(linear_params, {"reactor.ramp_mw_per_h": 20.0, "reactor.fuel_usd_per_mwh_e": 30.0})
    ts = make_series([30.0] * 4, [0.0, 100.0, 100.0, 100.0])
    h = run_case(1, p, ts).hourly
    steps = np.diff(h["reactor_mwth"])
    assert np.all(np.abs(steps) <= 20.0 + TOL)
    assert steps[0] == pytest.approx(20.0)


def test_interlock_and_export(linear_params):
    ts = make_series([30.0, 30.0], [50.0, 60.0])
    h = run_case(1, linear_params, ts).hourly
    assert np.all(np.minimum(h["grid_import_mw"], h["grid_export_mw"]) <= TOL)
    assert np.all(h["grid_export_mw"] > 0)             # cheap reactor output is sold


def test_outage_hours_buy_from_grid(linear_params):
    p = dataclasses.replace(linear_params, outage=Outage(1, 1))
    h = run_case(2, p, make_series([30.0] * 3, [10.0, 10.0, 10.0])).hourly
    assert h["reactor_mwth"][1] == 0 and h["absorber_mwc"][1] == 0
    assert h["grid_import_mw"][1] == pytest.approx(30.0 + 30.0 / 3.0)


def test_battery_arbitrage_and_soc(linear_params):
    p = dataclasses.replace(linear_params, battery=Battery(
        energy_mwh=10.0, power_mw=5.0, round_trip_efficiency=0.81, self_discharge_per_h=0.0, soc_min_fraction=0.0,
        soc_max_fraction=1.0, initial_soc_fraction=0.6, capex_usd_per_kwh=0.0, fom_usd_per_kw_yr=0.0,
        vom_usd_per_mwh=0.0, lifetime_yr=10))
    ts = make_series([30.0] * 4, [10.0, 10.0, 200.0, 200.0])
    r = run_case(1, p, ts, battery=True)
    h = r.hourly
    assert_balanced(r, p)
    assert h["battery_charge_mw"][:2].sum() > 0 and h["battery_discharge_mw"][2:].sum() > 0
    assert h["battery_soc_mwh"].iloc[-1] == pytest.approx(6.0)
    assert r.costs["capex"] == 0


def test_carbon_and_credit_accounting(params, series):
    p = with_changes(params, {"finance.carbon_price_usd_per_t": 40.0})
    r = run_case(1, p, series)
    h = r.hourly
    w = series.weight
    footprint = w * 1e-3 * (np.sum(series.carbon_g_per_kwh * (h["grid_import_mw"] - h["grid_export_mw"]))
                            + p.reactor.lifecycle_g_per_kwh * h["turbine_net_mw"].sum())
    assert r.totals["footprint_t_co2"] == pytest.approx(footprint)
    assert r.costs["carbon"] == pytest.approx(40.0 * footprint)                                  # Eq. (11)
    assert r.costs["ptc"] == pytest.approx(-w * p.finance.levelized_ptc_usd_per_mwh * h["turbine_net_mw"].sum())
    no_credit = run_case(1, with_changes(p, {"reactor.ptc_eligible": False}), series)
    assert no_credit.costs["ptc"] == 0 and no_credit.tac > r.tac


def test_tac_concave_in_carbon_price(params, series):
    # TAC(pi) = min over dispatch of a function linear in pi: the slope at pi is the footprint at pi
    r0 = run_case(1, params, series)
    r1 = run_case(1, with_changes(params, {"finance.carbon_price_usd_per_t": 60.0}), series)
    tol = 4e-6 * abs(r0.tac)                       # two solves at the relative MIP gap of the defaults
    assert r1.tac <= r0.tac + 60.0 * r0.totals["footprint_t_co2"] + tol
    assert r1.tac >= r0.tac + 60.0 * r1.totals["footprint_t_co2"] - tol


def test_build_model_rejects_case3(params, series):
    with pytest.raises(ParameterError):
        build_model(3, params, series)
    with pytest.raises(ParameterError, match="needs"):
        build_model(2, dataclasses.replace(params, absorber=None), series)


@pytest.mark.skipif(importlib.util.find_spec("gurobipy") is None, reason="gurobipy not installed")
def test_gurobi_matches_highs(params, series):
    try:
        g = solve(build_model(2, params, series), solver="gurobi")
    except Exception as exc:  # no licence, etc.
        pytest.skip(f"Gurobi not usable: {exc}")
    h = run_case(2, params, series)
    assert g.objective + g.fixed_cost == pytest.approx(h.tac, rel=1e-5)
