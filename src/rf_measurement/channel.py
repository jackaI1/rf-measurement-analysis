"""Channel checks from Touchstone files: TDR, fixture removal, limits and a PRBS eye.

Everything here works on the same S-parameters a VNA exports. Time-domain results are
only as good as the frequency range: a 50 MHz to 20 GHz sweep gives a time step of about
25 ps and a window of 20 ns, and nothing outside that band is inferred.
"""

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import skrf as rf
from scipy.signal import find_peaks
from skrf.calibration.deembedding import IEEEP370_SE_NZC_2xThru

SPEED_OF_LIGHT = 299792458.0
_TIME_OVERSAMPLE = 8
# A phase counts as open when its height is above this fraction of the full swing.
EYE_OPEN_FRACTION = 0.01


def _check_reference(net):
    if net.nports < 2:
        raise ValueError("Channel checks need a two-port or four-port Touchstone file")


def load_channel(path: str | Path) -> rf.Network:
    """Read a two-port or four-port Touchstone file with finite, increasing frequencies."""
    net = rf.Network(str(path))
    if net.nports not in (2, 4) or not len(net.f):
        raise ValueError("Need a nonempty two-port or four-port Touchstone network")
    if not np.isfinite(net.s).all() or np.any(net.f <= 0) or np.any(np.diff(net.f) <= 0):
        raise ValueError("Need finite S-parameters and positive, increasing frequencies")
    if not np.isfinite(net.z0).all() or np.any(net.z0.real <= 0) or np.any(net.z0.imag != 0):
        raise ValueError("Need positive real reference impedances")
    return net


def uniform_dc_network(net: rf.Network) -> rf.Network:
    """Return the network on a uniform grid that starts at 0 Hz.

    The 0 Hz point is the first measured magnitude with zero phase. For a passive,
    reciprocal line this is exact at DC. A linear extrapolation of the complex value
    is not: a 1.5 ns delay would make |S21(0)| about 1.2. Every other frequency is
    interpolated linearly in real and imaginary parts, because the time-domain
    transforms need a uniform grid and measured sweeps rarely have one.
    """
    f = np.asarray(net.f, dtype=float)
    if len(f) < 3:
        raise ValueError("Need at least three frequency points for a time-domain transform")
    step = float(np.median(np.diff(f)))
    grid = np.arange(0.0, f[-1] + step / 2, step)
    s = net.s
    s_dc = np.abs(s[0]).astype(complex)
    f_aug = np.concatenate(([0.0], f))
    s_aug = np.concatenate((s_dc[None], s), axis=0)
    out = np.empty((len(grid),) + s.shape[1:], dtype=complex)
    for m in range(s.shape[1]):
        for n in range(s.shape[2]):
            out[:, m, n] = np.interp(grid, f_aug, s_aug[:, m, n].real) + 1j * np.interp(
                grid, f_aug, s_aug[:, m, n].imag
            )
    z0 = np.real(net.z0[0])
    return rf.Network(
        frequency=rf.Frequency.from_f(grid, unit="hz"),
        s=out,
        z0=z0,
        name=net.name,
    )


def tdr(
    net: rf.Network, *, velocity_factor: float = 1.0, window: str = "hamming", pad: int = 0
) -> pd.DataFrame:
    """Step response of port 1 converted to impedance versus one-way distance.

    The reflection step is scikit-rf's `step_response` of S11. Impedance is
    Z = Z0 (1 + rho) / (1 - rho). Distance is half the round trip at
    velocity_factor times the speed of light, so 0.66 means a typical PCB trace.
    """
    _check_reference(net)
    if not 0 < velocity_factor <= 1:
        raise ValueError("Velocity factor must be greater than 0 and at most 1")
    grid = uniform_dc_network(net)
    z0 = float(np.real(grid.z0[0, 0]))
    t, step = grid.s11.step_response(window=window, pad=pad)
    keep = t >= 0
    rho = np.real(step[keep])
    with np.errstate(divide="ignore", invalid="ignore"):
        impedance = z0 * (1 + rho) / (1 - rho)
    return pd.DataFrame(
        {
            "time_s": t[keep],
            "distance_m": t[keep] * velocity_factor * SPEED_OF_LIGHT / 2,
            "reflection": rho,
            "impedance_ohm": impedance,
        }
    )


