import numpy as np
import pytest

from rf_measurement.dsp import estimate_snr, lowpass_fir, power_spectral_density, windowed_fft


def test_fft_recovers_peak_amplitude_and_frequency():
    fs = 8192
    t = np.arange(fs) / fs
    f, amplitude = windowed_fft(1.7 * np.sin(2 * np.pi * 512 * t), fs)
    assert f[np.argmax(amplitude)] == 512
    assert amplitude[512] == pytest.approx(1.7, rel=1e-10)


@pytest.mark.parametrize("n", [1023, 1024])
def test_fft_dc_and_highest_bin_scaling(n):
    f, amplitude = windowed_fft(np.full(n, 3.0), n, window="boxcar")
    assert amplitude[0] == pytest.approx(3)
    k = n // 2
    _, amplitude = windowed_fft(np.cos(2 * np.pi * k * np.arange(n) / n), n, window="boxcar")
    assert amplitude[-1] == pytest.approx(1)
    assert f[-1] == k


def test_psd_integrates_to_tone_power():
    t = np.arange(8192) / 8192
    f, density = power_spectral_density(2 * np.sin(2 * np.pi * 512 * t), 8192)
    assert np.sum(density) * (f[1] - f[0]) == pytest.approx(2, rel=1e-10)


def test_psd_integrates_to_seeded_noise_variance():
    x = np.random.default_rng(42).normal(0, 0.2, 65536)
    f, density = power_spectral_density(x, 8192)
    assert np.sum(density) * (f[1] - f[0]) == pytest.approx(0.04, rel=0.025)


def test_fir_preserves_passband_and_rejects_interference():
    fs = 8192
    t = np.arange(16384) / fs
    x = np.sin(2 * np.pi * 256 * t) + np.sin(2 * np.pi * 2048 * t)
    filtered, taps = lowpass_fir(x, fs, cutoff_hz=700, numtaps=129)
    assert len(filtered) == len(x)
    assert len(taps) == 129
    # Discard start-up samples; retained record contains whole tone cycles.
    _, amplitude = windowed_fft(filtered[8192:], fs)
    assert amplitude[256] == pytest.approx(1, abs=0.01)
    assert amplitude[2048] < 0.001


def test_snr_matches_known_noise_including_off_bin_tone_and_dc():
    fs = 8192
    t = np.arange(65536) / fs
    rng = np.random.default_rng(7)
    x = 2 * np.sin(2 * np.pi * 517.3 * t + 0.6) + 4 + rng.normal(0, 0.1, len(t))
    # Signal power = 2 V²; noise = .01 V², so 10 log10(200) = 23.0103 dB.
    assert estimate_snr(x, fs, 517.3) == pytest.approx(23.0103, abs=0.12)


@pytest.mark.parametrize("function", [windowed_fft, power_spectral_density])
@pytest.mark.parametrize(
    "x,fs",
    [
        ([], 1),
        ([1], 1),
        ([1, 2], 0),
        ([1, 2], np.inf),
        ([1, np.nan], 1),
        ([[1, 2], [3, 4]], 1),
        ([1 + 1j, 2], 1),
    ],
)
def test_spectral_functions_reject_invalid_inputs(function, x, fs):
    with pytest.raises(ValueError):
        function(x, fs)


@pytest.mark.parametrize(
    "cutoff,taps", [(0, 129), (4096, 129), (700, 2), (700, 128), (float("nan"), 129)]
)
def test_filter_rejects_invalid_design(cutoff, taps):
    with pytest.raises(ValueError):
        lowpass_fir(np.ones(1000), 8192, cutoff, taps)


@pytest.mark.parametrize("frequency", [0, 4096, -1, float("nan")])
def test_snr_rejects_unidentifiable_tones(frequency):
    with pytest.raises(ValueError):
        estimate_snr(np.ones(100), 8192, frequency)


def test_snr_rejects_zero_signal():
    with pytest.raises(ValueError):
        estimate_snr(np.zeros(100), 8192, 512)
