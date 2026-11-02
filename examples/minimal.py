"""Minimal example: the four configurations on two synthetic summer weeks (HiGHS, a few seconds).

Synthetic inputs and round illustrative parameters (smr_dc_cogen.synthetic); the numbers say nothing about any
real plant, market or site.

    python examples/minimal.py
"""

from smr_dc_cogen import (balance_residuals, cost_table, grid_cost_margin, illustrative_parameters, run_case,
                          synthetic_timeseries)

ts = synthetic_timeseries(hours=336)
params = illustrative_parameters(hours=336)

results = [run_case(case, params, ts) for case in (0, 1, 2, 3)]
grid_only = results[0]

print(f"{ts.hours} hours; every hourly sum is multiplied by {ts.weight:.3f} to give annual values")
print("\nCost buckets of Eq. (9), million $/yr (negative: revenue or credit)")
print(cost_table(results).round(3).to_string())

print("\nGrid-cost margin M = 1 - TAC_c / TAC_0 (Eq. 13); positive means cheaper than grid supply")
for r in results:
    print(f"  Case {r.case} ({r.name}): {grid_cost_margin(r.tac, grid_only.tac):+.3f}")

r2 = results[2]
print("\nCase 2 annual quantities")
for key in ("net_generation_mwh", "grid_export_mwh", "grid_import_mwh", "absorber_share_of_cooling",
            "extraction_mwh_th", "footprint_t_co2"):
    print(f"  {key}: {r2.totals[key]:,.3f}")

worst = max(max(balance_residuals(r, params).values()) for r in results)
print(f"\nlargest residual of the balance equations over all cases: {worst:.2e}")