def find_discontinuities(
    profile: pd.DataFrame,
    z0: float,
    *,
    min_deviation_ohm: float = 5.0,
    min_spacing_m: float = 0.005,
) -> pd.DataFrame:
    """List impedance peaks (high) and dips (low) that stand out from z0.

    Each row gives the one-way distance of a local extreme, its impedance and whether it
    is above or below the reference. Impedance steps show up as plateaus, so the reported
    distance is the middle of the plateau's highest or lowest value. Closer features than
    min_spacing_m are merged because the step response blurs them together.
    """
    distance = profile["distance_m"].to_numpy()
    impedance = profile["impedance_ohm"].to_numpy()
    finite = np.isfinite(impedance)
    impedance = np.where(finite, impedance, z0)
    if len(distance) < 3:
        return pd.DataFrame(columns=["distance_m", "impedance_ohm", "deviation_ohm", "type"])
    spacing = float(np.median(np.diff(distance)))
    rows = []
    for sign, kind in ((1, "high"), (-1, "low")):
        deviation = sign * (impedance - z0)
        peaks, _ = find_peaks(
            deviation,
            height=min_deviation_ohm,
            distance=max(1, round(min_spacing_m / spacing)),
        )
        for i in peaks:
            rows.append(
                {
                    "distance_m": distance[i],
                    "impedance_ohm": impedance[i],
                    "deviation_ohm": impedance[i] - z0,
                    "type": kind,
                }
            )
    out = pd.DataFrame(rows, columns=["distance_m", "impedance_ohm", "deviation_ohm", "type"])
    return out.sort_values("distance_m", ignore_index=True)


def correct_reflection(gamma, gamma_open, gamma_short, *, min_gain=0.5):
    """Remove a gain and offset error from a reflection, using open and short standards.

    The measured value is modelled as gain * true + offset. An ideal open reads +1 and an
    ideal short reads -1, which gives gain = (open - short) / 2 and offset =
    (open + short) / 2. Frequencies where the gain is below min_gain do not separate the
    standards and keep their uncorrected value.
    """
    gamma = np.asarray(gamma, dtype=complex)
    gain = (np.asarray(gamma_open) - np.asarray(gamma_short)) / 2
    offset = (np.asarray(gamma_open) + np.asarray(gamma_short)) / 2
    usable = np.abs(gain) > min_gain
    if not usable.any():
        raise ValueError("Open and short standards do not separate at any frequency")
    out = gamma.copy()
    out[usable] = (gamma[usable] - offset[usable]) / gain[usable]
    return out


def deembed_fixture(dut, thru, *, open=None, short=None):
    """Remove two test-fixture halves with a thru, then correct port reflections.

    1. The thru (fixture to fixture) is split into two halves with scikit-rf's IEEE P370
       2x-thru method, and both halves are removed from the DUT measurement.
    2. If open and short standards are given, they go through the same removal. Their
       de-embedded port-1 and port-2 reflections should be +1 and -1. A two-term
       reflection model (gain k and offset b) fitted to them corrects each port's S11
       and S22 of the DUT. S21 and S12 are left as the thru step produced them.

    All networks must be two-port. Standards are interpolated onto the DUT frequencies.
    The P370 split is exact for ideal lines and approximate for mismatched fixtures;
    the reflection correction reduces that residual only where open and short
    standards exist.
    """
    for name, net in (("DUT", dut), ("thru", thru)):
        if net.nports != 2:
            raise ValueError(f"Fixture de-embedding needs a two-port {name} file")
    dut_grid = uniform_dc_network(dut)
    thru_grid = uniform_dc_network(thru).interpolate(dut_grid.frequency, kind="linear")
    z0 = float(np.real(dut_grid.z0[0, 0]))
    with warnings.catch_warnings():
        # The 0 Hz point is kept on purpose, so skrf's DC notice is expected here.
        warnings.filterwarnings("ignore", message="DC point detected", category=RuntimeWarning)
        splitter = IEEEP370_SE_NZC_2xThru(dummy_2xthru=thru_grid, z0=z0)
        corrected = splitter.deembed(dut_grid)
    if open is None or short is None:
        return corrected
    standards = {}
    for name, net in (("open", open), ("short", short)):
        if net.nports != 2:
            raise ValueError(f"Fixture de-embedding needs a two-port {name} file")
        grid = uniform_dc_network(net).interpolate(dut_grid.frequency, kind="linear")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="DC point detected", category=RuntimeWarning)
            standards[name] = splitter.deembed(grid)
    s = corrected.s.copy()
    for port in (0, 1):
        s[:, port, port] = correct_reflection(
            corrected.s[:, port, port],
            standards["open"].s[:, port, port],
            standards["short"].s[:, port, port],
        )
    corrected.s = s
    return corrected


