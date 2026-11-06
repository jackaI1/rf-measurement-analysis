"""A deterministic cubic amplifier and thermal-noise source, not bench measurements."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import skrf as rf

GAIN_DB = 20.0
P1DB_DBM = -5.0
NF_DB = 3.0
# Normalized input voltage squared is power in mW.
CUBIC_COEFFICIENT = (1 - 10 ** (-1 / 20)) / 10 ** (P1DB_DBM / 10)


def generate(directory: str | Path) -> dict:
    """Write model files without replacing any existing measurement file."""
    directory = Path(directory)
    names = ["amplifier.s2p", "power.csv", "two_tone.csv", "noise.csv", "expected.json"]
    if any((directory / name).exists() for name in names):
        raise FileExistsError(
            "Synthetic output already contains measurement files; use a new folder"
        )
    directory.mkdir(parents=True, exist_ok=True)
    f = np.linspace(0.8e9, 1.2e9, 51)
    delay = np.exp(-2j * np.pi * f * 1e-9)
    s = np.zeros((len(f), 2, 2), dtype=complex)
    s[:, 0, 0] = 0.1 * delay
    s[:, 1, 1] = 0.05 * delay
    s[:, 1, 0] = 10 ** (GAIN_DB / 20) * delay
    s[:, 0, 1] = 0.001 * delay
    rf.Network(
        frequency=rf.Frequency.from_f(f, unit="hz"), s=s, z0=50, name="amplifier"
    ).write_touchstone(filename=str(directory / "amplifier"), form="ri")
    touchstone = directory / "amplifier.s2p"
    touchstone.write_text(
        "\n".join(line.rstrip() for line in touchstone.read_text().splitlines()) + "\n"
    )
    pin = np.arange(-50.0, 0.01, 0.25)
    # For y=a1*x-a3*x^3, a single sine's fundamental is a1*A*(1-c*A^2).
    pout = pin + GAIN_DB + 20 * np.log10(1 - CUBIC_COEFFICIENT * 10 ** (pin / 10))
    pd.DataFrame({"pin_dbm": pin, "pout_dbm": pout}).to_csv(directory / "power.csv", index=False)
    pin = np.arange(-60.0, -34.9, 2.5)
    fundamental = pin + GAIN_DB + 20 * np.log10(1 - 3 * CUBIC_COEFFICIENT * 10 ** (pin / 10))
    iip3 = float(10 * np.log10(1 / CUBIC_COEFFICIENT))
    im3 = 3 * pin + GAIN_DB - 2 * iip3
    pd.DataFrame({"pin_dbm": pin, "fundamental_dbm": fundamental, "im3_dbm": im3}).to_csv(
        directory / "two_tone.csv", index=False
    )
    te = 290 * (10 ** (NF_DB / 10) - 1)

    # k*T*B*G in W; multiply by 1000 before converting to dBm.
    def noise_power(temp):
        return 10 * np.log10(1.380649e-23 * (temp + te) * 1e6 * 10 ** (GAIN_DB / 10) * 1000)

    pd.DataFrame(
        {
            "frequency_hz": f,
            "t_hot_k": 870.0,
            "t_cold_k": 290.0,
            "hot_dbm": noise_power(870),
            "cold_dbm": noise_power(290),
        }
    ).to_csv(directory / "noise.csv", index=False)
    expected = {
        "gain_db": GAIN_DB,
        "input_p1db_dbm": P1DB_DBM,
        "output_p1db_dbm": P1DB_DBM + GAIN_DB - 1,
        "iip3_dbm": iip3,
        "oip3_dbm": iip3 + GAIN_DB,
        "noise_figure_db": NF_DB,
        "noise_temperature_k": te,
        "cubic_coefficient_per_mw": CUBIC_COEFFICIENT,
        "label": "Synthetic cubic amplifier, not hardware measurements",
    }
    (directory / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")
    return expected
