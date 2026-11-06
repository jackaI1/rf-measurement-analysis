"""Real, uniformly sampled signals in volts; Hz axes and explicit scaling."""

import numpy as np
from scipy.signal import firwin, get_window, lfilter, periodogram


def _samples(samples, sample_rate_hz):
    raw = np.asarray(samples)
    if np.iscomplexobj(raw):
        raise ValueError("Expected real time-domain samples")
    x = np.asarray(raw, dtype=float)
    if x.ndim != 1 or x.size < 4 or not np.all(np.isfinite(x)):
        raise ValueError("Expected at least four finite real samples in one dimension")
    if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("Sample rate must be finite and positive")
    return x


def windowed_fft(samples, sample_rate_hz, window="hann"):
    """Return Hz and one-sided peak volts, corrected for window coherent gain.

    Exact amplitudes assume bin-centered, separated tones. No detrending or padding.
    DC and the even-length Nyquist bin are not doubled.
    """
    x = _samples(samples, sample_rate_hz)
    weights = get_window(window, len(x), fftbins=True)
    gain = weights.sum()
    if abs(gain) < np.finfo(float).eps:
        raise ValueError("Window has zero coherent gain")
    amplitude = np.abs(np.fft.rfft(x * weights)) / abs(gain)
    amplitude[1 : -1 if len(x) % 2 == 0 else None] *= 2
    return np.fft.rfftfreq(len(x), 1 / sample_rate_hz), amplitude


def power_spectral_density(samples, sample_rate_hz, window="hann"):
    """Return Hz and one-sided V²/Hz. Summing density times bin width gives V².

    Keep DC; normalize by window energy rather than FFT amplitude gain.
    """
    x = _samples(samples, sample_rate_hz)
    return periodogram(x, fs=sample_rate_hz, window=window, detrend=False, scaling="density")


def lowpass_fir(samples, sample_rate_hz, cutoff_hz, numtaps=129):
    """Return causal filtered samples and taps of an odd-length Hamming FIR.

    Delay is (numtaps-1)/2 samples. Discard the first numtaps-1 samples for
    steady-state measurements. cutoff_hz is the half-amplitude (-6 dB) frequency.
    """
    x = _samples(samples, sample_rate_hz)
    if not np.isfinite(cutoff_hz) or not 0 < cutoff_hz < sample_rate_hz / 2:
        raise ValueError("Cutoff must lie strictly between zero and Nyquist")
    if not isinstance(numtaps, int) or numtaps < 3 or numtaps % 2 == 0:
        raise ValueError("Use an odd integer numtaps >= 3")
    taps = firwin(numtaps, cutoff_hz, fs=sample_rate_hz, window="hamming")
    return lfilter(taps, [1.0], x), taps


def estimate_snr(samples, sample_rate_hz, tone_hz):
    """Fit a known-frequency sine, cosine and DC offset; return residual SNR in dB.

    Residual includes noise, other tones, distortion and frequency mismatch.
    This supports non-bin-centered tones; it does not search for an unknown tone.
    """
    x = _samples(samples, sample_rate_hz)
    if not np.isfinite(tone_hz) or not 0 < tone_hz < sample_rate_hz / 2:
        raise ValueError("Tone must lie strictly between zero and Nyquist")
    phase = 2 * np.pi * tone_hz * np.arange(len(x)) / sample_rate_hz
    basis = np.column_stack((np.sin(phase), np.cos(phase), np.ones(len(x))))
    coefficients, _, rank, _ = np.linalg.lstsq(basis, x, rcond=None)
    if rank < 3 or np.ptp(x) == 0:
        raise ValueError("Tone cannot be estimated from this record")
    fitted_tone = basis[:, :2] @ coefficients[:2]
    signal_power = np.mean(fitted_tone**2)
    noise_power = np.mean((x - basis @ coefficients) ** 2)
    if signal_power == 0:
        raise ValueError("No measurable tone")
    if noise_power <= np.finfo(float).eps * signal_power:
        return float("inf")
    return float(10 * np.log10(signal_power / noise_power))