def _db(x):
    with np.errstate(divide="ignore"):
        return 20 * np.log10(np.abs(x))


def channel_checks(
    net: rf.Network,
    *,
    max_loss_db: float | None = None,
    min_return_loss_db: float | None = None,
    max_crosstalk_db: float | None = None,
) -> pd.DataFrame:
    """Insertion loss, return loss and crosstalk against stated limits.

    Insertion loss is -20 log10 |S21| (worst point is the largest loss). Return loss is
    -20 log10 |S11| (or |S22|), and the worst point is the smallest return loss. For a
    four-port file, ports 1-2 are one lane and 3-4 the neighbour lane: near-end crosstalk
    is |S31| and far-end crosstalk is |S41|, and the worst point is the largest value.
    Limits left as None are reported but not judged.
    """
    _check_reference(net)
    f_ghz = net.f / 1e9
    rows = []

    def add(name, values, limit, worse_is_high, unit="dB"):
        i = int(np.nanargmax(values) if worse_is_high else np.nanargmin(values))
        worst = float(values[i])
        if limit is None:
            passed = None
        elif worse_is_high:
            passed = bool(worst <= limit)
        else:
            passed = bool(worst >= limit)
        rows.append(
            {
                "check": name,
                "worst_value": worst,
                "worst_frequency_ghz": float(f_ghz[i]),
                "limit": limit,
                "unit": unit,
                "passed": passed,
            }
        )

    loss = -_db(net.s[:, 1, 0])
    add("insertion_loss_db", loss, max_loss_db, worse_is_high=True)
    rl = np.minimum(-_db(net.s[:, 0, 0]), -_db(net.s[:, 1, 1]))
    add("return_loss_db", rl, min_return_loss_db, worse_is_high=False)
    if net.nports >= 4:
        next_xtalk = _db(net.s[:, 2, 0])
        fext_xtalk = _db(net.s[:, 3, 0])
        add("near_end_crosstalk_db", next_xtalk, max_crosstalk_db, worse_is_high=True)
        add("far_end_crosstalk_db", fext_xtalk, max_crosstalk_db, worse_is_high=True)
    return pd.DataFrame(rows)


def prbs7(n_bits: int = 127) -> np.ndarray:
    """PRBS-7 bits from x^7 + x^6 + 1, starting from an all-ones register.

    One period has 127 bits, with 64 ones and 63 zeros.
    """
    state = 0x7F
    bits = np.empty(n_bits, dtype=int)
    for i in range(n_bits):
        bit = ((state >> 6) ^ (state >> 5)) & 1
        bits[i] = bit
        state = ((state << 1) | bit) & 0x7F
    return bits


