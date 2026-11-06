"""Reproducible numerical checks using known tones, interference and white noise."""

import argparse
import json
from pathlib import Path

import numpy as np

from .dsp import estimate_snr, lowpass_fir, power_spectral_density, windowed_fft
from .sweep import save_json


def run_demo(output):
    sample_rate = 8192
    time_s = np.arange(32768) / sample_rate
    clean = 1.2 * np.sin(2 * np.pi * 256 * time_s)
    noise = np.random.default_rng(42).normal(0, 0.1, time_s.size)
    noisy = clean + noise
    mixed = noisy + 0.8 * np.sin(2 * np.pi * 2048 * time_s)
    filtered, taps = lowpass_fir(mixed, sample_rate, 700, 129)
    frequency, amplitude = windowed_fft(clean, sample_rate)
    _, density = power_spectral_density(clean, sample_rate)
    # Trim more than the 128-sample FIR transient, retaining whole tone cycles.
    settled = filtered[8192:]
    f_after, a_after = windowed_fft(settled, sample_rate)
    _, a_before = windowed_fft(mixed[8192:], sample_rate)
    interferer_bin = int(np.argmin(abs(f_after - 2048)))
    report = {
        "seed": 42,
        "sample_rate_hz": sample_rate,
        "sample_count": len(clean),
        "tone_peak_volts": 1.2,
        "noise_std_volts": 0.1,
        "fir_taps": len(taps),
        "fir_delay_samples": (len(taps) - 1) // 2,
        "fft_peak_hz": float(frequency[np.argmax(amplitude)]),
        "fft_peak_volts": float(max(amplitude)),
        "psd_integrated_v2": float(sum(density) * (frequency[1] - frequency[0])),
        "expected_noise_snr_db": float(10 * np.log10(0.72 / 0.01)),
        "noise_snr_db": estimate_snr(noisy, sample_rate, 256),
        "snr_before_db": estimate_snr(mixed[8192:], sample_rate, 256),
        "snr_after_db": estimate_snr(settled, sample_rate, 256),
        "interferer_rejection_db": float(
            20 * np.log10(a_before[interferer_bin] / a_after[interferer_bin])
        ),
    }
    if (
        abs(report["fft_peak_volts"] - 1.2) > 1e-8
        or abs(report["psd_integrated_v2"] - 0.72) > 1e-8
        or abs(report["noise_snr_db"] - report["expected_noise_snr_db"]) > 0.2
        or report["snr_after_db"] - report["snr_before_db"] < 15
        or report["interferer_rejection_db"] < 40
    ):
        raise RuntimeError("Synthetic signal validation failed")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    np.savetxt(
        output / "samples.csv",
        np.column_stack((time_s, clean, mixed, filtered)),
        delimiter=",",
        header="time_s,clean_v,mixed_v,filtered_v",
        comments="",
    )
    np.savetxt(
        output / "spectrum.csv",
        np.column_stack((frequency, amplitude, density)),
        delimiter=",",
        header="frequency_hz,peak_v,psd_v2_per_hz",
        comments="",
    )
    save_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("measurements/dsp-demo"))
    args = parser.parse_args()
    print(json.dumps(run_demo(args.output), indent=2))


if __name__ == "__main__":
    main()
