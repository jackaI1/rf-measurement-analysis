"""Validated RF extraction, with dBm powers referred to the DUT connector planes."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import skrf as rf


def _table(data, columns, *, unique=None, minimum=1):
    frame = pd.read_csv(data) if isinstance(data, (str, Path)) else data.copy()
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")
    if len(frame) < minimum:
        raise ValueError(f"Need at least {minimum} rows")
    try:
        frame[list(columns)] = frame[list(columns)].astype(float)
    except (ValueError, TypeError) as exc:
        raise ValueError("Measurement columns must be numeric") from exc
    if not np.isfinite(frame[list(columns)].to_numpy()).all():
        raise ValueError("Measurement values must be finite")
    if unique:
        if frame[unique].duplicated().any():
            raise ValueError(f"Duplicate {unique} values")
        frame = frame.sort_values(unique).reset_index(drop=True)
    return frame


def load_network(path: str | Path) -> rf.Network:
    """Read a two-port Touchstone through scikit-rf, preserving its reference impedance."""
    net = rf.Network(str(path))
    if net.nports != 2 or not len(net.f):
        raise ValueError("Need a nonempty two-port Touchstone network")
    if (
        not np.isfinite(net.s).all()
        or not np.isfinite(net.f).all()
        or np.any(net.f <= 0)
        or np.any(np.diff(net.f) <= 0)
    ):
        raise ValueError("Need finite S-parameters and positive, increasing frequencies")
    if not np.isfinite(net.z0).all() or np.any(net.z0.real <= 0) or np.any(net.z0.imag != 0):
        raise ValueError("Need positive real reference impedances")
    return net


def network_metrics(net: rf.Network) -> pd.DataFrame:
    """All four complex S-parameters; matched-port |S21| gain, RL and port VSWR."""
    rows = {"frequency_hz": net.f}
    with np.errstate(divide="ignore", invalid="ignore"):
        for m, n in ((0, 0), (1, 0), (0, 1), (1, 1)):
            label = f"s{m + 1}{n + 1}"
            s = net.s[:, m, n]
            rows.update(
                {
                    label + "_real": s.real,
                    label + "_imag": s.imag,
                    label + "_db": 20 * np.log10(abs(s)),
                    label + "_phase_deg": np.angle(s, deg=True),
                }
            )
        for label, index in (("input", 0), ("output", 1)):
            gamma = abs(net.s[:, index, index])
            rows[label + "_return_loss_db"] = -20 * np.log10(gamma)
            rows[label + "_vswr"] = np.where(gamma <= 1, (1 + gamma) / (1 - gamma), np.nan)
    rows["gain_db"] = rows["s21_db"]
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class CompressionResult:
    gain_db: float
    input_p1db_dbm: float
    output_p1db_dbm: float
    baseline_slope: float
    fit_points: int


def compression(data, *, fit_points: int = 5) -> CompressionResult:
    """Mean low-level gain, then linear interpolation of the first 1dB loss crossing."""
    if not isinstance(fit_points, int) or fit_points < 3:
        raise ValueError("fit_points must be an integer >=3")
    df = _table(data, ["pin_dbm", "pout_dbm"], unique="pin_dbm", minimum=fit_points + 1)
    pin, pout = df.pin_dbm.to_numpy(), df.pout_dbm.to_numpy()
    slope = float(np.polyfit(pin[:fit_points], pout[:fit_points], 1)[0])
    if abs(slope - 1) > 0.1:
        raise ValueError("Low-power baseline slope must be near 1; choose a linear region")
    gain = float(np.mean(pout[:fit_points] - pin[:fit_points]))
    loss = gain - (pout - pin)
    if np.max(abs(loss[:fit_points])) > 0.2:
        raise ValueError("Baseline gain varies by >0.2 dB; choose lower-power fit points")
    crossing = np.flatnonzero((loss[:-1] < 1) & (loss[1:] >= 1))
    if not len(crossing):
        raise ValueError("Power sweep does not bracket 1 dB compression")
    i = int(crossing[0])
    if i < fit_points - 1:
        raise ValueError("Compression overlaps the baseline fit region")
    fraction = (1 - loss[i]) / (loss[i + 1] - loss[i])
    ip1 = float(pin[i] + fraction * (pin[i + 1] - pin[i]))
    return CompressionResult(gain, ip1, ip1 + gain - 1, slope, fit_points)


@dataclass(frozen=True)
class InterceptResult:
    iip3_dbm: float
    oip3_dbm: float
    fundamental_slope: float
    im3_slope: float
    fundamental_intercept_db: float
    im3_intercept_db: float
    points: int


def intercept(data, *, max_input_dbm: float | None = None) -> InterceptResult:
    """Intersect separate fundamental and IM3 fits in a selected weak-signal region.

    Input and output fundamental powers are per tone; IM3 is one product, not a sum.
    """
    df = _table(data, ["pin_dbm", "fundamental_dbm", "im3_dbm"], unique="pin_dbm", minimum=3)
    if max_input_dbm is not None:
        if not np.isfinite(max_input_dbm):
            raise ValueError("max_input_dbm must be finite")
        df = df[df.pin_dbm <= max_input_dbm]
    if len(df) < 3:
        raise ValueError("Need at least three two-tone points in the selected region")
    a, b = np.polyfit(df.pin_dbm, df.fundamental_dbm, 1)
    c, d = np.polyfit(df.pin_dbm, df.im3_dbm, 1)
    if abs(a - 1) > 0.15 or abs(c - 3) > 0.3:
        raise ValueError("Expected fundamental slope near 1 and IM3 slope near 3")
    if np.any(df.im3_dbm >= df.fundamental_dbm):
        raise ValueError("IM3 must be below the fundamental in the fit region")
    for measured, fitted in (
        (df.fundamental_dbm, a * df.pin_dbm + b),
        (df.im3_dbm, c * df.pin_dbm + d),
    ):
        if np.max(abs(measured - fitted)) > 0.5:
            raise ValueError("Two-tone fit residual exceeds 0.5 dB; select a linear region")
    ip3 = float((b - d) / (c - a))
    if ip3 <= df.pin_dbm.max():
        raise ValueError("Intercept must be above the measurement region")
    return InterceptResult(ip3, float(a * ip3 + b), float(a), float(c), float(b), float(d), len(df))


def noise_figure(data, *, reference_k: float = 290.0) -> pd.DataFrame:
    """Y-factor noise temperature and NF from calibrated hot/cold source temperatures."""
    df = _table(
        data, ["frequency_hz", "hot_dbm", "cold_dbm", "t_hot_k", "t_cold_k"], unique="frequency_hz"
    )
    if not np.isfinite(reference_k) or reference_k <= 0:
        raise ValueError("Reference temperature must be positive")
    if (
        np.any(df.frequency_hz <= 0)
        or np.any(df.t_cold_k <= 0)
        or np.any(df.t_hot_k <= df.t_cold_k)
    ):
        raise ValueError("Need positive frequencies and hot > cold > 0 K")
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        y = np.power(10.0, (df.hot_dbm - df.cold_dbm) / 10)
        te = (df.t_hot_k - y * df.t_cold_k) / (y - 1)
    if (
        np.any(y <= 1)
        or not np.isfinite(y).all()
        or not np.isfinite(te).all()
        or np.any(te < -1e-9)
    ):
        raise ValueError("Invalid Y-factor: need Y>1 and nonnegative noise temperature")
    # An ideal zero-noise device can round a fraction of a nanokelvin below zero.
    te = te.clip(lower=0)
    return df.assign(
        y_factor=y, noise_temperature_k=te, noise_figure_db=10 * np.log10(1 + te / reference_k)
    )
