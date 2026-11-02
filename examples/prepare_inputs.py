"""From raw records to an input directory: the generic steps of docs/data_pipeline.md on invented raw data.

Steps: wet-bulb temperature from dry-bulb and relative humidity; a sub-hourly load trace to hourly means, scaled
to a facility peak and shifted to the market year's weekdays; daily gas quotes (trading days only) to hours;
an hourly generation mix to an average carbon intensity; the Willans line fitted to turbine part-load points;
the extraction penalty from a heat balance. The raw records are generated here; replace them with downloads from
the public sources listed in docs/data_pipeline.md.

    python examples/prepare_inputs.py [OUTPUT_DIR]
"""

import sys

import numpy as np
import pandas as pd

from smr_dc_cogen import (TimeSeries, extraction_penalty_from_heat_balance, illustrative_parameters, run_case,
                          with_changes, write_inputs)
from smr_dc_cogen.timeseries import (align_weekdays, average_carbon_intensity, daily_to_hourly, fit_willans,
                                     heat_rate_to_thermal, resample_hourly_mean, scale_to_peak, trim_to_year,
                                     wet_bulb_stull)

out_dir = sys.argv[1] if len(sys.argv) > 1 else "outputs/prepared_inputs"
rng = np.random.default_rng(1)
market_year, trace_year = 2021, 2019
hours = 8760

# 1. weather: dry-bulb (degC) and relative humidity (%) -> wet-bulb (Stull 2011)
t = np.arange(8784 if market_year % 4 == 0 else 8760)
dry = 21 + 8 * np.cos(2 * np.pi * (t / 24 - 200) / 365) + 5 * np.sin(2 * np.pi * (t % 24 - 9) / 24)
rh = np.clip(70 - 15 * np.sin(2 * np.pi * (t % 24 - 9) / 24) + rng.normal(0, 4, t.size), 20, 99)
wet_bulb = trim_to_year(wet_bulb_stull(dry, rh))

# 2. IT load: 15-minute trace of another year -> hourly mean -> facility peak -> market-year weekdays
idx = pd.date_range(f"{trace_year}-01-01", periods=hours * 4, freq="15min")
quarter = 0.7 + 0.1 * np.sin(2 * np.pi * (idx.hour - 9) / 24) + 0.05 * (idx.day_of_week < 5) + rng.normal(0, 0.02, idx.size)
hourly_shape = resample_hourly_mean(pd.Series(quarter, index=idx)).to_numpy()[:hours]
it_load = align_weekdays(scale_to_peak(hourly_shape, 65.0), trace_year, market_year)

# 3. electricity price ($/MWh) and generation mix -> average carbon intensity (g/kWh)
price = 35 + 15 * np.exp(-((t[:hours] % 24 - 18) ** 2) / 8) + rng.normal(0, 6, hours)
mix = pd.DataFrame({"gas": 300 + 80 * rng.random(hours), "coal": 120 + 30 * rng.random(hours),
                    "wind": 200 * rng.random(hours), "solar": 150 * np.clip(np.sin(np.pi * (t[:hours] % 24 - 6) / 13), 0, None),
                    "nuclear": np.full(hours, 100.0), "storage": rng.normal(0, 10, hours)})
factors = {"gas": 450.0, "coal": 1000.0, "wind": 14.0, "solar": 40.0, "nuclear": 10.0, "storage": 0.0}  # illustrative
carbon = average_carbon_intensity(mix, factors)

# 4. daily hub gas quotes on trading days -> hourly
days = pd.bdate_range(f"{market_year}-01-01", f"{market_year}-12-31")
gas = daily_to_hourly(pd.Series(3.0 + np.cumsum(rng.normal(0, 0.05, days.size)), index=days), market_year)

ts = TimeSeries(it_load, price, carbon, wet_bulb, gas, label=f"prepared_{market_year}")

# 5. turbine: part-load heat-rate points -> Willans line through the design point
gross = np.array([110.0, 140.0, 170.0, 198.0])
heat_rate = np.array([11300.0, 10850.0, 10550.0, 10360.0])          # kJ/kWh, illustrative
thermal = heat_rate_to_thermal(gross, heat_rate)
a_w, b_w, r2 = fit_willans(thermal, gross, thermal[-1], gross[-1])

# 6. extraction penalty from a heat balance of the tap (kJ per kg of extracted steam, illustrative)
alpha_w = extraction_penalty_from_heat_balance(lost_lp_work_kj_per_kg=400.0, generator_heat_kj_per_kg=2000.0,
                                               condensate_credit_kj_per_kg=60.0)

params = with_changes(illustrative_parameters(hours=hours), {
    "turbine.willans_slope": round(a_w, 4), "turbine.willans_intercept_mw": round(b_w, 3),
    "turbine.extraction_penalty": round(alpha_w, 3), "reactor.thermal_mw": round(float(thermal[-1]), 1)})
write_inputs(out_dir, params, ts)

print(f"wet-bulb {wet_bulb.min():.1f} to {wet_bulb.max():.1f} degC; IT load peak {it_load.max():.1f} MW, "
      f"mean {it_load.mean():.1f} MW; carbon intensity mean {carbon.mean():.0f} g/kWh")
print(f"Willans fit: a_w = {a_w:.4f} MW_e/MW_th, b_w = {b_w:.2f} MW_e, R^2 = {r2:.4f}; alpha_w = {alpha_w:.3f}")
print(f"wrote {out_dir}/timeseries.csv and parameters.json")
case0 = run_case(0, params, ts)
print(f"Case 0 on the prepared year: TAC {case0.tac / 1e6:.3f} million $/yr")
