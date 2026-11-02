"""smr_dc_cogen: hourly techno-economic framework for small-modular-reactor cogeneration serving a data center's
IT and cooling loads, compared with grid supply (framework of Li, She & Zhang, arXiv:2608.10999).

See docs/formulation.md for the equations (numbered as in the article), docs/data_pipeline.md for inputs,
docs/scenarios.md for the study design and docs/architecture.md for the code layout.
"""

from .analysis import (EndpointFactors, absorber_breakeven_capex, absorber_water_factor, breakeven_capex,
                       crossover_price, epbt, extraction_diagnostics, grid_cost_margin, lcoc_grid_only, nlcs,
                       reactor_parity_capex, realized_pue, water_footprint)
from .cases import CASES, CaseSpec
from .closed_form import evaluate_case0, evaluate_case3
from .io import read_inputs, write_inputs
from .model import CaseModel, build_model
from .parameters import (Absorber, Battery, Chiller, Finance, GasPlant, Grid, Outage, ParameterError, Parameters,
                         Reactor, Site, Turbine, annualized_capital, annuity_factor, cop_for_full_load_pue, crf,
                         extraction_penalty_from_heat_balance, from_dict, full_load_pue, levelized_ptc,
                         relief_matching_absorber, to_dict, with_changes)
from .results import CaseResult, balance_residuals, bucket_difference, cost_table
from .scenarios import Scenario, add_margins, run_scenarios, scale_data_center
from .solve import SolveError, result_of, run_case, solve
from .synthetic import illustrative_endpoint_factors, illustrative_parameters, synthetic_timeseries
from .timeseries import SeriesError, TimeSeries

__version__ = "1.0.1"

__all__ = [
    "EndpointFactors", "absorber_breakeven_capex", "absorber_water_factor", "breakeven_capex", "crossover_price",
    "epbt", "extraction_diagnostics", "grid_cost_margin", "lcoc_grid_only", "nlcs", "reactor_parity_capex",
    "realized_pue", "water_footprint", "CASES", "CaseSpec", "evaluate_case0", "evaluate_case3", "read_inputs",
    "write_inputs", "CaseModel", "build_model", "Absorber", "Battery", "Chiller", "Finance", "GasPlant", "Grid",
    "Outage", "ParameterError", "Parameters", "Reactor", "Site", "Turbine", "annualized_capital", "annuity_factor",
    "cop_for_full_load_pue", "crf", "extraction_penalty_from_heat_balance", "from_dict", "full_load_pue",
    "levelized_ptc", "relief_matching_absorber", "to_dict", "with_changes", "CaseResult", "balance_residuals",
    "bucket_difference", "cost_table",
    "Scenario", "add_margins", "run_scenarios", "scale_data_center", "SolveError", "result_of", "run_case", "solve",
    "illustrative_endpoint_factors", "illustrative_parameters", "synthetic_timeseries", "SeriesError", "TimeSeries",
    "__version__",
]
