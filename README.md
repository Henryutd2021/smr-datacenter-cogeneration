# SMR–data-center cogeneration framework: reactor power and steam-driven cooling against grid supply

A documented, runnable implementation of the techno-economic framework in:

> Honglin Li, Buxin She and Jie Zhang. **Techno-economic boundary analysis of small modular reactor cogeneration for hyperscale data center IT and cooling loads.** *Energy Conversion and Management* (2026, accepted). Preprint: [arXiv:2608.10999](https://arxiv.org/abs/2608.10999).

A small modular reactor colocated with a data center can sell low-carbon electricity and can also cool the data center: a mid-pressure steam extraction drives an absorption chiller.

The framework co-optimizes, hour by hour:

- reactor output and turbine generation;
- the steam extraction and the absorption chiller;
- the electric (vapor-compression) chiller;
- battery storage;
- grid purchases and sales.

It compares this cogeneration plant, and the same plant without heat recovery, with grid supply and with an on-site gas plant, all on one total-annualized-cost basis.

## Research question

Under which reactor-capital, market and policy conditions is a colocated small modular reactor that also cools the data center through steam extraction cheaper than buying the data center's electricity from the grid? And how far does each condition move that boundary?

## System and scope

| Aspect | In the article | In this framework |
|---|---|---|
| Configurations | four: grid-only (Case 0); reactor without heat recovery (Case 1); reactor with steam extraction to an absorption chiller (Case 2); on-site natural gas combined cycle, islanded (Case 3) | same four cases |
| Site and market | a hyperscale data center in Houston, Texas; ERCOT day-ahead prices | any site with hourly price, grid carbon-intensity and wet-bulb series |
| Reactor | GE-Hitachi BWRX-300 boiling-water small modular reactor | any reactor given by thermal rating, minimum load, ramp limit and refueling outage |
| Turbine | cascaded high/low-pressure turbine; Willans line with an extraction penalty | same; fitting and heat-balance helpers included |
| Cooling | double-effect LiBr–H₂O absorber sharing dispatch with a water-cooled centrifugal chiller; wet-bulb-dependent COPs; crystallization gate | same |
| Time | hourly, one year (8,760 h) | hourly, one year or any shorter block (costs annualized) |
| Integer structure | special-ordered-set (SOS2) part-load map of the electric chiller; hourly import–export interlock | same (the SOS2 map is written with the equivalent incremental formulation) |
| Objective | total annualized cost: capital, fixed and variable O&M, fuel, grid exchange, carbon, Section 45Y credit | same |
| Policy | Section 45Y clean-electricity credit levelized over the comparison window; carbon price with hourly export crediting | same |
| Study design | a baseline and one-at-a-time sweeps (market year, cooling efficiency, battery, reactor capital, reactor × absorber capital, carbon price, financing, data-center size, capital × market year) | scenario tools for each sweep, matched grid-cost margins, closed-form break-even capital and crossover carbon prices |
| Solver | Pyomo with Gurobi | linopy; HiGHS by default, Gurobi optional |

The complete mathematics is in [docs/formulation.md](docs/formulation.md). Equation numbers in this repository refer to the arXiv preprint (v1): (1)–(13) in the main text and (S1)–(S14) in its Supplementary Information.

## What is in this repository

| Path | Content |
|---|---|
| [`docs/formulation.md`](docs/formulation.md) | configurations, sets, parameters, variables; Eqs. (1)–(13) and (S1)–(S14); the SOS2 chiller map; relations behind the boundary tools; conventions of this implementation; scope stated in the article |
| [`docs/data_pipeline.md`](docs/data_pipeline.md) | how each input is built from public sources (ERCOT prices, EIA-930 generation mix, Open-Meteo weather, EIA Henry Hub gas prices, NLR ATB costs, Section 45Y), preparation rules, input file schemas, terms of use |
| [`docs/scenarios.md`](docs/scenarios.md) | the article's study design and how to express each sweep; matched margins; interpretation |
| [`docs/architecture.md`](docs/architecture.md) | modules, data flow, model structure, solvers, problem size, output tables, tests |
| [`src/smr_dc_cogen/`](src/smr_dc_cogen/) | the implementation: parameters, preparation helpers, technical relations, closed-form Cases 0 and 3, the MILP of Cases 1 and 2 (linopy), accounting, endpoints and boundary tools, scenario runs, command line |
| [`examples/`](examples/) | `minimal.py` (four cases on two synthetic weeks), `cooling_dispatch.py` (absorber dispatch and value), `boundary_sweeps.py` (sweeps, parity capital, crossover carbon price), `prepare_inputs.py` (raw records to an input directory) |
| [`tests/`](tests/) | 83 tests: hand-computed accounting, balance equations recomputed from every solution, Case 0 solved two ways, dispatch logic, boundary tools, input validation, command line |

## Quick start

```bash
git clone https://github.com/Henryutd2021/smr-datacenter-cogeneration.git
cd smr-datacenter-cogeneration
python3 -m venv .venv && source .venv/bin/activate    # needs Python 3.10+ (check: python3 --version); if older, use e.g. python3.12
pip install -e ".[test]"
python examples/minimal.py                              # four cases on two synthetic weeks, HiGHS, a few seconds
pytest
```

With conda instead: `conda env create -f environment.yml && conda activate smr-dc-cogen && pip install -e .`

The command line works on input directories:

```bash
smr-dc-cogen synthetic my_inputs/                          # synthetic series + illustrative parameters
smr-dc-cogen run my_inputs/ --out-dir outputs/base         # Cases 0-3: cost buckets, grid-cost margins, hourly tables
smr-dc-cogen run my_inputs/ --case 1 2 --battery --carbon-price 75 --out-dir outputs/cp75
```

In Python:

```python
from smr_dc_cogen import read_inputs, run_case, grid_cost_margin, reactor_parity_capex, cost_table

params, ts = read_inputs("my_inputs/")                     # your own data, see docs/data_pipeline.md
grid = run_case(0, params, ts)                             # closed form
cogen = run_case(2, params, ts)                            # MILP; or run_case(2, params, ts, solver="gurobi")
print(cost_table([grid, cogen]))                           # million $/yr by bucket
print(grid_cost_margin(cogen.tac, grid.tac))               # Eq. (13)
print(reactor_parity_capex(cogen, grid.tac, params))       # reactor capital at grid parity, $/kW_e
```

**About the example data:** `smr_dc_cogen.synthetic` generates invented series (two summer weeks by default) and round illustrative parameters. They show how the model behaves and how to read its output. Their numbers say nothing about any real plant, market or site.

## Using your own data

1. **Hourly series.** Build them as described in [docs/data_pipeline.md](docs/data_pipeline.md), with the helpers in `smr_dc_cogen.timeseries` (see `examples/prepare_inputs.py`). The format is validated when read.
2. **Parameters.** Put your technical and cost parameters in `parameters.json`, in the units named by each field. The article lists its baseline values and sources in its Table 2 and Supplementary Tables S7–S8.
3. **Runs.** Run the baseline and your sweeps ([docs/scenarios.md](docs/scenarios.md)). Check the balance residuals (`balance_residuals`) and record the solver and gap with the results.

A full-year Case 1 or Case 2 instance is a mixed-integer program with roughly a hundred thousand binaries. HiGHS solves the synthetic full year in seconds, but harder price years can take much longer. Gurobi is supported, and shorter blocks with annualized costs are a fallback ([docs/architecture.md](docs/architecture.md#problem-size)).

## What this repository does not contain

This repository presents the modelling framework. It does not contain the code, the input data or the results used for the article. For the article's results, see the article.

## Citation

If you use this framework, please cite the article above. Citation metadata for both the article and this software are in [`CITATION.cff`](CITATION.cff).

## Licence

- Code: [MIT](LICENSE).
- Documentation (`README.md`, `docs/`): [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
