import numpy as np
import pandas as pd
import pytest

from smr_dc_cogen import Outage, ParameterError, SeriesError, TimeSeries
from smr_dc_cogen.physics import (absorber_available, absorber_cop, chiller_breakpoints, chiller_power,
                                  chiller_relief, cooling_demand, gas_heat_rate_multiplier, outage_mask)
from smr_dc_cogen.timeseries import (align_weekdays, average_carbon_intensity, daily_to_hourly, fit_willans,
                                     heat_rate_to_thermal, resample_hourly_mean, scale_to_peak, trim_to_year,
                                     wet_bulb_stull)


def test_eq1_cooling_demand(params):
    assert cooling_demand([10.0, 0.0], params.site) == pytest.approx([10.0 / params.site.chain_efficiency, 0.0])


def test_eq4_absorber_cop(params):
    ab = params.absorber
    t0 = ab.design_wet_bulb_c
    cop = absorber_cop([t0, t0 + 2.0, t0 - 50.0, t0 + 500.0], ab)
    assert cop[0] == pytest.approx(ab.cop_design)
    assert cop[1] == pytest.approx(ab.cop_design - 2.0 * ab.derating_per_k)
    assert cop[2] == pytest.approx(ab.cop_cap)            # capped in cool hours
    assert cop[3] == pytest.approx(ab.cop_floor)          # numerical floor


def test_eq5_availability(params):
    ab = params.absorber
    gate = ab.crystallization_c - ab.tower_approach_k
    a = absorber_available([gate - 1, gate, gate + 0.01, gate - 1], ab, np.array([False, False, False, True]))
    assert a.tolist() == [1.0, 1.0, 0.0, 0.0]


def test_chiller_relief(params):
    ch = params.chiller
    r = chiller_relief([ch.design_wet_bulb_c, ch.design_wet_bulb_c + 1, ch.design_wet_bulb_c - 100], ch)
    assert r == pytest.approx([1.0, 1 - ch.relief_per_k, ch.relief_cap])
    with pytest.raises(ParameterError):
        chiller_relief([ch.design_wet_bulb_c + 1.0 / ch.relief_per_k + 1], ch)


def test_relief_matches_absorber(params):
    from smr_dc_cogen import relief_matching_absorber
    ab, ch = params.absorber, params.chiller
    slope, cap = relief_matching_absorber(ab)
    assert (slope, cap) == pytest.approx((ab.derating_per_k / ab.cop_design, ab.cop_cap / ab.cop_design))
    # the illustrative chiller uses the matching relief: both COPs move by the same factor below the cap
    assert ch.design_wet_bulb_c == ab.design_wet_bulb_c
    for t in (ab.design_wet_bulb_c + 1.5, ab.design_wet_bulb_c - 2.0, ab.design_wet_bulb_c - 40.0):  # hot, cool, capped
        assert chiller_relief([t], ch)[0] == pytest.approx(absorber_cop([t], ab)[0] / ab.cop_design)


def test_chiller_map(params):
    ch = params.chiller
    q, p = chiller_breakpoints(ch)
    lf = np.array(ch.part_load_fraction)
    rel = np.array(ch.part_load_relative_cop)
    assert q == pytest.approx(lf * ch.nameplate_mwc)
    assert p[0] == 0.0 and p[1:] == pytest.approx(q[1:] / (ch.cop_design * rel[1:]))
    wb = np.full(q.size, ch.design_wet_bulb_c)
    assert chiller_power(q, wb, ch) == pytest.approx(p)
    mid = 0.5 * (q[2] + q[3])
    assert chiller_power([mid], [ch.design_wet_bulb_c], ch)[0] == pytest.approx(0.5 * (p[2] + p[3]))
    hot = ch.design_wet_bulb_c + 5
    assert chiller_power([q[-1]], [hot], ch)[0] == pytest.approx(p[-1] / (1 - 5 * ch.relief_per_k))
    with pytest.raises(ParameterError):
        chiller_power([ch.nameplate_mwc * 1.01], [20.0], ch)


def test_chiller_map_is_nonconvex_in_example(params):
    q, p = chiller_breakpoints(params.chiller)
    slopes = np.diff(p) / np.diff(q)
    assert np.any(np.diff(slopes) < 0) and np.any(np.diff(slopes) > 0)


def test_gas_multiplier(params):
    gp = params.gas_plant
    x, y = gp.part_load_fraction, gp.part_load_heat_rate_multiplier
    assert gas_heat_rate_multiplier([x[0], x[-1], 0.5 * (x[1] + x[2])], gp) == pytest.approx(
        [y[0], 1.0, 0.5 * (y[1] + y[2])])
    assert gas_heat_rate_multiplier([0.0], gp)[0] == pytest.approx(y[0])     # held at the end value


def test_outage_mask():
    m = outage_mask(Outage(3, 4), 10)
    assert m.tolist() == [False] * 3 + [True] * 4 + [False] * 3
    assert outage_mask(Outage(8, 5), 10).sum() == 2                          # cut at the horizon
    assert not outage_mask(None, 5).any()
    with pytest.raises(ParameterError):
        outage_mask(Outage(10, 2), 10)


def test_trim_and_weekday_alignment():
    year = np.arange(8784.0)
    assert trim_to_year(year).size == 8760 and trim_to_year(year)[-1] == 8759
    # trace year 2018 starts on a Monday, 2023 on a Sunday: shift by 6 days
    trace = np.repeat(np.arange(365) % 7, 24).astype(float)       # weekday index of 2018 (Monday = 0)
    aligned = align_weekdays(trace, 2018, 2023)
    assert aligned[0] == 6                                         # 1 Jan 2023 is a Sunday
    assert np.array_equal(align_weekdays(trace, 2018, 2018), trace)
    with pytest.raises(SeriesError):
        align_weekdays(np.ones(25), 2018, 2020)


