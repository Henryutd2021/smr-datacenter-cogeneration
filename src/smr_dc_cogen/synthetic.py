"""Synthetic inputs for examples and tests.

Everything here is invented: a smooth IT load with daily and weekly cycles, a price series with an evening peak,
a few negative-price night hours and one spike, a carbon intensity that dips at midday, a wet-bulb series with
one hot spell, and round illustrative parameters. The numbers say nothing about any real plant, market or site.
"""

from __future__ import annotations

import numpy as np

from .analysis import EndpointFactors, absorber_water_factor
from .parameters import (Absorber, Battery, Chiller, Finance, GasPlant, Grid, Outage, Parameters, Reactor, Site,
                         Turbine, relief_matching_absorber)
from .timeseries import TimeSeries


def synthetic_timeseries(hours: int = 336, start_day: int = 190, seed: int = 7, label: str = "synthetic") -> TimeSeries:
    """``hours`` hourly values starting on day-of-year ``start_day`` (day 0 is a Monday)."""
    if not 24 <= hours <= 8760:
        raise ValueError("hours must be between 24 and 8760")
    rng = np.random.default_rng(seed)
    t = np.arange(hours)
    hod = t % 24
    day = t // 24
    doy = (start_day + day) % 365
    season = np.cos(2 * np.pi * (doy - 200) / 365)          # +1 in mid-July, -1 in mid-January
    weekday = ((start_day + day) % 7) < 5

    load = (50 + 3 * season + 6 * np.sin(2 * np.pi * (hod - 9) / 24) + 2.5 * weekday
            + rng.normal(0, 1.2, hours))
    load = np.clip(load, 20, None)

    price = (34 + 10 * season + 16 * np.exp(-((hod - 18) ** 2) / 8) - 6 * np.exp(-((hod - 4) ** 2) / 6)
             + rng.normal(0, 5, hours))
    nights = (hod >= 2) & (hod <= 5) & (day % 5 == 3)
    price[nights] = -12 + rng.normal(0, 2, int(nights.sum()))
    spike = (day % 9 == 8) & (hod >= 16) & (hod <= 20)
    price[spike] += 220

    solar = np.clip(np.sin(np.pi * (hod - 6) / 13), 0, None)
    carbon = np.clip(410 - 120 * solar + 20 * season + rng.normal(0, 12, hours), 60, None)

    wet_bulb = 16 + 6 * season + 3.5 * np.sin(2 * np.pi * (hod - 9) / 24) + rng.normal(0, 0.6, hours)
    hot = (day % 11 == 2) & (hod >= 12) & (hod <= 18) & (season > 0.5)
    wet_bulb[hot] += 6.0

    daily_gas = 3.1 + np.cumsum(rng.normal(0, 0.12, hours // 24 + 1))
    gas = np.clip(np.repeat(daily_gas, 24)[:hours], 1.0, None)
    return TimeSeries(load, price, carbon, wet_bulb, gas, label=label)


def illustrative_parameters(hours: int = 336, carbon_price: float = 0.0) -> Parameters:
    """Round illustrative parameters sized for :func:`synthetic_timeseries`.

    The refueling outage covers 5 % of the horizon, starting at 36 % of it.
    """
    absorber = Absorber(nameplate_mwc=80.0, cop_design=1.2, design_wet_bulb_c=24.0, derating_per_k=0.02,
                        cop_cap=1.38, tower_approach_k=4.0, crystallization_c=32.0, parasitic_kwe_per_kwc=0.03,
                        capex_usd_per_kwc=500.0, fom_usd_per_kwc_yr=6.0, vom_usd_per_mwhc=0.6, lifetime_yr=20)
    relief_slope, relief_cap = relief_matching_absorber(absorber)   # both chillers respond alike to the wet-bulb
    return Parameters(
        site=Site(chain_efficiency=0.93),
        finance=Finance(wacc=0.07, ptc_usd_per_mwh=28.0, ptc_credit_years=8, ptc_window_years=25,
                        carbon_price_usd_per_t=carbon_price),
        grid=Grid(pcc_mw=250.0),
        chiller=Chiller(nameplate_mwc=90.0, cop_design=4.0, design_wet_bulb_c=24.0, relief_per_k=relief_slope,
                        relief_cap=relief_cap,
                        part_load_fraction=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                        part_load_relative_cop=(0.0, 0.75, 1.08, 1.15, 1.06, 1.0),
                        capex_usd_per_kwc=300.0, fom_usd_per_kwc_yr=6.0, vom_usd_per_mwhc=0.4, lifetime_yr=20),
        reactor=Reactor(thermal_mw=600.0, min_load_fraction=0.6, ramp_mw_per_h=90.0, capex_usd_per_kwe=6000.0,
                        fom_usd_per_kwe_yr=110.0, vom_usd_per_mwh=3.0, fuel_usd_per_mwh_e=9.0, lifetime_yr=35,
                        lifecycle_g_per_kwh=10.0),
        turbine=Turbine(willans_slope=0.36, willans_intercept_mw=18.0, extraction_penalty=0.17, aux_fraction=0.08),
        outage=Outage.from_capacity_factor(0.95, start_hour=int(0.36 * hours), horizon_h=hours),
        absorber=absorber,
        battery=Battery(energy_mwh=30.0, power_mw=20.0, round_trip_efficiency=0.88, self_discharge_per_h=2e-5,
                        soc_min_fraction=0.1, soc_max_fraction=0.9, initial_soc_fraction=0.4, capex_usd_per_kwh=400.0,
                        fom_usd_per_kw_yr=20.0, vom_usd_per_mwh=1.0, lifetime_yr=12, salvage_fraction=0.1),
        gas_plant=GasPlant(rating_mwe=120.0, hhv_efficiency=0.52, co2_direct_g_per_kwh=350.0,
                           co2_upstream_g_per_kwh=50.0,
                           part_load_fraction=(0.2, 0.4, 0.6, 0.8, 1.0),
                           part_load_heat_rate_multiplier=(1.6, 1.25, 1.12, 1.03, 1.0),
                           gas_basis_usd_per_mmbtu=0.25, capex_usd_per_kwe=1100.0, fom_usd_per_kwe_yr=25.0,
                           vom_usd_per_mwh=3.0, lifetime_yr=28),
    ).validate()


def illustrative_endpoint_factors() -> EndpointFactors:
    """Round illustrative water and embodied-energy factors."""
    return EndpointFactors(water_chiller_l_per_kwhc=0.12,
                           water_absorber_l_per_kwhc=absorber_water_factor(0.12, 1.25),
                           water_reactor_l_per_kwh=2.3, water_gas_l_per_kwh=0.7, water_grid_l_per_kwh=1.2,
                           scarcity_factor=0.8, embodied_reactor_mwh_per_kw=4.0, embodied_gas_mwh_per_kw=0.9)


__all__ = ["synthetic_timeseries", "illustrative_parameters", "illustrative_endpoint_factors"]
