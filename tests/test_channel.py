import json
import warnings

import numpy as np
import pandas as pd
import pytest
import skrf as rf
from skrf.media import DefinedGammaZ0

from rf_measurement import channel, cli
from rf_measurement.channel_report import create_channel_report
from rf_measurement.synthetic_channel import generate_channel_inputs

C0 = 299792458.0
VF = 0.66


def _medium(f_hz, loss_np_per_m=0.0):
    f = rf.Frequency.from_f(np.asarray(f_hz, dtype=float), unit="hz")
    gamma = loss_np_per_m + 1j * 2 * np.pi * f.f / (VF * C0)
    return DefinedGammaZ0(frequency=f, z0=50, gamma=gamma)


def _grid():
    return np.arange(1, 401) * 50e6


def _write(net, path):
    net.write_touchstone(filename=str(path), form="ri")
    return path


def test_uniform_grid_starts_at_dc_with_magnitude_of_first_point():
    med = _medium(_grid())
    line = med.line(0.2, unit="m")
    grid = channel.uniform_dc_network(line)
    assert grid.f[0] == 0.0
    assert np.allclose(np.diff(grid.f), 50e6)
    # A linear complex extrapolation would give about 1.2 here. Magnitude-with-zero-phase is exact.
    assert grid.s[0, 1, 0] == pytest.approx(1.0, abs=1e-9)
    assert grid.s[0, 0, 0] == pytest.approx(0.0, abs=1e-9)


def test_tdr_finds_the_known_impedance_features(tmp_path):
    files = generate_channel_inputs(tmp_path / "in")
    net = channel.load_channel(files["channel.s4p"])
    profile = channel.tdr(net, velocity_factor=VF)
    found = channel.find_discontinuities(profile, z0=50.0)
    highs = found[found["type"] == "high"]
    lows = found[found["type"] == "low"]
    assert len(highs) == 1 and len(lows) == 1
    # The 75 ohm section spans 3 to 5 cm, and the shunt capacitor sits at 10 cm.
    assert 0.03 <= highs.iloc[0]["distance_m"] <= 0.05
    assert highs.iloc[0]["impedance_ohm"] == pytest.approx(75, abs=2)
    assert lows.iloc[0]["distance_m"] == pytest.approx(0.10, abs=0.01)
    assert lows.iloc[0]["impedance_ohm"] < 45


def test_tdr_distance_scales_with_velocity_factor(tmp_path):
    files = generate_channel_inputs(tmp_path / "in")
    net = channel.load_channel(files["channel.s4p"])
    full = channel.tdr(net, velocity_factor=1.0)
    half = channel.tdr(net, velocity_factor=0.5)
    assert np.allclose(half["distance_m"] * 2, full["distance_m"])
    assert np.allclose(half["time_s"], full["time_s"])
    with pytest.raises(ValueError, match="Velocity factor"):
        channel.tdr(net, velocity_factor=1.5)


def test_find_discontinuities_ignores_small_ripple_and_merges_neighbours():
    distance = np.linspace(0, 0.5, 501)
    impedance = np.full_like(distance, 50.0)
    impedance += 0.5 * np.sin(distance * 400)  # ripple well under the threshold
    impedance[(distance > 0.2) & (distance < 0.21)] = 65.0
    profile = pd.DataFrame(
        {"distance_m": distance, "impedance_ohm": impedance, "time_s": distance, "reflection": 0}
    )
    found = channel.find_discontinuities(profile, z0=50.0, min_deviation_ohm=5.0)
    assert len(found) == 1
    assert found.iloc[0]["type"] == "high"
    assert found.iloc[0]["distance_m"] == pytest.approx(0.2, abs=0.01)


def test_fixture_deembedding_is_exact_for_an_ideal_line_fixture():
    med = _medium(_grid())
    dut = med.line(0.03, unit="m") ** med.shunt_capacitor(0.2e-12) ** med.line(0.02, unit="m")
    half = med.line(0.05, unit="m")
    measured = half**dut**half
    thru = half**half
    corrected = channel.deembed_fixture(measured, thru)
    ref = channel.uniform_dc_network(dut)
    assert np.allclose(corrected.s[1:], ref.s[1:], atol=1e-9)


def test_open_short_standards_leave_an_ideal_fixture_unchanged():
    med = _medium(_grid())
    dut = med.line(0.03, unit="m") ** med.shunt_capacitor(0.2e-12) ** med.line(0.02, unit="m")
    half = med.line(0.05, unit="m")
    f = dut.frequency
    open_std = half ** _reflect_standard(f, 1.0) ** half
    short_std = half ** _reflect_standard(f, -1.0) ** half
    measured = half**dut**half
    corrected = channel.deembed_fixture(measured, half**half, open=open_std, short=short_std)
    ref = channel.uniform_dc_network(dut)
    assert np.allclose(corrected.s[1:], ref.s[1:], atol=1e-9)


