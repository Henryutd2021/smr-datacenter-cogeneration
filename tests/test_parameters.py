import dataclasses
import json
import math

import pytest

from smr_dc_cogen import (Outage, ParameterError, annualized_capital, annuity_factor, cop_for_full_load_pue, crf,
                          extraction_penalty_from_heat_balance, from_dict, full_load_pue, levelized_ptc, to_dict,
                          with_changes)


def test_crf_and_annuity():
    i, n = 0.07, 20
    g = (1 + i) ** n
    assert crf(i, n) == pytest.approx(i * g / (g - 1))
    assert annuity_factor(i, n) == pytest.approx((1 - (1 + i) ** -n) / i)
    assert crf(i, n) * annuity_factor(i, n) == pytest.approx(1.0)


def test_levelized_ptc_eq12():
    i = 0.06
    expected = 25.0 * annuity_factor(i, 7) / annuity_factor(i, 22)
    assert levelized_ptc(25.0, i, 7, 22) == pytest.approx(expected)
    assert levelized_ptc(25.0, i, 22, 22) == 25.0       # credit lasts the whole window
    assert levelized_ptc(25.0, i, 30, 22) == 25.0


def test_salvage_credit_sinking_fund():
    p, i, n, s = 1e6, 0.08, 12, 0.15
    c = crf(i, n)
    assert annualized_capital(p, i, n, s) == pytest.approx(p * c - s * p * (c - i))
    assert annualized_capital(p, i, n) == pytest.approx(p * c)


def test_pue_conversions():
    assert full_load_pue(4.0, 0.8) == pytest.approx(1 + 1 / 3.2)
    assert full_load_pue(cop_for_full_load_pue(1.4, 0.95), 0.95) == pytest.approx(1.4)


def test_extraction_penalty_heat_balance():
    assert extraction_penalty_from_heat_balance(450.0, 2000.0, 90.0) == pytest.approx(0.18)
    with pytest.raises(ParameterError):
        extraction_penalty_from_heat_balance(100.0, 2000.0, 200.0)
    with pytest.raises(ParameterError):
        extraction_penalty_from_heat_balance(100.0, 0.0, 10.0)


def test_outage_from_capacity_factor():
    o = Outage.from_capacity_factor(0.9, start_hour=10, horizon_h=200)
    assert (o.start_hour, o.length_h) == (10, 20)
    assert Outage.from_capacity_factor(0.95, 0).length_h == round(0.05 * 8760)


def test_derived_reactor_quantities(params):
    tb, rx = params.turbine, params.reactor
    net = (1 - tb.aux_fraction) * (tb.willans_slope * rx.thermal_mw - tb.willans_intercept_mw)
    assert params.reactor_net_rating_mwe == pytest.approx(net)
    assert params.fuel_usd_per_mwh_th == pytest.approx(rx.fuel_usd_per_mwh_e * net / rx.thermal_mw)
    explicit = with_changes(params, {"reactor.net_rating_mwe": 150.0})
    assert explicit.reactor_net_rating_mwe == 150.0


@pytest.mark.parametrize("key,value", [
    ("finance.wacc", float("nan")),
    ("finance.wacc", 0.0),
    ("finance.ptc_credit_years", 2.5),
    ("site.chain_efficiency", 1.2),
    ("chiller.cop_design", -1.0),
    ("chiller.part_load_fraction", (0.0, 0.5, 0.9)),
    ("chiller.part_load_relative_cop", (0.0, 1.0, 1.0, 1.0, 1.0, 0.9)),
    ("reactor.min_load_fraction", 1.5),
    ("reactor.ramp_mw_per_h", 0.0),
    ("reactor.min_load_fraction", 0.0),            # Eq. (2) would give a negative gross output at zero load
    ("turbine.aux_fraction", 1.0),
    ("absorber.cop_floor", 9.0),
    ("battery.initial_soc_fraction", 0.95),
    ("battery.round_trip_efficiency", float("inf")),
    ("gas_plant.part_load_heat_rate_multiplier", (1.6, 1.25, 0.9, 1.03, 1.0)),
    ("outage.length_h", -1),
    ("grid.pcc_mw", 0.0),
])
def test_invalid_values_rejected(params, key, value):
    with pytest.raises(ParameterError):
        with_changes(params, {key: value})


def test_changes_are_applied_together(params):
    # moving the state-of-charge window: each change alone would be invalid, together they are valid
    p = with_changes(params, {"battery.soc_min_fraction": 0.5, "battery.initial_soc_fraction": 0.6})
    assert (p.battery.soc_min_fraction, p.battery.initial_soc_fraction) == (0.5, 0.6)
    assert params.battery.soc_min_fraction != 0.5            # the original is unchanged
    with pytest.raises(ParameterError):
        with_changes(params, {"battery.soc_min_fraction": 0.5})


@pytest.mark.parametrize("key", ["nonsense.field", "reactor.nonsense", "reactor", "a.b.c"])
def test_unknown_change_keys(params, key):
    with pytest.raises(ParameterError):
        with_changes(params, {key: 1.0})


def test_change_of_unset_block(params):
    p = dataclasses.replace(params, battery=None)
    with pytest.raises(ParameterError):
        with_changes(p, {"battery.energy_mwh": 10.0})


def test_dict_round_trip(params):
    d = to_dict(params)
    again = from_dict(json.loads(json.dumps(d)))
    assert again == params


def test_from_dict_rejects_unknown_and_missing(params):
    d = to_dict(params)
    d["reactor"]["colour"] = "blue"
    with pytest.raises(ParameterError, match="unknown field"):
        from_dict(d)
    d = to_dict(params)
    del d["turbine"]["willans_slope"]
    with pytest.raises(ParameterError, match="missing field"):
        from_dict(d)
    d = to_dict(params)
    del d["site"]
    with pytest.raises(ParameterError, match="required"):
        from_dict(d)
    d = to_dict(params)
    d["reactors"] = {}
    with pytest.raises(ParameterError, match="unknown parameter block"):
        from_dict(d)


def test_levelized_property(params):
    f = params.finance
    assert f.levelized_ptc_usd_per_mwh == pytest.approx(
        f.ptc_usd_per_mwh * annuity_factor(f.wacc, f.ptc_credit_years) / annuity_factor(f.wacc, f.ptc_window_years))
    assert math.isfinite(f.levelized_ptc_usd_per_mwh)
