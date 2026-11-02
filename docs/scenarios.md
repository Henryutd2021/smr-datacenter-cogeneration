# Study design and scenario sets

This document describes the article's study design and how to express each part with this framework. The article's run ledger (its Table 3) lists the levels of every sweep; they are not repeated here. [`examples/boundary_sweeps.py`](../examples/boundary_sweeps.py) runs a reduced version on synthetic data with illustrative levels.

## Building blocks

- **Case:** one of the four configurations of [formulation.md](formulation.md#configurations) (`run_case(case, params, ts)`). Cases 1 and 2 can include the battery (`battery=True`).
- **Series:** one aligned hourly input set (`TimeSeries`), for example one market year with its own price, wet-bulb and carbon-intensity records and the IT trace aligned to its weekdays.
- **Parameter changes:** `{"block.field": value}` applied together and validated (`with_changes`).
- **Scenario:** a named combination of the three (`Scenario(name, case, series, changes, battery, size)`). `run_scenarios` evaluates a list of scenarios and returns one table row per scenario with the seven cost buckets, the TAC and annual energy totals.
- **Matched margin:** `add_margins(table)` adds the grid-cost margin of Eq. (13) against the Case 0 row with the same series, carbon price and size. A fixed baseline can be given instead with `reference=`.

## Baseline (article group G0)

All four cases are run on the baseline market year. The baseline is set by:

- a full-load PUE label, that is, a design-point VCC COP;
- the mid-range reactor capital;
- the baseline WACC;
- a zero carbon price.

The margins of Cases 1–3 are taken against Case 0.

```python
from smr_dc_cogen import Scenario
base = [Scenario(f"G0_case{c}", c, "year_base", group="G0") for c in (0, 1, 2, 3)]
```

## One-at-a-time sweeps

| Group | What varies | Cases | How to express it |
|---|---|---|---|
| G1 cooling efficiency | VCC design-point COP; the run is labeled by the equivalent full-load PUE $`1 + 1/(\eta_\mathrm{ch}\mathrm{COP}_\mathrm{v})`$. The heat duty of Eq. (1) is unchanged. | 1, 2 | `changes={"chiller.cop_design": cop_for_full_load_pue(pue, eta_ch)}`. Margins against a fixed grid-only baseline: `add_margins(table, match_on=("series",), reference={...})` |
| G2 market year | price, wet-bulb and carbon-intensity series of each year; the IT trace re-aligned to each year's weekdays | 0–3 | one `TimeSeries` per year, `series="year_..."` |
| G3 battery | battery on or off | 1, 2 optimized; 0 and 3 kept as control rows | `battery=True` for Cases 1 and 2 |
| G4 reactor capital | reactor overnight capital at several anchors (first-of-a-kind, mid-range, nth-of-a-kind) | 1, 2 | `changes={"reactor.capex_usd_per_kwe": x}` |
| G5 reactor × absorber capital | two-dimensional grid of reactor and absorber capital, at the baseline year | 2 | nested loops over both fields |
| G6 carbon price | carbon price levels | 0–3 | `changes={"finance.carbon_price_usd_per_t": p}`; margins are matched on carbon price |
| G7 financing | WACC levels; the 45Y levelization (Eq. 12) and every CRF change with it | 2 | `changes={"finance.wacc": i}` |
| G8 data-center size | IT trace and data-center-side capacities scaled; the reactor stays fixed | 0–3 | `Scenario(..., size=m)`, which applies `scale_data_center` |
| G9 capital × market year | selected reactor-capital anchors in each market year | 1, 2 | `series=...` combined with `changes={"reactor.capex_usd_per_kwe": x}` |

### Notes on the sweeps

- **Ledger rows and distinct instances.** The article's ledger keeps rows that repeat the baseline inside a sweep, so that each group is complete. For example, the baseline-year cells of G2, the zero-carbon-price cells of G6 and the unit-size cells of G8 are such rows. A scenario list can either repeat them or reuse the baseline results.
- **Size rule (G8).** `scale_data_center` scales the IT trace and the chiller, absorber and NGCC capacities by the multiplier. The reactor is fixed. The PCC keeps its baseline value below a multiplier of one and scales upward only above it, so that exports from the fixed reactor are not artificially constrained.
- **Capital sweeps without re-solving (G4, G5, G9).** Reactor and absorber capital are dispatch-neutral ([formulation.md](formulation.md#relations-used-by-the-boundary-tools)): TAC separates as $`K(\text{capital}) + D(\text{dispatch})`$. Any capital level can therefore be evaluated from one solve per case and series: TAC changes by $`\mathrm{CRF}_k S_k\,\Delta\mathrm{CAPEX}_k`$. Thresholds follow in closed form:
  - `reactor_parity_capex`: the reactor capital at which a reactor case reaches the grid-only TAC;
  - `absorber_breakeven_capex`: the absorber capital at which Case 2 costs the same as Case 1.

  Re-solving at the computed value returns the same dispatch and closes the gap; the tests check both.
- **Carbon-price crossover (G6).** The TAC of each case is concave in the carbon price. The crossover with grid supply is interpolated between solved prices (`crossover_price`). A crossing beyond the highest solved price can be extrapolated from the last two solved points and is flagged as extrapolated. Results depend on the export-crediting convention of Eq. (11).

## Interpreting the outputs

- **Margins across scenarios.** The margin of Eq. (13) is relative to the matched grid-only TAC. When that denominator changes, as it does between market years, compare absolute annualized costs as well.
- **Absorber value (Case 2 against Case 1).**
  - `bucket_difference(case2, case1)` splits the TAC difference by bucket.
  - `extraction_diagnostics(case2, params)` reports the net generation that the extraction forgoes, its value at the hourly price, and the 45Y credit attached to it.
  - The absorber's dispatch follows the comparison, hour by hour, between the electricity the VCC would draw and the generation the extraction forgoes, both valued at the hourly price, with the forgone credit added on the steam side.
- **Secondary endpoints.** NLCS, LCOC, EPBT and the water tiers are computed from the same results (`analysis.py`). NLCS ranks the cases as the TAC does, because all cases share the IT-energy denominator; EPBT and the water tiers measure other quantities and can rank them differently.
- **Solver settings.** A full-year Case 1 or 2 is a mixed-integer program with $`K + 1`$ binaries per hour for a chiller map of $`K`$ segments ([architecture.md](architecture.md#problem-size)). Record the solver, its version and the relative MIP gap with every result table: `CaseResult.solver` holds the solver name and version, the requested gap tolerance and the gap the solver reports.
