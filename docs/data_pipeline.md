# Data pipeline and input schemas

This document describes how the model inputs are built from public sources, and the input format the framework reads.

- It follows the input families of the article (Section 2.3, Supplementary Note 6 and Supplementary Tables S7–S8). The article lists its baseline values and sources there; they are not repeated here.
- The repository contains no input data. [`examples/prepare_inputs.py`](../examples/prepare_inputs.py) runs every generic step below on invented raw records, and `smr_dc_cogen.synthetic` produces a complete synthetic input set.

## Overview

The inputs fall into three families:

- exogenous hourly series: IT load, electricity price, average grid carbon intensity, wet-bulb temperature, and the gas price for the NGCC case;
- equipment parameters: capital and operating cost coefficients, plus technical bounds for each block;
- financial and policy parameters: WACC, asset lives, the Section 45Y credit and the carbon price.

| Input | Symbol | Public source type (as used in the article) | Preparation (helper) | Stored as |
|---|---|---|---|---|
| electricity price | $`\pi_\mathrm{g}(t)`$ | ERCOT day-ahead settlement point prices at a hub, from the ERCOT Market Information System (report NP4-183-CD), retrieved with the open-source `gridstatus` package | one value per hour in market time; leap years trimmed (`trim_to_year`) | `timeseries.csv`: `price_usd_per_mwh` |
| grid carbon intensity | $`\theta(t)`$ | U.S. EIA Form 930 Hourly Electric Grid Monitor, generation by fuel for the balancing authority, weighted by technology life-cycle emission factors (UNECE 2022 life-cycle assessment) | hourly average emission factor (`average_carbon_intensity`) | `carbon_g_per_kwh` |
| wet-bulb temperature | $`T_\mathrm{wb}(t)`$ | Open-Meteo historical-weather archive (ECMWF ERA5 reanalysis): hourly dry-bulb temperature and relative humidity at the site | Stull (2011) wet-bulb formula (`wet_bulb_stull`) | `wet_bulb_c` |
| IT load | $`P_\mathrm{IT}(t)`$ | a published data-center workload power profile; the article's source and its processing are described in its Section 2.1 and Supplementary Note 6 | hourly means (`resample_hourly_mean`), scaling to the facility (`scale_to_peak` or a fixed factor), weekday alignment with the market year (`align_weekdays`) | `it_load_mw` |
| hub gas price | $`\pi_\mathrm{ng}(t)`$ less basis | U.S. EIA Henry Hub daily natural-gas spot price | non-trading days forward-filled, each day repeated over its hours (`daily_to_hourly`) | `gas_usd_per_mmbtu` |
| regional gas basis | part of $`\pi_\mathrm{ng}`$ | published regional hub differentials | one constant | `gas_plant.gas_basis_usd_per_mmbtu` |
| reactor and turbine | $`\overline{P}_\mathrm{rx}`$, $`\underline{P}_\mathrm{rx}`$, $`\Delta P_\mathrm{rx}^{\max}`$, $`a_\mathrm{w}`$, $`b_\mathrm{w}`$, $`\xi_\mathrm{aux}`$ | vendor design basis and public regulatory or IAEA ARIS descriptions; turbine part-load heat-rate characteristic | Willans line through the design point (`heat_rate_to_thermal`, `fit_willans`) | `reactor.*`, `turbine.*` |
| extraction penalty | $`\alpha_\mathrm{w}`$ | steam tables at the extraction-tap condition | heat balance (`extraction_penalty_from_heat_balance`) | `turbine.extraction_penalty` |
| capacity factor | $`\overline{\mathrm{CF}}`$ | EIA *Electric Power Monthly*, nuclear fleet capacity factors | outage length (`Outage.from_capacity_factor`) | `outage.*` |
| absorber | $`\mathrm{COP}_\mathrm{a}^{\,0}`$, $`\delta`$, $`\overline{\mathrm{COP}}_\mathrm{a}`$, $`T_\mathrm{cry}`$, $`\Delta T_\mathrm{cw}`$, $`\beta_\mathrm{a}`$ | double-effect LiBr–H₂O vendor datasheets and absorption-refrigeration references | – | `absorber.*` |
| vapor-compression chiller | $`\mathrm{COP}_\mathrm{v}`$, part-load shape | water-cooled centrifugal chiller ratings; ANSI/AHRI Standard 550/590 part-load (IPLV) weighting | breakpoints of relative COP versus load fraction | `chiller.*` |
| capital and O&M costs | $`\mathrm{CAPEX}_k`$, $`\mathrm{FOM}_k`$, $`v_x`$ | Annual Technology Baseline (ATB) of the National Laboratory of the Rockies (NLR) for the reactor, NGCC and battery. Alternative reactor-capital anchors: public project budgets and vendor targets. Absorber: U.S. DOE combined-heat-and-power fact sheets and vendor literature. VCC: ASHRAE engineering practice | – | `*.capex_*`, `*.fom_*`, `*.vom_*` |
| nuclear fuel cost | $`\pi_\mathrm{f}^\mathrm{e}`$ | published fleet fuel-cost statistics (net-electric basis) | converted to the thermal basis in the model | `reactor.fuel_usd_per_mwh_e` |
| emission factors | $`\gamma_\mathrm{rx}`$, $`\gamma_\mathrm{ng}`$ | UNECE 2022 life-cycle assessment (nuclear); EIA combustion factors plus upstream-methane literature (NGCC) | – | `reactor.lifecycle_g_per_kwh`, `gas_plant.co2_*` |
| finance and policy | $`i`$, $`N_k`$, $`\bar{c}`$ | ATB financial assumptions and asset lives; 26 U.S.C. §45Y, the annual Federal Register inflation adjustment and IRS guidance | levelization of the credit (Eq. 12) | `finance.*`, `*.lifetime_yr` |
| secondary-endpoint factors | $`w_x`$, $`f_\mathrm{sc}`$, $`e_k`$ | water consumption by generation technology (Macknick et al. 2012); basin scarcity (WRI Aqueduct, AWARE); embodied energy of power plants (Lenzen 2008) | $`w_\mathrm{a}`$ from $`w_\mathrm{v}`$ (`absorber_water_factor`) | `EndpointFactors` |

