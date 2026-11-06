"""Exercise the public CLI and fixture files, not a parallel calculation path."""

import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from matplotlib import pyplot as plt

from rf_measurement.analysis import (
    compression,
    intercept,
    load_network,
    network_metrics,
    noise_figure,
)
from rf_measurement.report import create_report
from rf_measurement.synthetic import generate


def test_generated_model_known_answers(tmp_path):
    generate(tmp_path)
    expected = json.loads((tmp_path / "expected.json").read_text())
    metrics = network_metrics(load_network(tmp_path / "amplifier.s2p"))
    np.testing.assert_allclose(metrics.gain_db, 20, atol=1e-9)
    np.testing.assert_allclose(metrics.input_return_loss_db, 20, atol=1e-9)
    np.testing.assert_allclose(metrics.output_return_loss_db, 26.020599913, atol=1e-8)
    np.testing.assert_allclose(metrics.input_vswr, 1.222222222, atol=1e-8)
    np.testing.assert_allclose(metrics.output_vswr, 1.105263158, atol=1e-8)
    p1 = compression(tmp_path / "power.csv")
    assert p1.gain_db == pytest.approx(20, abs=0.001)
    assert p1.input_p1db_dbm == pytest.approx(-5, abs=0.01)
    assert p1.output_p1db_dbm == pytest.approx(14, abs=0.01)
    ip3 = intercept(tmp_path / "two_tone.csv")
    # Oracle uses only the specified cubic coefficient, never an extracted result.
    oracle = -5 - 10 * np.log10(1 - 10 ** (-1 / 20))
    assert expected["iip3_dbm"] == pytest.approx(oracle)
    assert ip3.iip3_dbm == pytest.approx(oracle, abs=0.03)
    assert ip3.oip3_dbm == pytest.approx(oracle + 20, abs=0.03)
    nf = noise_figure(tmp_path / "noise.csv")
    np.testing.assert_allclose(nf.noise_figure_db, 3, atol=1e-8)
    np.testing.assert_allclose(nf.noise_temperature_k, 290 * (10**0.3 - 1), atol=1e-7)


def test_report_tables_plots_and_no_open_figures(tmp_path):
    inputs, output = tmp_path / "inputs", tmp_path / "report"
    generate(inputs)
    summary = create_report(
        inputs / "amplifier.s2p",
        inputs / "power.csv",
        inputs / "two_tone.csv",
        inputs / "noise.csv",
        output,
    )
    assert summary["compression"]["input_p1db_dbm"] == pytest.approx(-5, abs=0.01)
    for stem in ("smith", "s_parameters", "compression", "intercept", "noise_figure"):
        assert (output / (stem + ".png")).read_bytes().startswith(b"\x89PNG")
    table = pd.read_csv(output / "summary.csv")
    assert {"input_p1db_dbm", "iip3_dbm", "noise_figure_min_db"} <= set(table.metric)
    assert len(pd.read_csv(output / "s_parameters.csv")) == 51
    assert len(pd.read_csv(output / "noise_figure.csv")) == 51
    assert json.loads((output / "summary.json").read_text())["inputs"]["touchstone"].endswith(
        ".s2p"
    )
    assert "Input P1dB" in (output / "report.md").read_text()
    assert not plt.get_fignums()


