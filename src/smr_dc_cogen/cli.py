"""Command line: ``smr-dc-cogen synthetic DIR`` and ``smr-dc-cogen run DIR``.

``run`` evaluates the chosen cases on an input directory (docs/data_pipeline.md), prints the cost buckets and the
grid-cost margin against Case 0, and with ``--out-dir`` writes ``costs.csv``, ``summary.json`` and, per case,
``summary_case<N>.json`` and ``hourly_case<N>.csv``. Exit code 2 on invalid input, 3 if a solve fails.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .analysis import grid_cost_margin


def _margin(tac: float, tac_grid_only: float) -> Optional[float]:
    """Eq. (13), or None when the grid-only TAC is not positive (the margin is then undefined)."""
    return grid_cost_margin(tac, tac_grid_only) if tac_grid_only > 0 else None
from .io import read_inputs, write_inputs
from .parameters import ParameterError, with_changes
from .results import cost_table, strict_json
from .solve import SolveError, run_case
from .synthetic import illustrative_parameters, synthetic_timeseries
from .timeseries import SeriesError


def _synthetic(args) -> int:
    ts = synthetic_timeseries(hours=args.hours, start_day=args.start_day, seed=args.seed)
    d = write_inputs(args.directory, illustrative_parameters(hours=args.hours), ts)
    print(f"wrote {d / 'timeseries.csv'} ({ts.hours} hours) and {d / 'parameters.json'} (illustrative values)")
    return 0


def _run(args) -> int:
    try:
        params, ts = read_inputs(args.directory)
        if args.carbon_price is not None:
            params = with_changes(params, {"finance.carbon_price_usd_per_t": args.carbon_price})
        results = []
        for case in args.case:
            res = run_case(case, params, ts, battery=args.battery and case in (1, 2), solver=args.solver,
                           options={"time_limit": args.time_limit} if args.time_limit and args.solver == "highs" else None)
            results.append(res)
    except (ParameterError, SeriesError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    table = cost_table(results)
    base = next((r for r in results if r.case == 0), None)
    print(f"{ts.hours} hours, annualization weight {ts.weight:.4g}; costs in million $/yr")
    print(table.round(3).to_string())
    summary = {"hours": ts.hours, "carbon_price_usd_per_t": params.finance.carbon_price_usd_per_t, "cases": {}}
    for r in results:
        entry = {k: v for k, v in r.costs.items()}
        if base is not None:
            entry["margin"] = _margin(r.tac, base.tac)
        summary["cases"][f"case{r.case}" + ("_battery" if r.battery else "")] = entry
    if base is not None and base.tac > 0:
        print("grid-cost margin M (Eq. 13): " + ", ".join(
            f"case {r.case}{'+battery' if r.battery else ''}: {_margin(r.tac, base.tac):+.3f}" for r in results))
    elif base is not None:
        print("grid-cost margin M (Eq. 13): undefined, the grid-only TAC is not positive")
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        table.to_csv(out / "costs.csv", index_label="bucket")
        (out / "summary.json").write_text(strict_json(summary) + "\n")
        for r in results:
            r.to_files(out, hourly=not args.no_hourly)
        print(f"wrote {out}/")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="smr-dc-cogen", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("synthetic", help="write a synthetic input directory with illustrative parameters")
    s.add_argument("directory")
    s.add_argument("--hours", type=int, default=336)
    s.add_argument("--start-day", type=int, default=190, help="day of year of the first hour (default 190)")
    s.add_argument("--seed", type=int, default=7)
    s.set_defaults(func=_synthetic)
    r = sub.add_parser("run", help="evaluate cases on an input directory")
    r.add_argument("directory")
    r.add_argument("--case", type=int, nargs="+", default=[0, 1, 2, 3], choices=[0, 1, 2, 3])
    r.add_argument("--battery", action="store_true", help="add the battery to Cases 1 and 2")
    r.add_argument("--carbon-price", type=float, default=None, help="override finance.carbon_price_usd_per_t")
    r.add_argument("--solver", default="highs", help="highs (default) or gurobi")
    r.add_argument("--time-limit", type=float, default=None, help="HiGHS time limit per solve, seconds")
    r.add_argument("--out-dir", default=None)
    r.add_argument("--no-hourly", action="store_true", help="do not write the hourly tables")
    r.set_defaults(func=_run)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
