import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from smr_dc_cogen import ParameterError, SeriesError, read_inputs, write_inputs
from smr_dc_cogen.cli import main


def test_round_trip(tmp_path, params, series):
    write_inputs(tmp_path, params, series)
    p, ts = read_inputs(tmp_path)
    assert p == params
    assert np.allclose(ts.wet_bulb_c, series.wet_bulb_c) and np.allclose(ts.gas_usd_per_mmbtu, series.gas_usd_per_mmbtu)


def test_read_errors(tmp_path, params, series):
    with pytest.raises(FileNotFoundError):
        read_inputs(tmp_path)
    write_inputs(tmp_path, params, series)
    df = pd.read_csv(tmp_path / "timeseries.csv")
    df.loc[5, "it_load_mw"] = None
    df.to_csv(tmp_path / "timeseries.csv", index=False)
    with pytest.raises(SeriesError, match="timeseries.csv"):
        read_inputs(tmp_path)
    write_inputs(tmp_path, params, series)
    text = (tmp_path / "parameters.json").read_text().replace('"wacc": 0.07', '"wacc": NaN')
    (tmp_path / "parameters.json").write_text(text)
    with pytest.raises(ParameterError, match="NaN"):
        read_inputs(tmp_path)
    (tmp_path / "parameters.json").write_text("{not json")
    with pytest.raises(ParameterError, match="JSON"):
        read_inputs(tmp_path)


def test_cli_synthetic_and_run(tmp_path, capsys):
    d, out = tmp_path / "inputs", tmp_path / "out"
    assert main(["synthetic", str(d), "--hours", "48"]) == 0
    assert main(["run", str(d), "--case", "0", "1", "2", "3", "--carbon-price", "25", "--out-dir", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "grid-cost margin" in printed and "tac" in printed
    summary = json.loads((out / "summary.json").read_text(), parse_constant=lambda c: pytest.fail(c))
    assert set(summary["cases"]) == {"case0", "case1", "case2", "case3"}
    assert summary["cases"]["case0"]["margin"] == 0.0
    assert summary["carbon_price_usd_per_t"] == 25.0
    for case in range(4):
        assert (out / f"hourly_case{case}.csv").is_file()
        json.loads((out / f"summary_case{case}.json").read_text(), parse_constant=lambda c: pytest.fail(c))
    costs = pd.read_csv(out / "costs.csv", index_col=0)
    assert list(costs.index)[-1] == "tac"


def test_cli_battery_and_errors(tmp_path, capsys):
    d = tmp_path / "inputs"
    main(["synthetic", str(d), "--hours", "24"])
    assert main(["run", str(d), "--case", "1", "--battery", "--no-hourly"]) == 0
    assert main(["run", str(tmp_path / "missing")]) == 2
    (d / "parameters.json").write_text(json.dumps({"site": {"chain_efficiency": 2.0}}))
    assert main(["run", str(d)]) == 2
    assert "error" in capsys.readouterr().err


def test_cli_negative_grid_only_cost(tmp_path, capsys):
    # a legal input whose grid-only TAC is negative: costs are reported, the margin is undefined (null)
    d, out = tmp_path / "inputs", tmp_path / "out"
    main(["synthetic", str(d), "--hours", "24"])
    df = pd.read_csv(d / "timeseries.csv")
    df["price_usd_per_mwh"] = -80.0
    df.to_csv(d / "timeseries.csv", index=False)
    assert main(["run", str(d), "--case", "0", "3", "--out-dir", str(out)]) == 0
    assert "undefined" in capsys.readouterr().out
    summary = json.loads((out / "summary.json").read_text())
    assert summary["cases"]["case0"]["tac"] < 0 and summary["cases"]["case3"]["margin"] is None


def test_module_entry_point(tmp_path):
    out = subprocess.run([sys.executable, "-m", "smr_dc_cogen", "synthetic", str(tmp_path / "x"), "--hours", "24"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