## Steps

### 1. Horizon and calendar

- Choose a market year and a site. All hourly series of a run must belong to that year, in the same local clock (market settlement time), with one value per hour.
- **Leap years:** `trim_to_year` keeps the first 8,760 records, so the last day of a leap year is dropped. This also shifts the calendar date of a fixed hour index by one day in leap years.
- **Daylight-saving transitions:** hourly records in local time have one missing and one repeated hour per year. Keep the source's convention (one record per settlement interval) and check that the year has exactly 8,760 rows after trimming.

### 2. Electricity price

- Use the day-ahead settlement point price of the hub or node that the site would face. Download one year of hourly day-ahead prices.
- Negative prices are kept: the model handles them (imports are paid for, and exports cost money in such hours).
- Real-time prices can replace day-ahead prices for a sensitivity, after averaging each hour's intervals.

### 3. Grid carbon intensity

1. Download the EIA-930 balancing-authority file for the year and keep the generation by fuel.
2. Map the EIA fuel categories to the technologies of the emission-factor table. EIA changed its category split in 2024; aggregate back to one consistent set.
3. Compute the hourly average emission factor, $`\sum_f g_f\,\mathrm{EF}_f / \sum_f g_f`$ (`average_carbon_intensity`). Negative values, such as storage charging, are treated as zero generation.
4. Align EIA-930 timestamps (UTC and local columns) with the price clock.

This is an average emission factor. The article uses it as a tractable proxy for the marginal factor; marginal factors from other sources can be substituted in the same column.

### 4. Wet-bulb temperature

1. Download hourly 2-m dry-bulb temperature and relative humidity at the site for the market year (Open-Meteo historical archive, ERA5).
2. Compute the wet-bulb temperature with the Stull (2011) formula (`wet_bulb_stull`; valid near sea level for RH 5–99 % and −20 to 50 °C). A psychrometric routine with station pressure can be used instead at elevation.

The same series drives the absorber COP (Eq. 4), its crystallization gate (Eq. 5) and the VCC relief multiplier.

### 5. IT load

1. Start from a public whole-facility workload power profile. Resample it to hourly means (`resample_hourly_mean`), then scale it to the facility: the article scales a smaller facility's profile by a constant factor.
2. If the profile belongs to another calendar year, shift it circularly by whole days so that day of week and hour of day line up with the market year (`align_weekdays`). This keeps the seasonal position and avoids pairing weekend troughs with weekday price peaks.
3. Use the same trace in every case and scenario. The size sweep scales it (`TimeSeries.scaled_load`).

