import numpy as np
import pandas as pd
import pytest

from smr_dc_cogen import (Scenario, absorber_breakeven_capex, add_margins, breakeven_capex, crf, crossover_price,
                          epbt, evaluate_case0, extraction_diagnostics, grid_cost_margin, illustrative_endpoint_factors,
                          lcoc_grid_only, nlcs, reactor_parity_capex, realized_pue, run_case, run_scenarios,
                          scale_data_center, water_footprint, with_changes)
from smr_dc_cogen.results import bucket_difference


def test_margin_eq13():
    assert grid_cost_margin(80.0, 100.0) == pytest.approx(0.2)
    assert grid_cost_margin(150.0, 100.0) == pytest.approx(-0.5)
    with pytest.raises(ValueError):
        grid_cost_margin(1.0, 0.0)


def test_secondary_endpoints(params, series):
    f = illustrative_endpoint_factors()
    r0 = evaluate_case0(params, series)
    t = r0.totals
    assert nlcs(r0) == pytest.approx(r0.tac / t["it_energy_mwh"])                                  # (S1)
    h = r0.hourly
    ch = params.chiller
    fixed = r0.assets["chiller"]["capex"] + r0.assets["chiller"]["fom"]
    var = series.weight * np.sum(ch.vom_usd_per_mwhc * h["chiller_mwc"] + h["price_usd_per_mwh"] * h["chiller_mw"])
    assert lcoc_grid_only(r0, params) == pytest.approx((fixed + var) / t["chiller_cooling_mwhc"])     # (S2)
    wf = water_footprint(r0, f)                                                                     # (S4)-(S6)
    assert wf["W_dir_l_per_mwh"] == pytest.approx(1e3 * f.water_chiller_l_per_kwhc * t["chiller_cooling_mwhc"]
                                                  / t["it_energy_mwh"])
    assert wf["W_ind_l_per_mwh"] == pytest.approx(1e3 * f.water_grid_l_per_kwh * t["grid_import_mwh"]
                                                  / t["it_energy_mwh"])
    assert wf["W_sc_m3eq_per_mwh"] == pytest.approx(f.scarcity_factor * wf["W_tot_l_per_mwh"] / 1e3)
    assert np.isnan(epbt(r0, f))
    assert realized_pue(r0) == pytest.approx(1 + t["chiller_electricity_mwh"] / t["it_energy_mwh"])
    r2 = run_case(2, params, series)
    assert epbt(r2, f) == pytest.approx(f.embodied_reactor_mwh_per_kw * r2.sizes["reactor_mwe"] * 1e3
                                        / r2.totals["net_generation_mwh"])                          # (S3)
    assert realized_pue(r2) == pytest.approx(
        1 + (r2.totals["chiller_electricity_mwh"] + r2.totals["absorber_parasitic_mwh"]) / r2.totals["it_energy_mwh"])
    with pytest.raises(ValueError):
        lcoc_grid_only(r2, params)


def test_breakeven_formula():
    assert breakeven_capex(1000.0, 0.0, 0.07, 20, 5e4) == 1000.0
    assert breakeven_capex(1000.0, 2e6, 0.07, 20, 5e4) == pytest.approx(1000.0 - 2e6 / (crf(0.07, 20) * 5e4))


def test_absorber_breakeven_closes_the_gap(linear_params):
    from conftest import make_series
    p = with_changes(linear_params, {"absorber.fom_usd_per_kwc_yr": 2.0, "chiller.capex_usd_per_kwc": 100.0})
    ts = make_series([30.0, 32.0, 28.0, 30.0], [40.0, 60.0, 20.0, 50.0])
    r1 = run_case(1, p, ts)
    r2 = run_case(2, p, ts)
    x = absorber_breakeven_capex(r2, r1, p)
    assert x > 0
    r2x = run_case(2, with_changes(p, {"absorber.capex_usd_per_kwc": x}), ts)
    assert r2x.tac == pytest.approx(r1.tac, rel=1e-9)
    # dispatch does not depend on the absorber's capital
    assert np.allclose(r2x.hourly["absorber_mwc"], r2.hourly["absorber_mwc"], atol=1e-6)


def test_reactor_parity_capex(params, series):
    r0 = evaluate_case0(params, series)
    r1 = run_case(1, params, series)
    x = reactor_parity_capex(r1, r0.tac, params)
    r1x = run_case(1, with_changes(params, {"reactor.capex_usd_per_kwe": x}), series)
    assert r1x.tac == pytest.approx(r0.tac, rel=2e-6)
    assert grid_cost_margin(r1x.tac, r0.tac) == pytest.approx(0.0, abs=1e-5)


