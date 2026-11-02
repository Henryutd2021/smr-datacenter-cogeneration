"""Boundary tools on synthetic data: one-at-a-time sweeps expressed as scenarios, matched grid-cost margins,
the reactor capital at grid parity (closed form) and the crossover carbon price.

The levels below are illustrative choices for the synthetic example, not the article's study levels
(see docs/scenarios.md for how the article's scenario groups map onto these tools).

    python examples/boundary_sweeps.py
"""

from smr_dc_cogen import (Scenario, add_margins, crossover_price, illustrative_parameters, reactor_parity_capex,
                          run_scenarios, synthetic_timeseries)

params = illustrative_parameters(hours=168)
series = {"week_a": synthetic_timeseries(hours=168, start_day=190, seed=7, label="week_a"),
          "week_b": synthetic_timeseries(hours=168, start_day=40, seed=11, label="week_b")}

scenarios = []
for s in series:                                              # input series (e.g. operating years)
    scenarios += [Scenario(f"{s}_case{c}", c, s, group="series") for c in (0, 1, 2, 3)]
for cp in (40.0, 80.0, 120.0):                                # carbon price
    scenarios += [Scenario(f"cp{cp:g}_case{c}", c, "week_a", changes={"finance.carbon_price_usd_per_t": cp},
                           group="carbon") for c in (0, 1, 2, 3)]
for capex in (3000.0, 4500.0):                                # reactor capital
    scenarios += [Scenario(f"rx{capex:g}_case{c}", c, "week_a", changes={"reactor.capex_usd_per_kwe": capex},
                           group="reactor_capital") for c in (1, 2)]
for wacc in (0.06, 0.09):                                     # financing
    scenarios.append(Scenario(f"wacc{wacc:g}_case2", 2, "week_a", changes={"finance.wacc": wacc}, group="wacc"))
scenarios += [Scenario(f"battery_case{c}", c, "week_a", battery=True, group="battery") for c in (1, 2)]
for m in (0.8, 1.6):                                         # data-center size (reactor fixed)
    scenarios += [Scenario(f"size{m:g}_case{c}", c, "week_a", size=m, group="size") for c in (0, 1, 2)]

table, results = run_scenarios(scenarios, params, series, progress=print)
table = add_margins(table)
cols = ["group", "case", "series", "carbon_price", "size", "changes", "tac", "margin"]
print("\nTAC in million $/yr and grid-cost margin against the matched Case 0")
print(table[cols].assign(tac=table["tac"] / 1e6).round(3).to_string(index=False))

# reactor capital at grid parity: TAC = K(capital) + D(dispatch), so it follows from one solve per case
for c in (1, 2):
    x = reactor_parity_capex(results[f"week_a_case{c}"], results["week_a_case0"].tac, params)
    print(f"Case {c}: reactor capital at grid parity on week_a: {x:,.0f} $/kW_e")

# crossover carbon price against grid supply, from the solved prices only
prices = [0.0, 40.0, 80.0, 120.0]
tac = {c: [results[f"week_a_case{c}"].tac] + [results[f"cp{p:g}_case{c}"].tac for p in prices[1:]] for c in (0, 1, 2, 3)}
for c in (1, 2, 3):
    hit = crossover_price(prices, tac[c], tac[0], extrapolate=True)
    if hit is None:
        print(f"Case {c}: no crossover with grid supply")
    else:
        print(f"Case {c}: crosses grid supply at {hit[0]:,.1f} $/t CO2" + (" (extrapolated)" if hit[1] else ""))