def test_resample_and_scale():
    idx = pd.date_range("2021-01-01", periods=8, freq="15min")
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8.0], index=idx)
    assert resample_hourly_mean(s).tolist() == [2.5, 6.5]
    assert scale_to_peak([1.0, 2.0, 4.0], 10.0).tolist() == [2.5, 5.0, 10.0]


def test_daily_to_hourly_fills_gaps():
    daily = pd.Series([2.0, 3.0], index=pd.to_datetime(["2021-01-01", "2021-01-04"]))
    hourly = daily_to_hourly(daily, 2021, hours=24 * 5)
    assert hourly[:72].tolist() == [2.0] * 72 and hourly[72:].tolist() == [3.0] * 48
    # a quote from the previous year carries over the New Year holidays
    daily = pd.Series([7.0, 9.0], index=pd.to_datetime(["2020-12-31", "2021-01-04"]))
    assert daily_to_hourly(daily, 2021, hours=24 * 5)[::24].tolist() == [7.0, 7.0, 7.0, 9.0, 9.0]
    with pytest.raises(SeriesError):
        daily_to_hourly(pd.Series([1.0], index=pd.to_datetime(["2022-01-01"])), 2021)


def test_wet_bulb_stull_reference():
    # reference case of Stull (2011): T = 20 degC, RH = 50 %  ->  Tw = 13.7 degC
    assert float(wet_bulb_stull(20.0, 50.0)) == pytest.approx(13.7, abs=0.05)
    with pytest.raises(SeriesError):
        wet_bulb_stull(20.0, 120.0)


def test_average_carbon_intensity():
    gen = pd.DataFrame({"gas": [100.0, 0.0], "wind": [100.0, 50.0], "storage": [-10.0, 0.0]})
    ci = average_carbon_intensity(gen, {"gas": 400.0, "wind": 10.0, "storage": 0.0})
    assert ci == pytest.approx([205.0, 10.0])
    with pytest.raises(SeriesError):
        average_carbon_intensity(gen, {"gas": 400.0})
    with pytest.raises(SeriesError, match="missing"):
        average_carbon_intensity(gen.assign(gas=[np.nan, 0.0]), {"gas": 400.0, "wind": 10.0, "storage": 0.0})
    with pytest.raises(SeriesError):
        average_carbon_intensity(gen, {"gas": np.nan, "wind": 10.0, "storage": 0.0})


def test_willans_fit_through_design_point():
    thermal = np.array([200.0, 250.0, 300.0, 350.0, 400.0])
    gross = 0.35 * thermal - 12.0
    a, b, r2 = fit_willans(thermal, gross, 400.0, 0.35 * 400 - 12.0)
    assert (a, b, r2) == pytest.approx((0.35, 12.0, 1.0))
    noisy = gross + np.array([0.5, -0.5, 0.3, -0.2, 0.0])
    a, b, _ = fit_willans(thermal, noisy, 400.0, 128.0)
    assert a * 400.0 - b == pytest.approx(128.0)
    assert heat_rate_to_thermal([100.0], [10800.0]) == pytest.approx([300.0])


def test_timeseries_validation():
    ok = dict(it_load_mw=[1.0, 2.0], price_usd_per_mwh=[-5.0, 10.0], carbon_g_per_kwh=[300.0, 200.0],
              wet_bulb_c=[20.0, 21.0])
    ts = TimeSeries(**ok)
    assert ts.hours == 2 and ts.weight == pytest.approx(4380.0)
    for change in ({"it_load_mw": [1.0]}, {"it_load_mw": [1.0, -1.0]}, {"price_usd_per_mwh": [1.0, np.nan]},
                   {"carbon_g_per_kwh": [-1.0, 1.0]}, {"wet_bulb_c": [1.0, np.inf]}):
        with pytest.raises(SeriesError):
            TimeSeries(**{**ok, **change})
    with pytest.raises(SeriesError):
        TimeSeries(np.ones(8761), np.ones(8761), np.ones(8761), np.ones(8761))
    with pytest.raises(ValueError):
        ts.it_load_mw[0] = 5.0                      # read-only arrays


def test_timeseries_frame_round_trip(series):
    again = TimeSeries.from_frame(series.to_frame())
    assert np.array_equal(again.price_usd_per_mwh, series.price_usd_per_mwh)
    df = series.to_frame()
    with pytest.raises(SeriesError, match="missing"):
        TimeSeries.from_frame(df.drop(columns=["wet_bulb_c"]))
    with pytest.raises(SeriesError, match="unknown"):
        TimeSeries.from_frame(df.assign(extra=1.0))
    with pytest.raises(SeriesError, match="hour"):
        TimeSeries.from_frame(df.assign(hour=df["hour"] + 1))
    bad = df.copy()
    bad.loc[3, "price_usd_per_mwh"] = np.nan
    with pytest.raises(SeriesError, match="non-numeric"):
        TimeSeries.from_frame(bad)


def test_window_and_scaling(series):
    w = series.window(10, 24)
    assert w.hours == 24 and w.it_load_mw[0] == series.it_load_mw[10]
    assert series.scaled_load(1.7).it_load_mw == pytest.approx(1.7 * series.it_load_mw)
    with pytest.raises(SeriesError):
        series.window(60, 24)