def _reflect_standard(frequency, value):
    """A two-port that reflects the given value at both ports and passes nothing."""
    s = np.zeros((len(frequency.f), 2, 2), dtype=complex)
    s[:, 0, 0] = value
    s[:, 1, 1] = value
    return rf.Network(frequency=frequency, s=s, z0=50)


def test_fixture_with_impedance_step_is_bounded_and_open_short_is_reported():
    med = _medium(_grid())
    dut = med.line(0.03, unit="m") ** med.shunt_capacitor(0.2e-12) ** med.line(0.02, unit="m")
    half = (
        med.line(0.03, unit="m")
        ** med.impedance_mismatch(50, 60)
        ** med.line(0.02, unit="m")
        ** med.impedance_mismatch(60, 50)
    )
    measured = half**dut**half
    corrected = channel.deembed_fixture(measured, half**half)
    ref = channel.uniform_dc_network(dut)
    error = np.abs(corrected.s[1:] - ref.s[1:])
    # The P370 split is approximate for a fixture with a step. Keep the test strict enough to
    # catch regressions, and loose enough to match what the method can do.
    assert error[:, 1, 0].max() < 0.05
    assert error[:, 0, 0].max() < 0.12


def test_deembedding_rejects_non_two_port_inputs():
    med = _medium(_grid())
    line = med.line(0.1, unit="m")
    four_port = rf.Network(
        frequency=line.frequency, s=np.zeros((len(line.f), 4, 4), dtype=complex), z0=50
    )
    with pytest.raises(ValueError, match="two-port"):
        channel.deembed_fixture(four_port, line)


def test_correct_reflection_recovers_true_value_after_gain_and_offset():
    true = np.array([0.1 + 0.2j, -0.3j, 0.5])
    gain, offset = 0.9 - 0.05j, 0.02 + 0.01j
    measured = gain * true + offset
    open_m = gain * 1 + offset
    short_m = gain * -1 + offset
    recovered = channel.correct_reflection(measured, open_m, short_m)
    assert np.allclose(recovered, true)


def test_correct_reflection_leaves_non_separating_points_alone():
    gamma = np.array([0.3, 0.4])
    opened = np.array([0.001, 1.0])
    shorted = np.array([-0.001, -1.0])
    out = channel.correct_reflection(gamma, opened, shorted)
    assert out[0] == pytest.approx(0.3)
    assert out[1] == pytest.approx(0.4)
    with pytest.raises(ValueError, match="do not separate"):
        channel.correct_reflection(gamma, np.zeros(2), np.zeros(2))


def test_channel_checks_compare_against_limits_and_name_the_worst_point():
    med = _medium(_grid())
    line = med.line(0.1, unit="m")
    # A matched line with constant loss gives a known insertion loss and return loss.
    ideal_loss = rf.Network(frequency=line.frequency, s=_matched(line, 10 ** (-2 / 20)), z0=50)
    checks = channel.channel_checks(ideal_loss, max_loss_db=3.0, min_return_loss_db=20.0)
    il = checks[checks["check"] == "insertion_loss_db"].iloc[0]
    assert il["worst_value"] == pytest.approx(2.0, abs=1e-9)
    assert bool(il["passed"]) is True
    rl = checks[checks["check"] == "return_loss_db"].iloc[0]
    assert rl["worst_value"] > 100  # S11 is zero, so return loss is unbounded
    assert len(checks) == 2  # two-port: no crosstalk rows

    strict = channel.channel_checks(ideal_loss, max_loss_db=1.0)
    assert bool(strict[strict["check"] == "insertion_loss_db"].iloc[0]["passed"]) is False
    unjudged = channel.channel_checks(ideal_loss)
    assert unjudged["passed"].isna().all()


def _matched(line, gain):
    s = np.zeros((len(line.f), 2, 2), dtype=complex)
    s[:, 1, 0] = gain
    s[:, 0, 1] = gain
    return s


def test_four_port_crosstalk_rows_are_checked_against_the_limit(tmp_path):
    files = generate_channel_inputs(tmp_path / "in")
    net = channel.load_channel(files["channel.s4p"])
    checks = channel.channel_checks(net, max_crosstalk_db=-30.0)
    names = set(checks["check"])
    assert {"near_end_crosstalk_db", "far_end_crosstalk_db"} <= names
    near = checks[checks["check"] == "near_end_crosstalk_db"].iloc[0]
    assert near["worst_value"] == pytest.approx(-30.045, abs=0.01)
    assert bool(near["passed"]) is True


def test_prbs7_has_full_period_and_balance():
    bits = channel.prbs7(254)
    assert np.array_equal(bits[:127], bits[127:])
    assert bits[:127].sum() == 64


def test_eye_rejects_nonpositive_bit_rate():
    with pytest.raises(ValueError, match="Bit rate"):
        channel.eye_diagram(_medium(_grid()).line(0.1, unit="m"), bitrate_bps=0)


