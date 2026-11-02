# Mathematical formulation

This document states the model as implemented by this framework. The model is that of H. Li, B. She and J. Zhang, "Techno-economic boundary analysis of small modular reactor cogeneration for hyperscale data center IT and cooling loads", *Energy Conversion and Management* (2026, accepted); preprint arXiv:2608.10999, [doi:10.48550/arXiv.2608.10999](https://doi.org/10.48550/arXiv.2608.10999). It covers Section 2 of the main text and Supplementary Notes 7 and 9.

- Equation numbers follow the arXiv preprint (v1): (1)–(13) are in the main text, (S1)–(S14) in its Supplementary Information.
- Relations that the article states in words, without a number, are given here without a number.
- [Conventions of this implementation](#conventions-of-this-implementation) lists the definitions that the framework makes explicit.
- Notation follows the article's nomenclature. The last column of each table gives the name used in the code ([`parameters.py`](../src/smr_dc_cogen/parameters.py), [`model.py`](../src/smr_dc_cogen/model.py), [`results.py`](../src/smr_dc_cogen/results.py)).

## Configurations

Four configurations supply electricity and chilled water to the same data center (article Table 1). They share the IT load, the vapor-compression chiller (VCC) technology and the financial parameters. They differ in the electricity supply and in whether recovered reactor steam drives an absorption chiller.

| Case | Electricity | Cooling | Grid | Solved as |
|---|---|---|---|---|
| 0 | grid purchases | VCC | import only | closed form |
| 1 | reactor and turbine, no heat recovery | VCC | import and export | mixed-integer linear program |
| 2 | reactor and turbine with a mid-pressure extraction to a double-effect LiBr–H₂O absorber | absorber and VCC share dispatch | import and export | mixed-integer linear program |
| 3 | on-site natural gas combined cycle (NGCC) | VCC | none (islanded) | closed form |

A lithium-ion battery can be added to Cases 1 and 2 (the article's battery sensitivity). In Cases 0, 1 and 3 the absorption variables $`Q_\mathrm{a}(t)`$, $`Q_\mathrm{ext}(t)`$ are zero. In Case 3 the reactor and turbine variables are removed and the NGCC serves the IT and chiller load.

## Sets

| Symbol | Meaning | Code |
|---|---|---|
| $`t \in \mathcal{T}`$ | hourly time step; the article uses one year, $`\lvert\mathcal{T}\rvert = 8760`$ | `t` |
| $`c \in \mathcal{C}`$ | configuration, $`\mathcal{C} = \{0,1,2,3\}`$ | `case` |
| $`\mathcal{T}_\mathrm{out} \subset \mathcal{T}`$ | scheduled refueling-outage hours (contiguous) | `outage` |
| $`k`$ | segment of the chiller part-load map (implementation) | `k` |

## Parameters

| Symbol | Meaning | Unit | Code |
|---|---|---|---|
| $`P_\mathrm{IT}(t)`$ | data-center IT load | MW_e | `TimeSeries.it_load_mw` |
| $`\pi_\mathrm{g}(t)`$ | day-ahead locational marginal price | $/MWh_e | `TimeSeries.price_usd_per_mwh` |
| $`\theta(t)`$ | hourly average grid carbon intensity | g CO₂/kWh_e | `TimeSeries.carbon_g_per_kwh` |
| $`T_\mathrm{wb}(t)`$ | wet-bulb temperature | °C | `TimeSeries.wet_bulb_c` |
| $`\pi_\mathrm{ng}(t)`$ | delivered natural-gas price: hub price plus basis | $/MMBtu | `TimeSeries.gas_usd_per_mmbtu` + `gas_plant.gas_basis_usd_per_mmbtu` |
| $`\eta_\mathrm{ch}`$ | chilled-water chain efficiency | – | `site.chain_efficiency` |
| $`\overline{P}_\mathrm{rx}`$, $`\underline{P}_\mathrm{rx}`$ | reactor maximum and minimum thermal output | MW_th | `reactor.thermal_mw`, `reactor.min_load_fraction` × `thermal_mw` |
| $`\Delta P_\mathrm{rx}^{\max}`$ | reactor ramp limit | MW_th/h | `reactor.ramp_mw_per_h` |
| $`\overline{\mathrm{CF}}`$ | reactor annual capacity factor (sets the outage length) | – | `Outage.from_capacity_factor` |
| $`a_\mathrm{w}`$, $`b_\mathrm{w}`$ | Willans-line slope and no-load intercept | MW_e/MW_th, MW_e | `turbine.willans_slope`, `turbine.willans_intercept_mw` |
| $`\alpha_\mathrm{w}`$ | Willans extraction penalty | MW_e/MW_th | `turbine.extraction_penalty` |
| $`\xi_\mathrm{aux}`$ | turbine auxiliary fraction | – | `turbine.aux_fraction` |
| $`\eta_\mathrm{r}`$ | turbine rated electric efficiency at the design point (reported, not used in the constraints) | – | – |
| $`\mathrm{COP}_\mathrm{a}^{\,0}`$, $`\overline{\mathrm{COP}}_\mathrm{a}`$ | absorber COP at the design wet-bulb, and nameplate cap | – | `absorber.cop_design`, `absorber.cop_cap` |
| $`T_\mathrm{wb,0}`$, $`\delta`$ | absorber design wet-bulb and derating slope | °C, °C⁻¹ | `absorber.design_wet_bulb_c`, `absorber.derating_per_k` |
| $`\Delta T_\mathrm{cw}`$, $`T_\mathrm{cry}`$ | cooling-tower approach and LiBr crystallization threshold on the cooling-water inlet | K, °C | `absorber.tower_approach_k`, `absorber.crystallization_c` |
| $`\beta_\mathrm{a}`$ | absorber parasitic load intensity (pumps and tower fans) | kW_e/kW_c | `absorber.parasitic_kwe_per_kwc` |
| $`S_\mathrm{a}`$ | absorber cooling nameplate | MW_c | `absorber.nameplate_mwc` |
| $`\mathrm{COP}_\mathrm{v}`$ | VCC full-load COP at the design wet-bulb | – | `chiller.cop_design` |
| $`f_\mathrm{v}(\cdot)`$ | VCC part-load electricity map | MW_e | `chiller.part_load_fraction`, `chiller.part_load_relative_cop` |
| $`r(t)`$ | VCC wet-bulb relief multiplier on the design-point COP | – | `chiller.relief_per_k`, `chiller.relief_cap`, `chiller.design_wet_bulb_c` |
| $`S_\mathrm{v}`$ | VCC cooling nameplate | MW_c | `chiller.nameplate_mwc` |
| $`\mathrm{Cap}_\mathrm{E}`$, $`\overline{B}`$ | battery energy capacity and power limit | MWh, MW | `battery.energy_mwh`, `battery.power_mw` |
| $`\eta_\mathrm{rt}`$, $`\rho_\mathrm{B}`$ | battery round-trip efficiency and hourly self-discharge | – | `battery.round_trip_efficiency`, `battery.self_discharge_per_h` |
| $`\overline{P}_\mathrm{pcc}`$ | point-of-common-coupling capacity | MW_e | `grid.pcc_mw` |
| $`\overline{P}_\mathrm{ng}`$ | NGCC rated electric capacity | MW_e | `gas_plant.rating_mwe` |
| $`h_\mathrm{ng}`$, $`\eta_\mathrm{hhv}`$ | NGCC full-load HHV heat rate $`h_\mathrm{ng} = 3.412/\eta_\mathrm{hhv}`$ and efficiency | MMBtu/MWh_e, – | `gas_plant.hhv_efficiency` |
| $`\phi_\mathrm{ng}(\ell)`$ | NGCC part-load heat-rate multiplier, $`\phi_\mathrm{ng} \ge 1`$, equal to one at full load | – | `gas_plant.part_load_fraction`, `gas_plant.part_load_heat_rate_multiplier` |
| $`\gamma_\mathrm{rx}`$, $`\gamma_\mathrm{ng}`$ | reactor and NGCC life-cycle emission factors ($`\gamma_\mathrm{ng}`$ = direct combustion + upstream methane) | g CO₂-eq/kWh_e | `reactor.lifecycle_g_per_kwh`, `gas_plant.co2_direct_g_per_kwh` + `co2_upstream_g_per_kwh` |
| $`\mathrm{CAPEX}_k`$, $`N_k`$ | overnight capital per unit of size, and engineering life of asset $`k`$. Per kW_e of net rating for the reactor and NGCC, per kW_c for the chillers, per kWh of energy capacity for the battery | $/kW or $/kWh, yr | `*.capex_usd_per_*`, `*.lifetime_yr` |
| $`\mathrm{FOM}_k`$ | fixed O&M per unit of size and year. Per kW of the same size unit, except the battery: per kW of power rating | $/kW/yr | `*.fom_usd_per_*_yr` |
| $`S_k`$, $`S_k^\mathrm{FOM}`$ | installed size of asset $`k`$ for capital and for fixed O&M: net MW_e for the reactor, MW_c for the chillers, MW_e for the NGCC; for the battery, energy capacity $`\mathrm{Cap}_\mathrm{E}`$ (MWh) for capital and power rating $`\overline{B}`$ (MW) for fixed O&M | MW or MWh | `CaseResult.sizes` |
| $`v_x`$ | variable O&M coefficient of stream $`x`$ | $/MWh | `*.vom_usd_per_*` |
| $`\pi_\mathrm{f}^\mathrm{e}`$, $`\pi_\mathrm{f}`$ | nuclear fuel cost on the net-electric and on the thermal basis | $/MWh_e, $/MWh_th | `reactor.fuel_usd_per_mwh_e`, `Parameters.fuel_usd_per_mwh_th` |
| $`i`$ | weighted-average cost of capital (WACC) | – | `finance.wacc` |
| $`\bar{c}`$, $`c_{45\mathrm{Y}}`$ | statutory and window-levelized Section 45Y credit | $/MWh_e | `finance.ptc_usd_per_mwh`, `Finance.levelized_ptc_usd_per_mwh` |
| $`\pi_\mathrm{CO_2}`$ | carbon price | $/t CO₂ | `finance.carbon_price_usd_per_t` |
| $`\Theta`$ | kg-to-tonne conversion, $`10^{-3}`$ t/kg | t/kg | `THETA_T_PER_KG` |
| $`\Delta t`$ | time-step length, 1 h | h | – |

## Decision variables

| Symbol | Meaning | Unit | Code (model / hourly result column) |
|---|---|---|---|
| $`P_\mathrm{rx}(t)`$ | reactor thermal output | MW_th | `P_rx` / `reactor_mwth` |
| $`P_\mathrm{tg}(t)`$, $`P_\mathrm{tn}(t)`$ | turbine gross and net electric output | MW_e | `P_tg`, `P_tn` / `turbine_gross_mw`, `turbine_net_mw` |
| $`Q_\mathrm{ext}(t)`$ | mid-pressure steam extraction to the absorber | MW_th | `Q_ext` / `extraction_mwth` |
| $`Q_\mathrm{a}(t)`$ | absorption cooling delivered | MW_c | `Q_a` / `absorber_mwc` |
| $`Q_\mathrm{v}(t)`$, $`P_\mathrm{v}(t)`$ | VCC cooling delivered and electric input | MW_c, MW_e | `Q_v`, `P_v` / `chiller_mwc`, `chiller_mw` |
| $`P_\mathrm{g}^{+}(t)`$, $`P_\mathrm{g}^{-}(t)`$ | grid import and export | MW_e | `P_g_import`, `P_g_export` / `grid_import_mw`, `grid_export_mw` |
| $`y(t)`$ | import–export interlock binary (1 = import) | – | `y` |
| $`B^{+}(t)`$, $`B^{-}(t)`$, $`E_\mathrm{B}(t)`$ | battery charge, discharge and state of charge | MW, MW, MWh | `B_charge`, `B_discharge`, `E_B` |
| $`P_\mathrm{ng}(t)`$ | NGCC electric output (Case 3) | MW_e | `gas_plant_mw` |
| $`\delta_k(t)`$, $`z_k(t)`$ | filling fraction of segment $`k`$ of $`f_\mathrm{v}`$ and its ordering binary (implementation of the SOS2 map) | – | `vcc_delta`, `vcc_z` |

## Cooling demand

The full IT electrical draw is heat that the cooling system must reject:

```math
Q_\mathrm{cool}^\mathrm{dem}(t) = \frac{P_\mathrm{IT}(t)}{\eta_\mathrm{ch}} \qquad (1)
```

Each scenario is labeled by its full-load power usage effectiveness (PUE) under vapor-compression cooling, $`1 + 1/(\eta_\mathrm{ch}\,\mathrm{COP}_\mathrm{v})`$ (`full_load_pue`, and `cop_for_full_load_pue` for the inverse). The dispatch-realized annual PUE is $`1 + P_\mathrm{cool}/P_\mathrm{IT}`$ on annual energy, where $`P_\mathrm{cool}`$ counts cooling electricity only (`realized_pue`: chiller drive plus absorber parasitics). The steam that drives the absorber is not counted, so the Case 2 value is an electric-only metric.

## Reactor and turbine block (Cases 1 and 2)

The reactor is a continuous thermal source, bounded, held at zero through the refueling window and ramp-limited:

```math
\underline{P}_\mathrm{rx} \le P_\mathrm{rx}(t) \le \overline{P}_\mathrm{rx}, \quad \forall\, t \in \mathcal{T}\setminus\mathcal{T}_\mathrm{out} \qquad (\mathrm{S7})
```

```math
P_\mathrm{rx}(t) = 0, \quad \forall\, t \in \mathcal{T}_\mathrm{out} \qquad (\mathrm{S8})
```

```math
\lvert P_\mathrm{rx}(t) - P_\mathrm{rx}(t-1)\rvert \le \Delta P_\mathrm{rx}^{\max}, \quad \forall\, t \ge 1 \text{ outside } \mathcal{T}_\mathrm{out} \qquad (\mathrm{S9})
```

- The outage window is $`\mathrm{round}\big((1-\overline{\mathrm{CF}})\cdot 8760\big)`$ contiguous hours, so a plant at full power in every online hour realizes the capacity factor $`\overline{\mathrm{CF}}`$ (`Outage.from_capacity_factor`).
- Shutdown and startup at the window boundaries are managed procedures and are excluded from the ramp limit.
- During the outage the data center buys grid power at the prevailing price.

The cascaded high-pressure / low-pressure turbine follows a Willans line in reactor thermal input, with a penalty for the mid-pressure steam diverted to the absorber, and an auxiliary load:

```math
P_\mathrm{tg}(t) = a_\mathrm{w}\, P_\mathrm{rx}(t) - b_\mathrm{w} - \alpha_\mathrm{w}\, Q_\mathrm{ext}(t) \qquad (2)
```

```math
P_\mathrm{tn}(t) = (1 - \xi_\mathrm{aux})\, P_\mathrm{tg}(t) \qquad (3)
```

with $`P_\mathrm{tg}(t) = 0`$ during the outage window.

- The Willans line is fitted by least squares to the turbine part-load heat-rate characteristic and constrained through the design point (`timeseries.fit_willans`, `heat_rate_to_thermal`).
- The extraction coefficient follows from a steam-table heat balance of the extraction tap: the low-pressure expansion work forfeited per kilogram of extracted steam, net of the hot-condensate-return credit, divided by the heat delivered to the absorber generator (`extraction_penalty_from_heat_balance`). Diverted steam still expands through the high-pressure turbine before extraction.

## Absorption-chiller submodel (Case 2)

The double-effect LiBr–H₂O absorber has a wet-bulb-dependent COP and a binary availability indicator for LiBr crystallization risk:

```math
\mathrm{COP}_\mathrm{a}\big(T_\mathrm{wb}(t)\big) = \max\Big\{ \min\big[\mathrm{COP}_\mathrm{a}^{\,0} - \delta\,(T_\mathrm{wb}(t) - T_\mathrm{wb,0}),\ \overline{\mathrm{COP}}_\mathrm{a}\big],\ 0.5 \Big\} \qquad (4)
```

The lower bound (written as 0.5 in the article) is a numerical safeguard; the crystallization gate acts first. In the code it is `absorber.cop_floor`.

```math
a(t) = \begin{cases} 1 & \text{if } T_\mathrm{wb}(t) + \Delta T_\mathrm{cw} \le T_\mathrm{cry} \text{ and } t \notin \mathcal{T}_\mathrm{out}, \\ 0 & \text{otherwise} \end{cases} \qquad (5)
```

The absorber is fully offline whenever the cooling-water inlet would exceed the crystallization threshold, and throughout the reactor refueling outage, inside which its own annual maintenance is scheduled. No separate availability derate is applied. Delivered absorption cooling is

```math
Q_\mathrm{a}(t) = \mathrm{COP}_\mathrm{a}\big(T_\mathrm{wb}(t)\big)\cdot a(t)\cdot Q_\mathrm{ext}(t) \qquad (6)
```

subject to two physical limits:

- the generator's steam-side capacity, sized to deliver nameplate cooling at the design-point COP: $`Q_\mathrm{ext}(t) \le S_\mathrm{a}/\mathrm{COP}_\mathrm{a}^{\,0}`$ (`Absorber.steam_capacity_mw`);
- the installed cooling nameplate: $`Q_\mathrm{a}(t) \le S_\mathrm{a}`$, which binds in cool hours when the COP rises above its design value.

A parasitic electric load $`\beta_\mathrm{a}\,Q_\mathrm{a}(t)`$ powers the solution and refrigerant pumps and the cooling-tower fans. The VCC carries the residual cooling load.

## Vapor-compression chiller

The chiller electricity is a non-convex, piecewise-linear function of the cooling delivered, anchored at the design-point $`\mathrm{COP}_\mathrm{v}`$ at the design wet-bulb. The COP of a water-cooled centrifugal chiller rises at reduced load, peaks at part load and falls at very low load (integrated part-load value of ANSI/AHRI Standard 550/590). The VCC carries the same relative wet-bulb response as the absorber: the design-point COP is multiplied each hour by

```math
r(t) = \min\big[\, 1 + \sigma_\mathrm{v}\,(T_\mathrm{wb,v} - T_\mathrm{wb}(t)),\ \overline{r}\, \big]
```

where $`\sigma_\mathrm{v}`$ (`chiller.relief_per_k`) and $`\overline{r}`$ (`chiller.relief_cap`) are the absorber's relief slope and cap expressed relative to its design point, $`\sigma_\mathrm{v} = \delta/\mathrm{COP}_\mathrm{a}^{\,0}`$ and $`\overline{r} = \overline{\mathrm{COP}}_\mathrm{a}/\mathrm{COP}_\mathrm{a}^{\,0}`$ (`relief_matching_absorber`), and $`T_\mathrm{wb,v}`$ is the chiller's design wet-bulb. Cooler condenser water benefits both chillers identically, and warmer hours derate both. Hence

```math
P_\mathrm{v}(t) = \frac{f_\mathrm{v}\big(Q_\mathrm{v}(t)\big)}{r(t)}
```

**Breakpoints.** $`f_\mathrm{v}`$ is given by load fractions $`0 = \lambda_0 < \lambda_1 < \dots < \lambda_K = 1`$ and the COP relative to full load at each, $`\rho_k`$ with $`\rho_K = 1`$. Then $`q_k = \lambda_k S_\mathrm{v}`$, $`p_0 = 0`$ and $`p_k = q_k/(\mathrm{COP}_\mathrm{v}\,\rho_k)`$; $`f_\mathrm{v}`$ is linear between breakpoints (`physics.chiller_breakpoints`).

**In the closed-form Cases 0 and 3**, $`f_\mathrm{v}`$ is evaluated directly at the realized load (`physics.chiller_power`).

**In the optimized Cases 1 and 2**, the article enters $`f_\mathrm{v}`$ as a special-ordered-set-of-type-2 (SOS2) piecewise map. This framework writes the same map with the incremental formulation, an exact mixed-integer equivalent that HiGHS and Gurobi both accept:

```math
Q_\mathrm{v}(t) = \sum_{k=1}^{K} (q_k - q_{k-1})\,\delta_k(t), \qquad P_\mathrm{v}(t) = \sum_{k=1}^{K} \frac{p_k - p_{k-1}}{r(t)}\,\delta_k(t)
```

```math
0 \le \delta_k(t) \le 1, \qquad \delta_{k+1}(t) \le z_k(t) \le \delta_k(t), \qquad z_k(t) \in \{0,1\}, \quad k = 1,\dots,K-1
```

A segment can be used only when all earlier segments are full, so $`(Q_\mathrm{v}, P_\mathrm{v})`$ always lies on the graph of $`f_\mathrm{v}/r`$.

## Battery storage (Cases 1 and 2, optional)

```math
E_\mathrm{B}(t) = (1-\rho_\mathrm{B})\,E_\mathrm{B}(t-1) + \big(\sqrt{\eta_\mathrm{rt}}\,B^{+}(t) - B^{-}(t)/\sqrt{\eta_\mathrm{rt}}\big)\Delta t \qquad (\mathrm{S10})
```

- The initial state $`E_\mathrm{B}(0)`$ is a fixed share of $`\mathrm{Cap}_\mathrm{E}`$ (`battery.initial_soc_fraction`) and the end state is restored to it, $`E_\mathrm{B}(\lvert\mathcal{T}\rvert) = E_\mathrm{B}(0)`$.
- State-of-charge bounds are fixed shares of $`\mathrm{Cap}_\mathrm{E}`$; charge and discharge are limited by the power rating.
- No binary prevents simultaneous charge and discharge (as in the article). The results report the number of hours in which both are positive (`battery_simultaneous_hours`).

## Grid exchange

Grid exchange is bounded by the point of common coupling and interlocked by an hourly binary, so the site never buys and sells in the same hour:

```math
0 \le P_\mathrm{g}^{+}(t) \le \overline{P}_\mathrm{pcc}\, y(t), \qquad 0 \le P_\mathrm{g}^{-}(t) \le \overline{P}_\mathrm{pcc}\, \big(1 - y(t)\big) \qquad (7)
```

Case 0 imports only; Case 3 has no grid connection.

## Energy balances

The electric balance equates supply, battery discharge and grid imports to the IT load, chiller drives, absorber parasitics, battery charge and grid exports:

```math
P_\mathrm{tn}(t) + P_\mathrm{g}^{+}(t) + B^{-}(t) = P_\mathrm{IT}(t) + P_\mathrm{v}(t) + \beta_\mathrm{a} Q_\mathrm{a}(t) + P_\mathrm{g}^{-}(t) + B^{+}(t) \qquad (8)
```

The cooling balance equates the two chillers' combined output to the cooling demand of Eq. (1):

```math
Q_\mathrm{a}(t) + Q_\mathrm{v}(t) = Q_\mathrm{cool}^\mathrm{dem}(t)
```

## Objective and cost accounting

The optimization minimizes the total annualized cost (TAC) across seven accounting buckets: capital (CAPEX), fixed O&M (FOM), variable O&M (VOM), fuel, grid exchange, carbon and the production tax credit:

```math
\min\ \mathrm{TAC} = \sum_k \mathrm{CRF}_k\,\mathrm{CAPEX}_k\, S_k + \mathrm{FOM} + \mathrm{VOM} + \Phi_\mathrm{fuel} + \Phi_\mathrm{grid} + \Phi_\mathrm{CO_2} - \Phi_\mathrm{ptc} \qquad (9)
```

**Capital recovery.** The first term annualizes the capital of the reactor, turbine, absorption and vapor-compression chillers, battery and NGCC, sized at $`S_k`$, each over its own engineering life $`N_k`$ at the WACC $`i`$:

```math
\mathrm{CRF}_k = \frac{i\,(1+i)^{N_k}}{(1+i)^{N_k} - 1}
```

- The battery additionally carries an end-of-life salvage credit (`battery.salvage_fraction`; see conventions).
- $`\mathrm{FOM} = \sum_k \mathrm{FOM}_k S_k^\mathrm{FOM}`$, with $`S_k^\mathrm{FOM} = S_k`$ except for the battery, whose fixed O&M is charged on its power rating.
- The turbine is bundled into the reactor's overnight capital cost (its own CAPEX, FOM and VOM default to zero).
- The grid interconnection is not capitalized.

**Grid and carbon terms:**

```math
\Phi_\mathrm{grid} = \sum_t \pi_\mathrm{g}(t)\,\big[P_\mathrm{g}^{+}(t) - P_\mathrm{g}^{-}(t)\big]\Delta t \qquad (10)
```

```math
\Phi_\mathrm{CO_2} = \pi_\mathrm{CO_2}\,\Theta \sum_t \Big[\gamma_\mathrm{rx}\, P_\mathrm{tn}(t) + \theta(t)\,\big(P_\mathrm{g}^{+}(t) - P_\mathrm{g}^{-}(t)\big)\Big]\Delta t \qquad (11)
```

- Reactor generation is charged at its life-cycle factor $`\gamma_\mathrm{rx}`$.
- Imports are charged, and exports credited, at the hourly average grid intensity $`\theta(t)`$, used as a tractable proxy for the marginal emission factor.
- The bracket summed over hours is the case's net carbon footprint (`footprint_t_co2`, after $`\Theta`$).

**Variable O&M and nuclear fuel:**

```math
\mathrm{VOM} = \sum_t \big[v_\mathrm{rx} P_\mathrm{tn}(t) + v_\mathrm{v} Q_\mathrm{v}(t) + v_\mathrm{a} Q_\mathrm{a}(t)\big]\Delta t + \sum_t v_\mathrm{B}\big(B^{+}(t) + B^{-}(t)\big)\Delta t \qquad (\mathrm{S11})
```

```math
\Phi_\mathrm{fuel} = \pi_\mathrm{f} \sum_t P_\mathrm{rx}(t)\,\Delta t \qquad (\mathrm{S12})
```

Nuclear fuel is charged on thermal energy, because extraction does not reduce fuel burn. Its price is anchored on the net-electric basis used by fleet statistics:

```math
\pi_\mathrm{f} = \pi_\mathrm{f}^\mathrm{e}\,\big(\overline{P}_\mathrm{tn}/\overline{P}_\mathrm{rx}\big)
```

where $`\overline{P}_\mathrm{tn}`$ is the plant's rated net electric output (`Parameters.fuel_usd_per_mwh_th`).

**Clean-electricity production tax credit (Section 45Y).** The nuclear cases earn a flat per-MWh credit on net generation, paid for a statutory period after commissioning. It is levelized over the cost-comparison window by the ratio of annuity factors:

```math
c_{45\mathrm{Y}} = \bar{c}\,\frac{A(i,n_\mathrm{credit})}{A(i,n_\mathrm{window})}, \qquad A(i,n) = \frac{1-(1+i)^{-n}}{i} \qquad (12)
```

$`n_\mathrm{credit}`$ and $`n_\mathrm{window}`$ are `finance.ptc_credit_years` and `finance.ptc_window_years`. The annual credit is $`\Phi_\mathrm{ptc} = c_{45\mathrm{Y}} \sum_t P_\mathrm{tn}(t)\,\Delta t`$. Because the credit is generation-indexed, it raises the opportunity cost of every megawatt-hour the absorber diverts from the turbine. Cases 0 and 3 carry no credit.

**Case 3 (NGCC).** The same buckets are used, with the reactor terms replaced by gas terms.

- Fuel use and emissions scale with the part-load multiplier $`\phi_\mathrm{ng}(\ell)`$ of the electrical load fraction $`\ell(t) = P_\mathrm{ng}(t)/\overline{P}_\mathrm{ng}`$:

  ```math
  \Phi_\mathrm{fuel}^\mathrm{ng} = \sum_t \pi_\mathrm{ng}(t)\, h_\mathrm{ng}\, \phi_\mathrm{ng}\big(\ell(t)\big)\, P_\mathrm{ng}(t)\,\Delta t \qquad (\mathrm{S13})
  ```

  ```math
  \Phi_\mathrm{CO_2}^\mathrm{ng} = \pi_\mathrm{CO_2}\,\Theta \sum_t \gamma_\mathrm{ng}\, \phi_\mathrm{ng}\big(\ell(t)\big)\, P_\mathrm{ng}(t)\,\Delta t \qquad (\mathrm{S14})
  ```

- The VOM term is $`v_\mathrm{ng} P_\mathrm{ng}(t) + v_\mathrm{v} Q_\mathrm{v}(t)`$.
- Applying the same $`\phi_\mathrm{ng}`$ to fuel and emissions holds the fuel carbon content constant.

## Figure of merit

The cross-case figure of merit is the grid-cost margin (`grid_cost_margin`):

```math
M_c = 1 - \frac{\mathrm{TAC}_c}{\mathrm{TAC}_0}\Big|_{\text{matched scenario}} \qquad (13)
```

- The denominator is the Case 0 TAC at the same year and carbon price (`scenarios.add_margins` matches on series, carbon price and size by default).
- In a cooling-efficiency sweep the article quotes margins against a fixed grid-only baseline (`add_margins(..., reference=...)`).
- A positive margin means the configuration is cheaper than grid procurement.
- When the grid-only cost itself changes between scenarios (for example across market years), margins are not directly comparable, and absolute annualized costs should be compared as well.

## Secondary endpoints (Supplementary Note 7)

All endpoints use the same solved dispatch and accounting basis as the TAC. $`E_\mathrm{IT} = \sum_t P_\mathrm{IT}(t)\,\Delta t`$ is the annual IT energy, and $`E_\mathrm{gen}`$ the annual net on-site generation.

**Net levelized cost of IT supply** (`nlcs`). Because $`E_\mathrm{IT}`$ is identical across cases, its ranking is the TAC ranking:

```math
\mathrm{NLCS}_c = \frac{\mathrm{TAC}_c}{E_\mathrm{IT}} \qquad (\mathrm{S1})
```

**Levelized cost of cooling**, defined for the grid-only Case 0 only, where the cooling cost separates without an allocation rule (`lcoc_grid_only`):

```math
\mathrm{LCOC}_0 = \frac{\mathrm{CRF}_\mathrm{v}\,\mathrm{CAPEX}_\mathrm{v} S_\mathrm{v} + \mathrm{FOM}_\mathrm{v} S_\mathrm{v} + \sum_t \big[v_\mathrm{v}\, Q_\mathrm{v}(t) + \pi_\mathrm{g}(t)\, P_\mathrm{v}(t)\big]\Delta t}{\sum_t Q_\mathrm{v}(t)\,\Delta t} \qquad (\mathrm{S2})
```

**Energy payback time** (`epbt`), with $`e_k`$ the embodied-energy intensity per unit of installed capacity. It is undefined for Case 0:

```math
\mathrm{EPBT} = \frac{e_k\, S_k}{E_\mathrm{gen}} \qquad (\mathrm{S3})
```

**Water footprint, three tiers** (`water_footprint`), per unit of IT energy:

```math
W_\mathrm{dir} = \frac{w_\mathrm{v} \sum_t Q_\mathrm{v}(t)\Delta t + w_\mathrm{a} \sum_t Q_\mathrm{a}(t)\Delta t}{E_\mathrm{IT}} \qquad (\mathrm{S4})
```

```math
W_\mathrm{ind} = \frac{w_\mathrm{rx} \sum_t P_\mathrm{tn}(t)\Delta t + w_\mathrm{ng} \sum_t P_\mathrm{ng}(t)\Delta t + w_\mathrm{g} \sum_t P_\mathrm{g}^{+}(t)\Delta t}{E_\mathrm{IT}} \qquad (\mathrm{S5})
```

```math
W_\mathrm{tot} = W_\mathrm{dir} + W_\mathrm{ind}, \qquad W_\mathrm{sc} = f_\mathrm{sc}\,\frac{W_\mathrm{tot}}{1000} \qquad (\mathrm{S6})
```

- All factors are water consumption.
- Generation water is charged on the full net generation, including exports (site attribution).
- An absorber rejects its cooling duty plus its driving heat, so $`w_\mathrm{a} = w_\mathrm{v}\,(1 + 1/\mathrm{COP}_\mathrm{a})`$ at an annual-mean COP (`absorber_water_factor`).
- The scarcity tier converts litres to cubic metres and weights by a basin characterization factor $`f_\mathrm{sc}`$ (world average 1).

## Closed-form Cases 0 and 3

Neither case has a dispatch choice (`closed_form.py`).

- **Case 0:** $`Q_\mathrm{v}(t) = Q_\mathrm{cool}^\mathrm{dem}(t)`$, $`P_\mathrm{v}(t) = f_\mathrm{v}(Q_\mathrm{v}(t))/r(t)`$ and $`P_\mathrm{g}^{+}(t) = P_\mathrm{IT}(t) + P_\mathrm{v}(t)`$.
- **Case 3:** the same chiller dispatch, with $`P_\mathrm{ng}(t) = P_\mathrm{IT}(t) + P_\mathrm{v}(t)`$.

The TAC uses the buckets above, so all four cases are on one accounting basis. Solving Case 0 as a MILP (`run_case(0, ..., optimize_case0=True)`) gives the same result, which the tests check.

## Relations used by the boundary tools

These follow from the formulation and are stated in the article's Supplementary Notes 3 and 5.

- **Dispatch-neutral capital.** The capital of an asset with a fixed size enters Eq. (9) only as $`\mathrm{CRF}_k\,\mathrm{CAPEX}_k S_k`$ and appears in no constraint. The optimal dispatch therefore does not depend on it, and $`\mathrm{TAC} = K(\text{capital}) + D(\text{dispatch})`$.
  - A TAC gap $`\Delta\mathrm{TAC}`$ closes at $`\mathrm{CAPEX}_k^{*} = \mathrm{CAPEX}_k - \Delta\mathrm{TAC}/(\mathrm{CRF}_k S_k)`$ without further solves (`breakeven_capex`).
  - Applied to the reactor against Case 0: `reactor_parity_capex`.
  - Applied to the absorber, Case 2 against Case 1 (Case 1 installs no absorber): `absorber_breakeven_capex`.
  - A negative value means that no non-negative capital cost closes the gap.
- **Carbon price.** For fixed dispatch, TAC is linear in $`\pi_\mathrm{CO_2}`$ with slope equal to the net footprint. TAC is therefore the minimum of linear functions: it is concave in the carbon price, and its slope at any price is the footprint of the optimal dispatch at that price.
  - Crossover prices are interpolated between solved prices (`crossover_price`).
  - A crossing beyond the highest solved price can be extrapolated from the last two solved points; it is flagged as extrapolated.

## Conventions of this implementation

1. **Time step and annualization.** $`\Delta t = 1`$ h. The article solves full years ($`\lvert\mathcal{T}\rvert = 8760`$). For shorter horizons, every hourly sum in the objective and the reports is multiplied by $`w = 8760/\lvert\mathcal{T}\rvert`$, so that costs stay annual (`TimeSeries.weight`). For a full year, $`w = 1`$.
2. **Outage window.** It is given by its first hour and length, relative to the first hour of the series (`Outage`). `Outage.from_capacity_factor(CF, start, horizon)` uses $`\mathrm{round}((1-\mathrm{CF})\cdot\mathrm{horizon})`$, which is the article's rule for a horizon of 8,760 h. A window that runs past the end of the horizon is cut there.
3. **Turbine in the outage.** Eq. (2) applies in online hours. In outage hours $`P_\mathrm{tg} = 0`$, as stated in the article. $`P_\mathrm{tg}`$ and $`P_\mathrm{tn}`$ are non-negative. Parameters are refused if Eq. (2) gives a negative gross output at the minimum load without extraction ($`a_\mathrm{w}\underline{P}_\mathrm{rx} < b_\mathrm{w}`$).
4. **Extraction when the absorber is offline.** $`Q_\mathrm{ext}(t) \le (S_\mathrm{a}/\mathrm{COP}_\mathrm{a}^{\,0})\,a(t)`$. When $`a(t) = 0`$, Eq. (6) gives $`Q_\mathrm{a} = 0`$, and the bound also keeps the turbine from diverting steam that produces no cooling.
5. **Ramp limit.** It applies to consecutive hours that are both online. There is no wrap-around from the last hour to the first.
6. **Battery.** $`E_\mathrm{B}(0)`$ is the state before the first hour. The end-state condition holds for any horizon length. The salvage credit uses the sinking-fund convention: an asset with overnight cost $`P`$ and salvage share $`s`$ costs $`P\,[\mathrm{CRF}\,(1-s) + s\,i]`$ per year, that is $`P\,\mathrm{CRF} - sP\,\mathrm{SFF}`$ with $`\mathrm{SFF} = \mathrm{CRF} - i`$.
7. **Chiller map.** The same piecewise-linear $`f_\mathrm{v}`$ is used in the closed-form cases (evaluated directly) and in the MILP (incremental formulation), so Case 0 solved either way gives the same result. The relief multiplier $`r(t)`$ has no lower bound; a non-positive value is an input error.
8. **NGCC part-load curve.** $`\phi_\mathrm{ng}`$ is interpolated linearly between the given points and held at its end values outside them.
9. **Reactor size.** $`S_\mathrm{rx}`$, used for the reactor's CAPEX and FOM and for $`\overline{P}_\mathrm{tn}`$ in $`\pi_\mathrm{f}`$, is the net electric rating. It is taken from `reactor.net_rating_mwe` if given; otherwise it is Eqs. (2)–(3) at $`\overline{P}_\mathrm{rx}`$ with no extraction. A turbine cost, if given, is annualized over the reactor life.
10. **Credit levelization.** If the credit lasts at least as long as the window, it is not reduced. `reactor.ptc_eligible = false` removes the credit.
11. **Objective and fixed costs.** The solver objective holds the dispatch-dependent terms. Capital and fixed O&M are added afterwards, so TAC = objective + fixed costs, which is Eq. (9). After every solve the cost buckets are recomputed from the hourly solution by the same function that evaluates the closed-form cases; the tests check that both agree.
12. **Capacity checks.** Case 0 imports and Case 3 generation must stay within the PCC and the NGCC rating, and the cooling demand within the chiller nameplate; otherwise an error is raised.
13. **Solver tolerance.** The default relative MIP gap is $`10^{-6}`$ (HiGHS `mip_rel_gap`, Gurobi `MIPGap`). It can be changed through the solver options.

## Scope stated in the article

The article states these limits of its setting. They apply equally to this framework.

- Grid prices and carbon intensities are exogenous: the site is a price taker.
- Exports are credited at the hourly average, not the marginal, emission factor. Crossover carbon prices depend on this crediting convention.
- PUE counts cooling power only. Uninterruptible-power-supply, distribution and lighting overheads are excluded.
- The data-center load follows one profile, whose scale but not shape is varied.
- The 45Y credit is levelized over the comparison window by Eq. (12). Results depend on the window chosen.