def _channel_step(net: rf.Network):
    """Step response of S21 on a causal time axis, with its DC level set to S21(0).

    The spectrum is tapered to zero over its top 20 percent, which limits the ringing a
    sharp band edge would otherwise add to the step.
    """
    grid = uniform_dc_network(net)
    f = grid.f
    s21 = grid.s[:, 1, 0]
    edge = 0.8 * f[-1]
    taper = np.ones_like(f)
    above = f > edge
    taper[above] = 0.5 * (1 + np.cos(np.pi * (f[above] - edge) / (f[-1] - edge)))
    # Zero-padding the spectrum samples the same band-limited step more finely without
    # changing the 1/df window. Linear interpolation on the native 2 * fmax grid is too
    # coarse for eyes at tens of gigabits per second.
    n = 2 * (len(f) - 1) * _TIME_OVERSAMPLE
    h = np.fft.irfft(s21 * taper, n=n)
    dt = 1.0 / (n * (f[1] - f[0]))
    t = np.arange(n) * dt
    return t, np.cumsum(h)


@dataclass(frozen=True)
class EyeResult:
    """Eye metrics plus the overlaid traces, so a plot can draw them."""

    bitrate_bps: float
    unit_interval_s: float
    eye_height_fraction: float
    eye_width_ui: float
    best_phase_ui: float
    phase_ui: np.ndarray
    height_fraction: np.ndarray
    traces: np.ndarray

    def metrics(self) -> dict:
        return {
            "bitrate_gbps": self.bitrate_bps / 1e9,
            "eye_height_fraction": self.eye_height_fraction,
            "eye_width_ui": self.eye_width_ui,
            "best_phase_ui": self.best_phase_ui,
        }


def eye_diagram(
    net: rf.Network,
    *,
    bitrate_bps: float,
    n_bits: int = 508,
    phases: int = 64,
    memory_ui: int = 24,
) -> EyeResult:
    """Overlay an ideal NRZ PRBS-7 pattern sent through the channel's S21.

    The output at any time is superposed from the channel step response, so the eye is
    not limited to the time grid of the file. Heights are a fraction of the full
    transmitter swing (one bit goes from -1 to +1, a swing of 2). A phase is open when
    the lowest '1' sample is above the highest '0' sample at that phase. The width is
    the fraction of the unit interval with an open eye (height above 1 percent of swing).
    """
    _check_reference(net)
    if bitrate_bps <= 0:
        raise ValueError("Bit rate must be positive")
    ui = 1.0 / bitrate_bps
    t, step = _channel_step(net)

    def step_at(times):
        return np.interp(times, t, step, left=0.0, right=float(step[-1]))

    # The channel delay can be many UI long, so the look-back must cover it plus the ISI.
    edge_s = t[int(np.argmax(np.diff(step)))]
    lead = int(np.ceil(max(edge_s, 0.0) / ui)) + memory_ui
    if n_bits <= lead + 2 or phases < 4:
        raise ValueError(
            "Use more bits than the channel delay plus its memory, and at least four phases"
        )
    symbols = 2.0 * prbs7(n_bits) - 1.0
    phase_s = np.arange(phases) * ui / phases
    lags = np.arange(lead + 1, dtype=float)
    settled = slice(lead, n_bits)
    traces = np.empty((phases, n_bits - lead))
    height = np.empty(phases)
    for p, phase in enumerate(phase_s):
        # Response to a +1 level from one bit boundary to the next: S(t) - S(t - UI).
        g = step_at(lags * ui + phase) - step_at((lags - 1) * ui + phase)
        out = np.convolve(symbols, g)[:n_bits][settled]
        # At each phase, the output is decided by the symbol at its main cursor: the lag
        # that carries the largest share of the response. A delay that is not a whole
        # number of UI moves that lag between phases, so the cursor is found per phase.
        cursor = int(np.argmax(np.abs(g)))
        decided = symbols[lead - cursor : n_bits - cursor]
        traces[p] = out
        high = out[decided > 0]
        low = out[decided < 0]
        height[p] = high.min() - low.max()
    best = int(np.argmax(height))
    return EyeResult(
        bitrate_bps=float(bitrate_bps),
        unit_interval_s=float(ui),
        eye_height_fraction=float(height[best] / 2.0),
        eye_width_ui=float(np.count_nonzero(height / 2.0 > EYE_OPEN_FRACTION) / phases),
        best_phase_ui=float(phase_s[best] / ui),
        phase_ui=phase_s / ui,
        height_fraction=height / 2.0,
        traces=traces,
    )