This repository does not redistribute any workload dataset. Check the terms of use of the profile you choose.

### 6. Gas price (Case 3)

1. Download the EIA Henry Hub daily spot price. `daily_to_hourly` forward-fills weekends and holidays from the last trading day, and repeats each day's value over its 24 hours.
2. Put the regional basis, a constant added to every hour, in `gas_plant.gas_basis_usd_per_mmbtu`.

### 7. Turbine, extraction and outage

1. **Willans line.** Take the turbine's part-load heat-rate characteristic, as gross output and gross heat rate. Convert it to thermal input with $`P_\mathrm{rx} = P_\mathrm{tg}\,\mathrm{HR}/3600`$ (`heat_rate_to_thermal`). Fit $`P_\mathrm{tg} = a_\mathrm{w} P_\mathrm{rx} - b_\mathrm{w}`$ by least squares, constrained through the design point (`fit_willans`, which also returns $`R^2`$).
2. **Extraction penalty.** At the extraction-tap condition, use steam tables to obtain, per kilogram of extracted steam:
   - the low-pressure expansion work forfeited;
   - the heat delivered to the absorber generator;
   - the work returned through the hot-condensate credit.

   Then $`\alpha_\mathrm{w} = (\text{lost work} - \text{credit})/\text{heat}`$ (`extraction_penalty_from_heat_balance`). A parallel main-steam tap would instead be charged at the full turbine efficiency.
3. **Outage.** Choose the start hour of the refueling outage, in a mild-load shoulder season, and its length from the fleet capacity factor (`Outage.from_capacity_factor`).

### 8. Costs, finance and policy

1. **Units.** Express every capital cost per kW of the asset's size unit: kW_e net for the reactor and NGCC, kW_c for the chillers, kWh for the battery. Use one constant-dollar year for all costs and prices.
2. **Lives.** Use one engineering life per asset; capital is annualized over each asset's own life.
3. **45Y credit.** Use the statutory amount for the year, at the rate that applies (for example the prevailing-wage rate), and the statutory credit period. Choose the cost-comparison window over which Eq. (12) levelizes it.
4. **Nuclear fuel.** Give the fuel cost on the net-electric basis; the model converts it to the thermal basis with the rated net and thermal outputs.

### 9. Provenance record

For every value used in a study, keep one row in a provenance table with these columns:

