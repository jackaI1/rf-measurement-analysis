"""Independent numbers catch unit, port-order and extraction mistakes."""

import numpy as np
import pandas as pd
import pytest

from rf_measurement.analysis import (
    compression,
    intercept,
    load_network,
    network_metrics,
    noise_figure,
)


def test_touchstone_port_order_and_all_metrics(tmp_path):
    path = tmp_path / "hand.s2p"
    # Touchstone order S11, S21, S12, S22; real/imaginary, GHz, 50 ohm.
    path.write_text("# GHz S RI R 50\n1 0.3 0.4 10 0 0.01 0 0.1 0\n2 0.3 0.4 10 0 0.01 0 0.1 0\n")
    net = load_network(path)
    rows = network_metrics(net)
    np.testing.assert_allclose(rows.frequency_hz, [1e9, 2e9])
    for key, value in {
        "s11_db": -6.020599913,
        "s21_db": 20,
        "s12_db": -40,
        "s22_db": -20,
        "input_return_loss_db": 6.020599913,
        "output_return_loss_db": 20,
        "input_vswr": 3,
        "output_vswr": 1.222222222,
        "gain_db": 20,
        "s11_phase_deg": 53.130102354,
    }.items():
        np.testing.assert_allclose(rows[key], value, atol=1e-8)
    for name, real, imag, phase in [
        ("s11", 0.3, 0.4, 53.130102354),
        ("s21", 10, 0, 0),
        ("s12", 0.01, 0, 0),
        ("s22", 0.1, 0, 0),
    ]:
        for suffix, expected in [("_real", real), ("_imag", imag), ("_phase_deg", phase)]:
            np.testing.assert_allclose(rows[name + suffix], expected, atol=1e-8)


def test_compression_interpolates_and_sorts():
    # Independent piecewise bench data: gain 20 dB, crossing halfway -9 to -7.
    df = pd.DataFrame(
        {"pin_dbm": [-30, -25, -20, -9, -7, -5], "pout_dbm": [-10, -5, 0, 10.5, 11.5, 12]}
    )
    result = compression(df.sample(frac=1, random_state=2), fit_points=3)
    assert result.gain_db == pytest.approx(20)
    assert result.input_p1db_dbm == pytest.approx(-8)
    assert result.output_p1db_dbm == pytest.approx(11)


def test_intercept_known_lines():
    pin = np.arange(-40, -19, 5.0)
    result = intercept(
        pd.DataFrame({"pin_dbm": pin, "fundamental_dbm": pin + 20, "im3_dbm": 3 * pin + 20 - 2 * 5})
    )
    assert result.iip3_dbm == pytest.approx(5)
    assert result.oip3_dbm == pytest.approx(25)
    assert result.fundamental_slope == pytest.approx(1)
    assert result.im3_slope == pytest.approx(3)


def test_y_factor_known_temperature():
    # NF=3.0103dB -> F=2 -> Te=290K; Y=(870+290)/(290+290)=2.
    df = pd.DataFrame(
        {
            "frequency_hz": [1e9],
            "hot_dbm": [-60],
            "cold_dbm": [-63.01029995664],
            "t_hot_k": [870],
            "t_cold_k": [290],
        }
    )
    rows = noise_figure(df)
    assert rows.y_factor.iloc[0] == pytest.approx(2)
    assert rows.noise_temperature_k.iloc[0] == pytest.approx(290, abs=1e-7)
    assert rows.noise_figure_db.iloc[0] == pytest.approx(3.01029995664)


@pytest.mark.parametrize(
    "kind", ["missing", "nan", "duplicate", "too_short", "no_crossing", "bad_slope"]
)
def test_invalid_power_sweeps(kind):
    df = pd.DataFrame(
        {"pin_dbm": [-30, -25, -20, -15, -10, -5], "pout_dbm": [-10, -5, 0, 5, 9, 12]}
    )
    if kind == "missing":
        df = df.drop(columns="pout_dbm")
    if kind == "nan":
        df.loc[2, "pout_dbm"] = np.nan
    if kind == "duplicate":
        df.loc[1, "pin_dbm"] = -30
    if kind == "too_short":
        df = df.iloc[:2]
    if kind == "no_crossing":
        df.pout_dbm = df.pin_dbm + 20
    if kind == "bad_slope":
        df.pout_dbm = 2 * df.pin_dbm + 20
    with pytest.raises(ValueError):
        compression(df, fit_points=3)


