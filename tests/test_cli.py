import json

import numpy as np
import pytest

from rf_measurement import demo, sweep


def test_dsp_demo_exports_reproducible_validated_results(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rf-dsp-demo", "--output", str(tmp_path)])
    demo.main()
    report = json.loads(capsys.readouterr().out)
    assert report["fft_peak_hz"] == 256
    assert report["fft_peak_volts"] == pytest.approx(1.2, abs=1e-8)
    assert report["psd_integrated_v2"] == pytest.approx(0.72, abs=1e-8)
    assert report["noise_snr_db"] == pytest.approx(18.5733, abs=0.2)
    assert report["snr_after_db"] - report["snr_before_db"] > 15
    assert report["interferer_rejection_db"] > 40
    samples = np.loadtxt(tmp_path / "samples.csv", skiprows=1, delimiter=",")
    assert samples.shape == (32768, 4)
    assert (tmp_path / "spectrum.csv").exists()
    assert json.loads((tmp_path / "report.json").read_text()) == report


def test_sweep_cli_and_invalid_input(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["rf-sweep", "--frequencies", "1000000", "--powers", "-20", "--output", str(tmp_path)],
    )
    sweep.main()
    assert len(list(tmp_path.glob("*/manifest.json"))) == 1
    assert capsys.readouterr().out.strip()
    monkeypatch.setattr(
        "sys.argv", ["rf-sweep", "--span", "0", "--output", str(tmp_path / "invalid")]
    )
    with pytest.raises(SystemExit) as error:
        sweep.main()
    assert error.value.code == 2
    assert not (tmp_path / "invalid").exists()