def test_crossover_price():
    p = [0, 45, 90]
    assert crossover_price(p, [120, 100, 80], [90, 95, 100]) == (pytest.approx(54.0), False)
    assert crossover_price(p, [120, 110, 100], [90, 95, 100]) == (pytest.approx(90.0), False)
    assert crossover_price(p, [130, 120, 110], [90, 95, 100]) is None
    x, extrapolated = crossover_price(p, [130, 120, 110], [90, 95, 100], extrapolate=True)
    assert extrapolated and x == pytest.approx(90 + 10 / 15 * 45)
    assert crossover_price(p, [100, 115, 130], [90, 95, 100], extrapolate=True) is None   # only upward
    with pytest.raises(ValueError):
        crossover_price([0, 0], [1, 2], [2, 1])


def test_extraction_diagnostics(params, series):
    r2 = run_case(2, params, series)
    d = extraction_diagnostics(r2, params)
    tb = params.turbine
    lost = (1 - tb.aux_fraction) * tb.extraction_penalty * r2.hourly["extraction_mwth"]
    assert d["forgone_net_generation_mwh"] == pytest.approx(series.weight * lost.sum())
    assert d["forgone_generation_value_usd"] == pytest.approx(series.weight * (lost * series.price_usd_per_mwh).sum())
    assert d["forgone_ptc_usd"] == pytest.approx(params.finance.levelized_ptc_usd_per_mwh * d["forgone_net_generation_mwh"])
    diff = bucket_difference(r2, run_case(1, params, series))
    assert diff["tac"] == pytest.approx(sum(v for k, v in diff.items() if k != "tac"))


def test_size_rule(params, series):
    small, ts_small = scale_data_center(params, series, 0.6)
    big, ts_big = scale_data_center(params, series, 1.7)
    assert small.grid.pcc_mw == params.grid.pcc_mw and big.grid.pcc_mw == pytest.approx(1.7 * params.grid.pcc_mw)
    assert big.reactor == params.reactor
    assert big.chiller.nameplate_mwc == pytest.approx(1.7 * params.chiller.nameplate_mwc)
    assert big.absorber.nameplate_mwc == pytest.approx(1.7 * params.absorber.nameplate_mwc)
    assert big.gas_plant.rating_mwe == pytest.approx(1.7 * params.gas_plant.rating_mwe)
    assert ts_small.it_load_mw == pytest.approx(0.6 * series.it_load_mw)


def test_scenarios_and_matched_margins(params, series):
    other = series.scaled_load(1.0)
    scs = [Scenario("c0", 0, "a"), Scenario("c1", 1, "a"), Scenario("c0b", 0, "b"),
           Scenario("c1b", 1, "b", changes={"reactor.capex_usd_per_kwe": 3000.0}),
           Scenario("c0_cp", 0, "a", changes={"finance.carbon_price_usd_per_t": 30.0}),
           Scenario("c1_cp", 1, "a", changes={"finance.carbon_price_usd_per_t": 30.0})]
    table, results = run_scenarios(scs, params, {"a": series, "b": other})
    table = add_margins(table)
    row = table.set_index("name")
    assert row.loc["c1", "margin"] == pytest.approx(grid_cost_margin(results["c1"].tac, results["c0"].tac))
    assert row.loc["c1_cp", "tac_grid_only"] == pytest.approx(results["c0_cp"].tac)
    assert row.loc["c1b", "tac_grid_only"] == pytest.approx(results["c0b"].tac)
    assert row.loc["c0", "margin"] == pytest.approx(0.0)
    fixed = add_margins(table.drop(columns=["tac_grid_only", "margin"]), match_on=("series",),
                        reference={"a": 1e8, "b": 1e8})
    assert fixed.set_index("name").loc["c1", "margin"] == pytest.approx(1 - results["c1"].tac / 1e8)
    with pytest.raises(Exception):
        run_scenarios([Scenario("x", 0, "missing")], params, {"a": series})
    with pytest.raises(Exception):
        run_scenarios([Scenario("x", 0, "a"), Scenario("x", 1, "a")], params, {"a": series})
    neg = add_margins(table.drop(columns=["tac_grid_only", "margin"]), match_on=("series",),
                      reference={"a": -1.0, "b": 0.0})
    assert neg["margin"].isna().all()
    dup = pd.concat([table, table[table["name"] == "c0"]])
    with pytest.raises(ValueError):
        add_margins(dup)