def test_ideal_line_has_an_open_eye_regardless_of_its_delay():
    short = _medium(_grid()).line(0.1, unit="m")
    longer = _medium(_grid()).line(0.3, unit="m")
    eye_short = channel.eye_diagram(short, bitrate_bps=5e9)
    eye_long = channel.eye_diagram(longer, bitrate_bps=5e9)
    assert eye_short.eye_height_fraction > 0.95
    assert eye_long.eye_height_fraction > 0.95
    assert eye_short.eye_width_ui == pytest.approx(1.0)
    assert eye_long.eye_width_ui == pytest.approx(1.0)


def test_lossy_channel_closes_the_eye():
    lossy = _medium(_grid(), loss_np_per_m=30.0).line(0.5, unit="m")
    eye = channel.eye_diagram(lossy, bitrate_bps=10e9)
    assert eye.eye_height_fraction < 0.05
    assert eye.eye_width_ui == pytest.approx(0.0)
    assert set(eye.metrics()) == {
        "bitrate_gbps",
        "eye_height_fraction",
        "eye_width_ui",
        "best_phase_ui",
    }


def test_channel_report_writes_outputs_and_refuses_to_overwrite_inputs(tmp_path):
    files = generate_channel_inputs(tmp_path / "in")
    out = tmp_path / "report"
    summary = create_channel_report(
        files["channel.s4p"],
        out,
        max_loss_db=10.0,
        min_return_loss_db=8.0,
        max_crosstalk_db=-30.0,
        min_eye_height=0.3,
    )
    for name in (
        "summary.json",
        "checks.csv",
        "discontinuities.csv",
        "tdr.csv",
        "report.md",
        "tdr.png",
        "eye.png",
        "response.png",
    ):
        assert (out / name).exists()
    saved = json.loads((out / "summary.json").read_text())
    assert saved["ports"] == 4
    assert saved["fixture_deembedded"] is False
    # The return-loss limit fails for this channel, so the summary must say so.
    assert summary["all_judged_checks_passed"] is False
    assert not list(tmp_path.glob(".rf-channel-*"))
    collide = tmp_path / "collide"
    collide.mkdir()
    copied = collide / "summary.json"
    copied.write_text("{}")
    with pytest.raises(ValueError, match="overwrite"):
        create_channel_report(copied, collide)


def test_channel_report_with_fixture_matches_direct_analysis(tmp_path):
    files = generate_channel_inputs(tmp_path / "in")
    summary = create_channel_report(
        files["fixture_lane.s2p"],
        tmp_path / "fixture",
        thru=files["thru.s2p"],
        open_path=files["open.s2p"],
        short_path=files["short.s2p"],
        max_loss_db=10.0,
    )
    direct = create_channel_report(files["channel.s4p"], tmp_path / "direct", max_loss_db=10.0)
    il_fixture = _check(summary, "insertion_loss_db")["worst_value"]
    il_direct = _check(direct, "insertion_loss_db")["worst_value"]
    # The fixture adds a small residual, so the de-embedded lane should be close to the channel.
    assert abs(il_fixture - il_direct) < 0.2
    assert summary["fixture_deembedded"] is True
    assert summary["open_short_correction"] is True


def _check(summary, name):
    return next(row for row in summary["checks"] if row["check"] == name)


def test_report_needs_both_open_and_short(tmp_path):
    files = generate_channel_inputs(tmp_path / "in")
    with pytest.raises(ValueError, match="both open and short"):
        create_channel_report(
            files["fixture_lane.s2p"],
            tmp_path / "out",
            thru=files["thru.s2p"],
            open_path=files["open.s2p"],
        )


def test_synthetic_channel_refuses_existing_files_and_has_expected_ports(tmp_path):
    files = generate_channel_inputs(tmp_path / "in")
    assert set(files) == {
        "channel.s4p",
        "fixture_lane.s2p",
        "thru.s2p",
        "open.s2p",
        "short.s2p",
    }
    assert channel.load_channel(files["channel.s4p"]).nports == 4
    with pytest.raises(FileExistsError):
        generate_channel_inputs(tmp_path / "in")


def test_channel_demo_cli_runs_and_reports_pass_and_fail(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rf-analysis", "channel-demo", "--output", str(tmp_path)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cli.main()
    assert "Report written to" in capsys.readouterr().out
    fixture = json.loads((tmp_path / "fixture-report" / "summary.json").read_text())
    channel_summary = json.loads((tmp_path / "channel-report" / "summary.json").read_text())
    assert fixture["fixture_deembedded"] is True
    assert channel_summary["ports"] == 4
    results = {row["check"]: row["passed"] for row in channel_summary["checks"]}
    assert results["insertion_loss_db"] is True
    assert results["return_loss_db"] is False  # the demo has a 0.3 pF via, so return loss fails


def test_channel_cli_rejects_bad_velocity_factor(tmp_path, monkeypatch):
    files = generate_channel_inputs(tmp_path / "in")
    monkeypatch.setattr(
        "sys.argv",
        [
            "rf-analysis",
            "channel",
            "--touchstone",
            files["channel.s4p"],
            "--output",
            str(tmp_path / "out"),
            "--velocity-factor",
            "2",
        ],
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
