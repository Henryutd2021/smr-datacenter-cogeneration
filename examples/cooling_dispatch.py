"""Absorption cooling: how Case 2 splits the cooling load between steam and electricity, and what the absorber
adds relative to the reactor-only Case 1.

Shows: the hourly split against the electricity price, the bucket-by-bucket difference Case 2 - Case 1, the
extraction diagnostics, the absorber capital at which Case 2 and Case 1 cost the same (closed form, because the
absorber's capital is dispatch-neutral), and a check of that value by re-solving.

Synthetic inputs and illustrative parameters only.

    python examples/cooling_dispatch.py
"""

import pandas as pd

from smr_dc_cogen import (absorber_breakeven_capex, bucket_difference, extraction_diagnostics,
                          illustrative_parameters, run_case, synthetic_timeseries, with_changes)

ts = synthetic_timeseries(hours=336)
params = illustrative_parameters(hours=336)

case1 = run_case(1, params, ts)
case2 = run_case(2, params, ts)
h = case2.hourly[~case2.hourly["outage"]].copy()

# share of the cooling load carried by the absorber, by price band (hours with the absorber available)
h = h[h["absorber_available"] > 0]
h["share"] = h["absorber_mwc"] / h["cooling_demand_mwc"]
bands = pd.qcut(h["price_usd_per_mwh"], 5)
print("Absorber share of cooling by electricity-price quintile (available, online hours)")
print(h.groupby(bands, observed=True)["share"].agg(["mean", "count"]).round(3).to_string())

print("\nCase 2 - Case 1 by cost bucket, million $/yr")
for k, v in bucket_difference(case2, case1).items():
    print(f"  {k:7s} {v / 1e6:+9.3f}")

d = extraction_diagnostics(case2, params)
print("\nSteam extraction (annual)")
print(f"  net generation forgone:            {d['forgone_net_generation_mwh']:,.0f} MWh")
print(f"  ... valued at the hourly price:    {d['forgone_generation_value_usd'] / 1e6:,.3f} million $")
print(f"  ... 45Y credit attached to it:     {d['forgone_ptc_usd'] / 1e6:,.3f} million $")

x = absorber_breakeven_capex(case2, case1, params)
print(f"\nAbsorber capital at which Case 2 = Case 1: {x:,.1f} $/kW_c "
      f"(illustrative value in the parameters: {params.absorber.capex_usd_per_kwc:,.0f} $/kW_c)")
if x >= 0:
    check = run_case(2, with_changes(params, {"absorber.capex_usd_per_kwc": x}), ts)
    print(f"check by re-solving: TAC Case 2 = {check.tac / 1e6:.4f}, Case 1 = {case1.tac / 1e6:.4f} million $/yr")
else:
    print("no non-negative absorber capital closes the gap in this example")