def test_cli_demo_real_inputs_and_errors(tmp_path):
    demo = subprocess.run(
        [sys.executable, "-m", "rf_measurement", "demo", "--output", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert demo.returncode == 0, demo.stderr
    assert (tmp_path / "report" / "summary.csv").exists()
    inputs = tmp_path / "inputs"
    output = tmp_path / "rerun"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "rf_measurement",
            "report",
            "--touchstone",
            str(inputs / "amplifier.s2p"),
            "--power",
            str(inputs / "power.csv"),
            "--two-tone",
            str(inputs / "two_tone.csv"),
            "--noise",
            str(inputs / "noise.csv"),
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads((output / "summary.json").read_text())["intercept"]["points"] == 11
    bad = subprocess.run(
        [
            sys.executable,
            "-m",
            "rf_measurement",
            "report",
            "--touchstone",
            "missing.s2p",
            "--power",
            "missing.csv",
            "--two-tone",
            "missing.csv",
            "--noise",
            "missing.csv",
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad.returncode != 0
    assert "error:" in bad.stderr and "Traceback" not in bad.stderr


def test_generation_refuses_to_overwrite_measurements(tmp_path):
    tmp_path.joinpath("power.csv").write_text("my original bench measurements")
    with pytest.raises(FileExistsError):
        generate(tmp_path)
    assert tmp_path.joinpath("power.csv").read_text() == "my original bench measurements"


def test_report_preserves_sources_on_destination_collision(tmp_path):
    generate(tmp_path)
    source = tmp_path / "summary.csv"
    source.write_text((tmp_path / "power.csv").read_text())
    original = source.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        create_report(
            tmp_path / "amplifier.s2p",
            source,
            tmp_path / "two_tone.csv",
            tmp_path / "noise.csv",
            tmp_path,
        )
    assert source.read_bytes() == original


def test_demo_report_identifies_synthetic_data(tmp_path):
    run = subprocess.run(
        [sys.executable, "-m", "rf_measurement", "demo", "--output", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    summary = json.loads((tmp_path / "report" / "summary.json").read_text())
    assert "synthetic" in summary["label"].lower()
    assert "synthetic" in (tmp_path / "report" / "report.md").read_text().lower()


def test_report_includes_reflection_summary(tmp_path):
    inputs = tmp_path / "inputs"
    generate(inputs)
    summary = create_report(
        inputs / "amplifier.s2p",
        inputs / "power.csv",
        inputs / "two_tone.csv",
        inputs / "noise.csv",
        tmp_path / "report",
    )
    assert summary["network"]["input_return_loss_min_db"] == pytest.approx(20)
    assert summary["network"]["output_return_loss_min_db"] == pytest.approx(26.020599913)
    assert summary["network"]["input_vswr_max"] == pytest.approx(1.222222222)
    assert summary["network"]["output_vswr_max"] == pytest.approx(1.105263158)


def test_plot_save_failure_closes_owned_figure(tmp_path, monkeypatch):
    from matplotlib.figure import Figure

    inputs = tmp_path / "inputs"
    generate(inputs)

    def disk_failure(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(Figure, "savefig", disk_failure)
    with pytest.raises(OSError, match="write failure"):
        create_report(
            inputs / "amplifier.s2p",
            inputs / "power.csv",
            inputs / "two_tone.csv",
            inputs / "noise.csv",
            tmp_path / "report",
        )
    assert not plt.get_fignums()


def test_report_flags_partly_undefined_vswr(tmp_path):
    inputs = tmp_path / "inputs"
    generate(inputs)
    touchstone = inputs / "mixed.s2p"
    touchstone.write_text(
        "# GHz S RI R 50\n1 0.1 0 10 0 0.001 0 0.05 0\n2 1.1 0 10 0 0.001 0 0.05 0\n"
    )
    output = tmp_path / "report"
    result = create_report(
        touchstone, inputs / "power.csv", inputs / "two_tone.csv", inputs / "noise.csv", output
    )
    assert result["network"]["input_vswr_max"] is None
    assert result["network"]["input_vswr_undefined_points"] == 1
    assert result["network"]["output_vswr_max"] == pytest.approx(1.105263158)
    assert "undefined" in (output / "report.md").read_text()


def test_failed_regeneration_preserves_previous_report(tmp_path, monkeypatch):
    from matplotlib.figure import Figure

    inputs, output = tmp_path / "inputs", tmp_path / "report"
    generate(inputs)
    args = (
        inputs / "amplifier.s2p",
        inputs / "power.csv",
        inputs / "two_tone.csv",
        inputs / "noise.csv",
        output,
    )
    create_report(*args)
    originals = {p.name: p.read_bytes() for p in output.iterdir()}
    power = pd.read_csv(inputs / "power.csv")
    power.pout_dbm += 1
    power.to_csv(inputs / "power.csv", index=False)

    def disk_failure(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(Figure, "savefig", disk_failure)
    with pytest.raises(OSError):
        create_report(*args)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == originals
    assert not list(tmp_path.glob(".rf-report-*"))
