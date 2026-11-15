"""Generated channel Touchstone files with known features, not measured hardware.

The channel is a 50 ohm line with a 75 ohm section and a 0.3 pF shunt capacitance, so the
TDR has known features at about 3 to 5 cm and 10 cm. The fixture is a short 50 to 55 ohm
line. Crosstalk is a simple frequency-dependent coupling added to the second lane.
"""

from pathlib import Path

import numpy as np
import skrf as rf
from skrf.media import DefinedGammaZ0

SPEED_OF_LIGHT = 299792458.0
VELOCITY_FACTOR = 0.66
Z0 = 50.0
FREQUENCIES_HZ = np.arange(1, 401) * 50e6
FILES = (
    "channel.s4p",
    "fixture_lane.s2p",
    "thru.s2p",
    "open.s2p",
    "short.s2p",
)


def _medium():
    f = rf.Frequency.from_f(FREQUENCIES_HZ, unit="hz")
    w = 2 * np.pi * f.f
    f_ghz = f.f / 1e9
    # Dielectric and conductor loss, in dB per metre, roughly rising with the square root of
    # frequency plus a dielectric term. The values are chosen for a plausible PCB trace.
    loss_db_per_m = 2.0 * np.sqrt(f_ghz) + 1.0 * f_ghz
    alpha = loss_db_per_m / 8.686
    gamma = alpha + 1j * w / (VELOCITY_FACTOR * SPEED_OF_LIGHT)
    return DefinedGammaZ0(frequency=f, z0=Z0, gamma=gamma)


def _channel(med):
    return (
        med.line(0.03, unit="m")
        ** med.impedance_mismatch(50, 75)
        ** med.line(0.02, unit="m")
        ** med.impedance_mismatch(75, 50)
        ** med.line(0.05, unit="m")
        ** med.shunt_capacitor(0.3e-12)
        ** med.line(0.05, unit="m")
    )


def _reflect_standard(value):
    """A two-port in which both ports reflect with the given value and no transmission."""
    n = len(FREQUENCIES_HZ)
    s = np.zeros((n, 2, 2), dtype=complex)
    s[:, 0, 0] = value
    s[:, 1, 1] = value
    return s


def generate_channel_inputs(directory: str | Path) -> dict:
    """Write the channel, fixture and standard files without replacing existing ones."""
    directory = Path(directory)
    if any((directory / name).exists() for name in FILES):
        raise FileExistsError("Synthetic channel output already contains files; use a new folder")
    directory.mkdir(parents=True, exist_ok=True)
    med = _medium()
    f = med.frequency
    dut = _channel(med)
    fixture = (
        med.line(0.04, unit="m")
        ** med.impedance_mismatch(50, 55)
        ** med.line(0.01, unit="m")
        ** med.impedance_mismatch(55, 50)
    )
    thru = fixture**fixture
    open_std = fixture ** rf.Network(frequency=f, s=_reflect_standard(1.0), z0=Z0) ** fixture
    short_std = fixture ** rf.Network(frequency=f, s=_reflect_standard(-1.0), z0=Z0) ** fixture
    lane = fixture**dut**fixture

    # Four-port channel: lane A on ports 1-2, lane B on ports 3-4. Crosstalk is weak and grows
    # with frequency, and the far-end term follows the lane's own insertion loss.
    w = 2 * np.pi * f.f
    f_ghz = f.f / 1e9
    phase = np.exp(-1j * w * 0.1e-9)
    near = 10 ** ((-30.0 - 0.9 * f_ghz) / 20) * phase
    far = 10 ** ((-36.0 - 0.5 * f_ghz) / 20) * phase * dut.s[:, 1, 0]
    s4 = np.zeros((len(f.f), 4, 4), dtype=complex)
    s4[:, 0:2, 0:2] = dut.s
    s4[:, 2:4, 2:4] = dut.s
    s4[:, 2, 0] = s4[:, 0, 2] = near
    s4[:, 3, 1] = s4[:, 1, 3] = near
    s4[:, 3, 0] = s4[:, 0, 3] = far
    s4[:, 2, 1] = s4[:, 1, 2] = far

    outputs = {
        "channel.s4p": rf.Network(frequency=f, s=s4, z0=Z0, name="channel"),
        "fixture_lane.s2p": lane,
        "thru.s2p": thru,
        "open.s2p": open_std,
        "short.s2p": short_std,
    }
    for name, net in outputs.items():
        net.write_touchstone(filename=str(directory / name), form="ri")
        _tidy(directory / name)
    return {name: str(directory / name) for name in FILES}


def _tidy(path: Path):
    """Remove trailing spaces so the generated files diff cleanly."""
    path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")
