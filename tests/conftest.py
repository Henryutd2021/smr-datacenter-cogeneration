import numpy as np
import pytest

from smr_dc_cogen import (Absorber, Chiller, Finance, Grid, Parameters, Reactor, Site, TimeSeries, Turbine,
                          illustrative_parameters, synthetic_timeseries)


@pytest.fixture(scope="session")
def series():
    """Three synthetic days (72 h), summer, with the hot spell and negative-price hours of the generator."""
    return synthetic_timeseries(hours=72, start_day=190, seed=3)


@pytest.fixture(scope="session")
def params():
    return illustrative_parameters(hours=72)


@pytest.fixture
def linear_params():
    """Hand-checkable parameters: linear chiller map, absorber COP 1, no credit, no O&M, no outage."""
    return Parameters(
        site=Site(chain_efficiency=1.0),
        finance=Finance(wacc=0.04, ptc_usd_per_mwh=0.0, ptc_credit_years=5, ptc_window_years=15),
        grid=Grid(pcc_mw=500.0),
        chiller=Chiller(nameplate_mwc=40.0, cop_design=3.0, design_wet_bulb_c=20.0, relief_per_k=0.0,
                        relief_cap=1.0, part_load_fraction=(0.0, 0.5, 1.0), part_load_relative_cop=(0.0, 1.0, 1.0),
                        capex_usd_per_kwc=0.0, fom_usd_per_kwc_yr=0.0, vom_usd_per_mwhc=0.0, lifetime_yr=10),
        reactor=Reactor(thermal_mw=300.0, min_load_fraction=0.4, ramp_mw_per_h=300.0, capex_usd_per_kwe=0.0,
                        fom_usd_per_kwe_yr=0.0, vom_usd_per_mwh=0.0, fuel_usd_per_mwh_e=0.0, lifetime_yr=30,
                        lifecycle_g_per_kwh=0.0),
        turbine=Turbine(willans_slope=0.4, willans_intercept_mw=10.0, extraction_penalty=0.1, aux_fraction=0.0),
        absorber=Absorber(nameplate_mwc=40.0, cop_design=1.0, design_wet_bulb_c=20.0, derating_per_k=0.0,
                          cop_cap=1.0, tower_approach_k=6.0, crystallization_c=40.0, parasitic_kwe_per_kwc=0.0,
                          capex_usd_per_kwc=0.0, fom_usd_per_kwc_yr=0.0, vom_usd_per_mwhc=0.0, lifetime_yr=10,
                          cop_floor=0.5),
    ).validate()


def make_series(load, price, carbon=None, wet_bulb=None, gas=None):
    n = len(load)
    carbon = np.full(n, 400.0) if carbon is None else carbon
    wet_bulb = np.full(n, 20.0) if wet_bulb is None else wet_bulb
    return TimeSeries(np.asarray(load, float), np.asarray(price, float), np.asarray(carbon, float),
                      np.asarray(wet_bulb, float), None if gas is None else np.asarray(gas, float))