- parameter,
- value,
- unit,
- source (citation, URL and access date),
- status: `public_reported` (taken directly from a public source), `public_derived` (computed from public data by a documented step), `assumption` (an analyst's choice), or `sensitivity` (a level of a sweep).

The framework does not read this table; it records how each model input was obtained from the raw files.

## Input directory format

`read_inputs(dir)` and `smr-dc-cogen run DIR` read two files, and `write_inputs` writes them.

### `timeseries.csv`

| Column | Unit | Required | Checks |
|---|---|---|---|
| `hour` | – | yes | 0, 1, 2, … without gaps |
| `it_load_mw` | MW_e | yes | finite, ≥ 0 |
| `price_usd_per_mwh` | $/MWh_e | yes | finite (may be negative) |
| `carbon_g_per_kwh` | g CO₂/kWh_e | yes | finite, ≥ 0 |
| `wet_bulb_c` | °C | yes | finite |
| `gas_usd_per_mmbtu` | $/MMBtu | Case 3 only | finite |

- The file holds at most 8,760 rows. Unknown columns are refused.
- A shorter contiguous block is allowed: costs are annualized with $`w = 8760/\lvert\mathcal{T}\rvert`$ ([formulation.md](formulation.md#conventions-of-this-implementation)). Outage hours count from the first row.

### `parameters.json`

One object per block. Field names carry their units. Unknown blocks or fields, missing fields, non-finite numbers and out-of-range values are refused with the block and field name. Blocks that a case does not need may be left out; `site`, `finance` and `chiller` are always required.

| Block | Fields (unit) |
|---|---|
| `site` | `chain_efficiency` (–) |
| `finance` | `wacc` (–), `ptc_usd_per_mwh`, `ptc_credit_years`, `ptc_window_years` (yr), `carbon_price_usd_per_t` |
| `grid` | `pcc_mw` |
| `chiller` | `nameplate_mwc`, `cop_design` (–), `design_wet_bulb_c`, `relief_per_k` (1/K), `relief_cap` (–), `part_load_fraction` (list, 0 … 1), `part_load_relative_cop` (list, 1.0 at full load), `capex_usd_per_kwc`, `fom_usd_per_kwc_yr`, `vom_usd_per_mwhc`, `lifetime_yr` |
| `reactor` | `thermal_mw`, `min_load_fraction` (–), `ramp_mw_per_h`, `capex_usd_per_kwe`, `fom_usd_per_kwe_yr`, `vom_usd_per_mwh`, `fuel_usd_per_mwh_e`, `lifetime_yr`, `lifecycle_g_per_kwh`, `net_rating_mwe` (optional), `ptc_eligible` (true/false) |
| `turbine` | `willans_slope` (MW_e/MW_th), `willans_intercept_mw`, `extraction_penalty` (MW_e/MW_th), `aux_fraction` (–), optional `capex_usd_per_kwe`, `fom_usd_per_kwe_yr`, `vom_usd_per_mwh` (default 0: bundled) |
| `outage` | `start_hour`, `length_h` |
| `absorber` | `nameplate_mwc`, `cop_design`, `design_wet_bulb_c`, `derating_per_k`, `cop_cap`, `tower_approach_k`, `crystallization_c`, `parasitic_kwe_per_kwc`, `capex_usd_per_kwc`, `fom_usd_per_kwc_yr`, `vom_usd_per_mwhc`, `lifetime_yr`, optional `cop_floor` |
| `battery` | `energy_mwh`, `power_mw`, `round_trip_efficiency`, `self_discharge_per_h`, `soc_min_fraction`, `soc_max_fraction`, `initial_soc_fraction`, `capex_usd_per_kwh`, `fom_usd_per_kw_yr`, `vom_usd_per_mwh`, `lifetime_yr`, optional `salvage_fraction` |
| `gas_plant` | `rating_mwe`, `hhv_efficiency`, `co2_direct_g_per_kwh`, `co2_upstream_g_per_kwh`, `part_load_fraction` (list, ending at 1), `part_load_heat_rate_multiplier` (list, ≥ 1, 1.0 at full load), `gas_basis_usd_per_mmbtu`, `capex_usd_per_kwe`, `fom_usd_per_kwe_yr`, `vom_usd_per_mwh`, `lifetime_yr` |

`smr-dc-cogen synthetic DIR` writes a complete example of both files.

## Data preparation rules and checks

- **Alignment:** one clock and one year for all series of a run. The IT trace is aligned to the market year's weekdays.
- **Completeness:** no missing hours. Fill gaps only by documented rules (forward fill of daily prices; linear interpolation of short weather gaps) and record them in the provenance table.
- **Units:** convert at preparation time, not in the model. Prices in $/MWh_e, intensities in g/kWh_e (equal to kg/MWh_e), temperatures in °C, capital per kW or kWh of the stated size unit.
- **Plausibility checks after preparation:**
  - the annual mean and the range of each series;
  - the share of hours above the crystallization gate, $`T_\mathrm{cry} - \Delta T_\mathrm{cw}`$;
  - the number of negative-price hours;
  - the peak cooling demand $`\max_t P_\mathrm{IT}(t)/\eta_\mathrm{ch}`$ against the chiller nameplate;
  - the peak import against the PCC.
- **Checks made by the code:**
  - series lengths, finiteness and signs;
  - parameter ranges, curve ordering and end points;
  - a positive VCC relief multiplier in every hour;
  - chiller, PCC and NGCC capacity in the closed-form cases.

## Terms of use of the sources

- **ERCOT** market data: public reports, subject to the ERCOT website terms of use.
- **U.S. EIA** data (EIA-930, Henry Hub, *Electric Power Monthly*): U.S. government works, in the public domain; cite EIA.
- **Open-Meteo:** CC BY 4.0. Attribute Open-Meteo and the ERA5 reanalysis (Hersbach et al. 2020, *Q. J. R. Meteorol. Soc.* 146, 1999–2049).
- **NLR ATB:** openly licensed. Cite the ATB edition used.
- **UNECE** life-cycle assessment: public report; cite it.
- **Vendor datasheets and standards** (AHRI 550/590, ASHRAE): cite them; do not redistribute their files.
- **Workload profiles:** follow the licence of the dataset you use.
