"""Solve a :class:`~smr_dc_cogen.model.CaseModel` with HiGHS (default) or Gurobi through linopy, and run cases."""

from __future__ import annotations

from importlib import metadata
from typing import Mapping, Optional

from .cases import case_spec
from .closed_form import evaluate_case0, evaluate_case3
from .model import CaseModel, build_model, solution_frame
from .parameters import Parameters, ParameterError
from .results import CaseResult, account
from .timeseries import TimeSeries

#: HiGHS: quiet, with a tight relative MIP gap (any HiGHS option can be passed instead)
HIGHS_DEFAULTS = {"output_flag": False, "mip_rel_gap": 1e-6}
#: Gurobi: quiet, with the same relative MIP gap
GUROBI_DEFAULTS = {"OutputFlag": 0, "MIPGap": 1e-6}


class SolveError(RuntimeError):
    """The solver did not return an optimal solution."""


def _solver_version(solver: str) -> Optional[str]:
    package = {"highs": "highspy", "gurobi": "gurobipy"}.get(solver)
    try:
        return metadata.version(package) if package else None
    except metadata.PackageNotFoundError:
        return None


def _realized_gap(model, solver: str) -> Optional[float]:
    """Relative MIP gap reported by the solver after an in-memory solve (None if not available)."""
    native = getattr(model, "solver_model", None)
    try:
        if solver == "highs":
            return abs(float(native.getInfo().mip_gap))
        if solver == "gurobi":
            return abs(float(native.MIPGap))
    except Exception:  # attribute missing in this solver or linopy version
        return None
    return None


def solve(cm: CaseModel, solver: str = "highs", options: Optional[Mapping[str, object]] = None,
          log_file: Optional[str] = None, require_optimal: bool = True) -> CaseModel:
    """Solve in memory. ``options`` are passed to the solver unchanged and override the defaults above,
    e.g. ``{"time_limit": 600, "mip_rel_gap": 1e-4}`` for HiGHS or ``{"Threads": 8}`` for Gurobi."""
    defaults = {"highs": HIGHS_DEFAULTS, "gurobi": GUROBI_DEFAULTS}.get(solver, {})
    opts = {**defaults, **(options or {})}
    kwargs = {"io_api": "direct", "progress": False}
    if log_file:
        kwargs["log_fn"] = log_file
    status, termination = cm.model.solve(solver_name=solver, **kwargs, **opts)
    status, termination = str(status), str(termination)
    cm.solver = {"name": solver, "version": _solver_version(solver), "status": status, "termination": termination,
                 "mip_gap_requested": opts.get("mip_rel_gap", opts.get("MIPGap")),
                 "mip_gap": _realized_gap(cm.model, solver)}
    if termination != "optimal":
        if require_optimal:
            raise SolveError(f"solver {solver} returned status={status}, termination={termination}")
        return cm
    obj = cm.model.objective
    value = obj.value if hasattr(obj, "value") else cm.model.objective_value
    cm.objective = float(value)
    cm.solver["objective_usd_per_yr"] = cm.objective + cm.fixed_cost
    return cm


def result_of(cm: CaseModel, label: str = "") -> CaseResult:
    """Cost accounting of a solved model (same function as for the closed-form cases)."""
    if cm.solver.get("termination") != "optimal":
        raise SolveError("the model has no optimal solution")
    res = account(cm.spec, cm.params, cm.ts, solution_frame(cm), cm.battery, label=label)
    res.solver = dict(cm.solver)
    return res


def run_case(case: int, params: Parameters, ts: TimeSeries, battery: bool = False, solver: str = "highs",
             options: Optional[Mapping[str, object]] = None, optimize_case0: bool = False,
             label: str = "") -> CaseResult:
    """Evaluate one case: closed form for Cases 0 and 3, MILP for Cases 1 and 2.

    ``optimize_case0=True`` solves Case 0 through the MILP instead (it gives the same result; used as a check).
    """
    spec = case_spec(case)
    if case == 3:
        if battery:
            raise ParameterError("the battery is optimized only in Cases 1 and 2")
        return evaluate_case3(params, ts, label=label)
    if case == 0 and not optimize_case0:
        if battery:
            raise ParameterError("the battery is optimized only in Cases 1 and 2")
        return evaluate_case0(params, ts, label=label)
    cm = solve(build_model(spec.number, params, ts, battery=battery), solver=solver, options=options)
    return result_of(cm, label=label)


__all__ = ["HIGHS_DEFAULTS", "GUROBI_DEFAULTS", "SolveError", "solve", "result_of", "run_case"]
