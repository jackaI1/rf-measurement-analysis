# Acquisition and analysis notes

## Running the examples

I use these commands from the repository folder. Each command has a different job;
installing the project does not take any readings.

| Command | What it does | Where to look afterward |
| --- | --- | --- |
| `uv sync --locked --extra dev --python 3.12` | Installs the project and its test tools. | The local `.venv` environment. |
| `uv run rf-sweep --frequencies 1000000 2000000 3000000 --powers -20 -10` | Records six combinations from the simulated lab equipment. | The new timestamped folder printed under `measurements/`. |
| `uv run rf-dsp-demo` | Checks the calculations using generated tones and noise. | `measurements/dsp-demo/report.json` and the CSV files beside it. |
| `uv run rf-analysis demo --output reports/first-run` | Generates example amplifier measurements and a report. | `reports/first-run/report/report.md`. Use a new output folder for another run. |

## Instrument control

`Bench` owns one PyVISA resource manager and two message-based sessions. An exit stack
switches generator output off before closing resources, including when a trace is
invalid or saving fails. If the second connection cannot open, the first is closed.
The script is sequential. Do not share its sessions between threads or run overlapping
`Bench` contexts using the same backend. Output-off is a best-effort command over the
connection: a disconnected instrument or killed process cannot guarantee shutdown.

The bundled YAML follows the [pyvisa-sim definition format](https://pyvisa.readthedocs.io/projects/pyvisa-sim/en/latest/definitions.html).
It provides stateful frequency, power and output properties for the generator;
stateful center/span properties and a fixed ASCII trace for the analyzer. The trace
fixture is deliberately independent of generator state. Its frequency axis uses the
analyzer's center/span readbacks and reported point count, assuming a linear sweep.
It is a protocol and persistence fixture, not a circuit or propagation model.

| Device | Commands used |
| --- | --- |
| Generator | `*IDN?`, `FREQ`, `FREQ?`, `POW`, `POW?`, `OUTP` |
| Analyzer | `*IDN?`, `FREQ:CENT`, `FREQ:CENT?`, `FREQ:SPAN`, `FREQ:SPAN?`, `INIT:CONT OFF`, `INIT:IMM`, `*OPC?`, `SWE:POIN?`, `TRAC:DATA? TRACE1` |

The generator range is 1 Hz to 6 GHz and -120 to +20 dBm. Analyzer span is 1 Hz to
1 GHz; sweep validation prevents an axis starting below zero. These are driver/demo
limits, not a claim about a physical model. Each setting is sent as a decimal number
with six fractional digits. The script rejects generator readbacks that differ from
requested settings, malformed/nonfinite traces and mismatched point counts.

### Timeouts and failure records

Queries write once, then allow `retries + 1` attempts to read the first byte, with
50 ms between timeout retries. This driver uses newline-terminated ASCII replies.
After the first byte arrives, the rest of the response is read once. Any timeout
in that remainder aborts the run: PyVISA can discard already-consumed bytes when a
read fails, and a remaining suffix can still look like a valid numeric trace.
Never retry that suffix. A first-byte newline is treated as an empty reply, which
trace parsing rejects. Only VISA timeout status is retried before the first byte;
failed writes and other errors propagate immediately.

This handles late responses without issuing duplicate acquisitions, but does not
replay a lost command. Recovery from connection loss or an interrupted response
requires a new run and, on hardware, clearing any stale device buffer.

Metadata is replaced atomically on the same filesystem. A point enters the manifest
only after its text, CSV and metadata files have been written. Completed points remain
on error. A killed process can leave a `running` manifest, temporary metadata file or
an unlisted incomplete point; this is not a transactional database or a guarantee
against power loss. Each run uses UTC microseconds plus a random suffix to prevent
repeated runs from overwriting data.

### Hardware setup

The default is always the bundled simulation. To use hardware:

1. Install the instrument vendor's VISA runtime, find the resource addresses with
   `python -c 'import pyvisa; print(pyvisa.ResourceManager("@ni").list_resources())'`.
   For a pure Python network backend, install `pyvisa-py` and use `@py` instead.
2. Check the instrument manuals against the command table. Configure the analyzer
   for a linear frequency axis, ASCII trace output and dBm units. Models using binary
   blocks, different command names or log sweeps need a driver adaptation first.
3. Connect the bench with attenuation and input limits appropriate to the instruments.
   Use their real addresses in place of the placeholders below.

```sh
rf-sweep --backend @ni \
  --generator 'TCPIP0::GENERATOR_IP::inst0::INSTR' \
  --analyzer 'TCPIP0::ANALYZER_IP::inst0::INSTR' \
  --frequencies 1000000 2000000 --powers -30 \
  --span 20000 --settle-s 0.1 --timeout-ms 5000 --output measurements
```

No vendor-specific hardware compatibility is claimed. No physical bench was available
for this implementation. Instrument addresses and raw captures can identify a private
bench, so output folders are ignored and should be reviewed before sharing.

## Signal processing

The DSP functions accept finite, real, one-dimensional arrays with at least four
samples, and a positive sample rate. Voltage samples must be uniformly spaced.
Spectrum analyzer dBm traces have already been transformed into the frequency domain;
feeding them into these FFT functions would not give a meaningful voltage spectrum.

- `windowed_fft(x, fs, window='hann')`: periodic Hann window, real FFT and division by
  the sum of window coefficients (coherent gain). Positive-frequency bins are doubled,
  except Nyquist for even record lengths. DC is never doubled. Units are peak V.
  Exact amplitude recovery assumes bin-centered, separated tones. An off-bin tone
  spreads across bins and its largest bin underestimates amplitude. No zero padding
  or DC subtraction is applied.
- `power_spectral_density(x, fs)`: SciPy's one-sided periodogram with a Hann window,
  no detrending and density normalization. Units are V²/Hz, not watts or dBm.
  Sum times bin width estimates mean-square voltage. The window weights individual
  samples, so noise power matches its theoretical variance within statistical error.
- `lowpass_fir(x, fs, cutoff_hz, numtaps=129)`: Hamming-window `firwin` coefficients
  and causal `lfilter`. Odd tap count gives integer group delay `(numtaps-1)/2`.
  The cutoff is the -6 dB amplitude point. Discard at least `numtaps-1` leading
  samples before measuring steady state; the demo trims a longer whole-cycle block.
  The returned coefficient array makes the filter design inspectable.
- `estimate_snr(x, fs, tone_hz)`: least-squares fit of sine, cosine and a DC term.
  The ratio is `10*log10(mean(fitted_tone²)/mean(residual²))`. It supports known
  off-bin frequencies and unknown phase. Interference, distortion and frequency
  mismatch count as residual, so the demo's before/after metric includes interference.
  It is not a blind frequency detector. Near-zero residuals return infinity; an
  unidentifiable fit or constant record raises `ValueError`.

I use [SciPy's signal-processing functions](https://docs.scipy.org/doc/scipy/reference/signal.html)
for filter design and periodograms. The FFT scaling is explicit in my module so the
amplitude units and DC/Nyquist handling can be checked independently.

## Validation

The tests use known amplitudes, analytical tone power, seeded Gaussian noise and a
known off-bin tone. Filter tests discard startup transients and check passband
amplitude and stopband suppression separately. Instrument tests run real pyvisa-sim
sessions; fault injection only delays reads or interrupts an acquisition to exercise
cleanup and partial-file retention. A genuinely unsupported query exercises the
simulator's VISA timeout path. No physical instruments, network servers or accounts
are needed for the tests.