@pytest.mark.parametrize(
    "change",
    [{"hot_dbm": -65}, {"t_cold_k": -1}, {"t_hot_k": 100}, {"hot_dbm": -40}, {"cold_dbm": np.inf}],
)
def test_invalid_noise(change):
    row = {
        "frequency_hz": 1e9,
        "hot_dbm": -60,
        "cold_dbm": -63.01029995664,
        "t_hot_k": 870,
        "t_cold_k": 290,
    }
    row.update(change)
    with pytest.raises(ValueError):
        noise_figure(pd.DataFrame([row]))


def test_intercept_rejects_compressed_or_unresolved_data():
    pin = np.arange(-40, -19, 5.0)
    for fundamental, im3 in [(0.5 * pin + 20, 3 * pin), (pin + 20, pin), (pin + 20, pin + 21)]:
        with pytest.raises(ValueError):
            intercept(
                pd.DataFrame({"pin_dbm": pin, "fundamental_dbm": fundamental, "im3_dbm": im3})
            )


def test_network_rejects_one_port_and_invalid_frequency(tmp_path):
    p = tmp_path / "one.s1p"
    p.write_text("# GHz S RI R 50\n1 0 0\n")
    with pytest.raises(ValueError):
        load_network(p)
    p = tmp_path / "bad.s2p"
    p.write_text("# GHz S RI R 50\n1 0 0 1 0 0 0 0 0\n1 0 0 1 0 0 0 0 0\n")
    with pytest.warns(UserWarning), pytest.raises(ValueError):
        load_network(p)


def test_ideal_reflections_and_zero_transmission(tmp_path):
    p = tmp_path / "ideal.s2p"
    p.write_text("# GHz S RI R 50\n1 0 0 0 0 0 0 1 0\n")
    rows = network_metrics(load_network(p))
    assert np.isposinf(rows.input_return_loss_db.iloc[0])
    assert rows.input_vswr.iloc[0] == 1
    assert np.isposinf(rows.output_vswr.iloc[0])
    assert np.isneginf(rows.gain_db.iloc[0])


def test_active_reflection_vswr_is_undefined(tmp_path):
    p = tmp_path / "active.s2p"
    p.write_text("# GHz S RI R 50\n1 1.1 0 10 0 0 0 0 0\n")
    assert np.isnan(network_metrics(load_network(p)).input_vswr.iloc[0])


@pytest.mark.parametrize("fit_points", [0, 2, 2.5])
def test_invalid_baseline_selection(fit_points):
    with pytest.raises(ValueError):
        compression(pd.DataFrame(), fit_points=fit_points)


def test_intercept_region_selection():
    pin = np.arange(-40.0, -9.0, 5)
    df = pd.DataFrame({"pin_dbm": pin, "fundamental_dbm": pin + 20, "im3_dbm": 3 * pin + 10})
    df.loc[df.pin_dbm > -20, "fundamental_dbm"] -= 4
    result = intercept(df, max_input_dbm=-20)
    assert result.iip3_dbm == pytest.approx(5)
    assert result.points == 5
    with pytest.raises(ValueError):
        intercept(df)
    with pytest.raises(ValueError):
        intercept(df, max_input_dbm=-39)
    with pytest.raises(ValueError):
        intercept(df, max_input_dbm=np.nan)


def test_noise_reference_and_numeric_validation():
    df = pd.DataFrame(
        {
            "frequency_hz": [1e9],
            "hot_dbm": [-60],
            "cold_dbm": [-63.01029995664],
            "t_hot_k": [870],
            "t_cold_k": [290],
        }
    )
    assert noise_figure(df, reference_k=580).noise_figure_db.iloc[0] == pytest.approx(
        1.76091259056, abs=1e-8
    )
    with pytest.raises(ValueError):
        noise_figure(df, reference_k=0)
    df["hot_dbm"] = ["broken"]
    with pytest.raises(ValueError):
        noise_figure(df)


def test_ideal_zero_noise_figure_survives_roundoff():
    df = pd.DataFrame(
        {
            "frequency_hz": [1e9],
            "hot_dbm": [-60.0],
            "cold_dbm": [-64.77121254719662],
            "t_hot_k": [870.0],
            "t_cold_k": [290.0],
        }
    )
    rows = noise_figure(df)
    assert rows.noise_temperature_k.iloc[0] == pytest.approx(0, abs=1e-9)
    assert rows.noise_figure_db.iloc[0] == pytest.approx(0, abs=1e-9)
