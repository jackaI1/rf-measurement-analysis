# rf-measurement-analysis

I built a Python tool for checking radio-frequency measurements because I wanted to understand how the numbers on a network analyzer turn into amplifier gain, distortion and noise figures. It also checks cables, circuit-board traces and test fixtures: it shows where a signal is reflected or lost, and whether the path would still carry a fast digital signal. This is a learning project.

## Try it

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```bash
uv sync --locked --extra dev --python 3.11
uv run rf-analysis demo --output reports/first-run
uv run rf-analysis channel-demo --output reports/channel-demo
uv run rf-sweep --frequencies 1000000 2000000 3000000 --powers -20 -10
uv run rf-dsp-demo
```

Open `reports/first-run/report/report.md` in a Markdown viewer. It contains a summary table and five plots. The folder also contains CSV tables for a spreadsheet and JSON results for another script. Use a new output folder for another demo run; the generator refuses to overwrite existing input files.

The sweep command captures six combinations and prints a new folder under `measurements/`. The signal-analysis command checks known tones and saves its CSV files in `measurements/dsp-demo/`. The channel demo writes a fixture report and a channel report under `reports/channel-demo/`. Everything runs without physical instruments, drivers or accounts.

[See the included amplifier example report](docs/example-report/report.md) and [the included channel example reports](docs/channel-example/channel-report/report.md).

## Use my own exports

A Touchstone `.s2p` file holds the four small-signal S-parameters versus frequency. I use scikit-rf to read its frequency units, complex values, port order and reference impedance, and to draw the Smith chart. The [scikit-rf Network documentation](https://scikit-rf.readthedocs.io/en/latest/api/generated/skrf.network.Network.html) describes the underlying format and plotting API.

The three CSV files need these headers. Extra columns are allowed.

| File | Required columns | Meaning |
| --- | --- | --- |
| Power sweep | `pin_dbm,pout_dbm` | Single-tone input and output powers at the amplifier connectors. |
| Two-tone sweep | `pin_dbm,fundamental_dbm,im3_dbm` | Input power **per tone**, one output fundamental, and one third-order distortion product. Use equal input tones. |
| Noise sweep | `frequency_hz,hot_dbm,cold_dbm,t_hot_k,t_cold_k` | Hot/cold output noise powers measured over the same bandwidth, and calibrated source temperatures in kelvin. |

Powers use dBm, a logarithmic scale referenced to 1 milliwatt. [The example inputs](examples/synthetic/) show complete files.

```bash
uv run rf-analysis report \
  --touchstone measurements/amplifier.s2p \
  --power measurements/power.csv \
  --two-tone measurements/two_tone.csv \
  --noise measurements/noise.csv \
  --p1-fit-points 5 \
  --ip3-max-input-dbm -30 \
  --output reports/my-amplifier
```

I choose the first `--p1-fit-points` low-power points where gain is flat. For IIP3, `--ip3-max-input-dbm` limits the fit to the linear region; omitting it fits every row. The tool checks the fitted slopes and residuals, and refuses a power sweep that never reaches 1 dB compression. `summary.json` records input paths, settings and fit diagnostics. Reports replace generated files in their destination, so I keep raw measurements in a separate folder.

I correct cable losses and reference input/output powers to the amplifier connectors before importing. Two-tone data must resolve the distortion above the instrument noise floor. The noise calculation assumes negligible receiver noise or measurements already corrected for it. It does not remove fixture losses or perform instrument calibration. Power and two-tone sweeps represent one operating frequency each; I keep their frequency and bias conditions consistent with the network and noise data myself.

## Channel checks: reflections, loss, crosstalk and eye

I added channel checks because the same Touchstone file that shows loss against frequency can also show where along a trace a reflection happens, which is how engineers look at a circuit-board trace or a test fixture. I wanted to learn that view and build it with scikit-rf rather than a commercial package.

The command reads a two-port or four-port file and writes one folder with a report, tables and plots:

```bash
uv run rf-analysis channel \
  --touchstone measurements/trace.s2p \
  --thru measurements/fixture-thru.s2p \
  --open measurements/fixture-open.s2p \
  --short measurements/fixture-short.s2p \
  --velocity-factor 0.66 \
  --bitrate-gbps 10 \
  --max-loss-db 10 \
  --min-return-loss-db 8 \
  --output reports/trace
```

- **Time-domain reflectometry (TDR)** turns S11 into an impedance against distance, so a 75 ohm section or a capacitive spot shows up at its position along the trace. `--velocity-factor` converts the round-trip time to metres; the value depends on the board material.
- **Fixture removal** takes a thru (the fixture connected to itself) and removes the two fixture halves from the measurement. Open and short standards then correct the reflections at each port. I measured the error on generated files and put the numbers in [the method notes](docs/channel-analysis.md).
- **Limits** judge insertion loss (how much signal is lost), return loss (how much is reflected) and, on four-port files, crosstalk between two neighbouring lanes. Each check reports its worst frequency and whether it passes.
- **Eye diagram** sends a PRBS-7 test pattern (a long pseudo-random sequence of ones and zeros) through the channel and overlays the bits. A wide, open eye means a fast digital signal would be read reliably. The eye height is a fraction of the full swing, and the width is a fraction of one bit time.

The demo writes a generated channel with a 75 ohm section, a shunt capacitor and a neighbour lane, plus a fixture lane with its thru, open and short. In the demo report, the insertion loss check passes at 8.2 dB worst case, while return loss fails at 4.9 dB near 18 GHz. I put that capacitor in on purpose to create a clear reflection, so the failure shows the tool working as intended, not a fault. The eye is 0.84 of full swing at 10 Gb/s, and the TDR finds both features, at 3.95 cm and 10.1 cm.

[The channel example report](docs/channel-example/channel-report/report.md) shows the full output. The method, error table and limits are in [docs/channel-analysis.md](docs/channel-analysis.md).

## What I save

Every sweep gets a new UTC timestamped folder, even when I repeat the same settings. Each measurement has:

- A `.txt` file containing the analyzer's original text response, with only the communication terminator removed.
- A `.csv` file with frequency in hertz and power in dBm, a logarithmic unit relative to one milliwatt.
- A `.json` file with acquisition time, requested generator settings, settings read back from the generator, and the trace's strongest point.

The folder's `manifest.json` records instrument identities, sweep settings, timeout counts, completed measurements and whether the run finished or failed. If a later measurement fails, earlier files remain available. Output files are ignored by Git.

## How the sweep works

I use PyVISA, a Python interface for talking to lab instruments. The commands are SCPI, short text instructions such as `FREQ 1000000` and `POW -20`. The bundled pyvisa-sim definition provides a signal generator and spectrum analyzer that accept these commands and remember their settings.

For each combination, my script sets and checks the generator, enables its output, waits for any requested settling time, sets the analyzer's center frequency and span, triggers a capture, and waits for completion. It checks the returned trace length and numbers before saving. The generator output is switched off and both instrument sessions are closed when the run exits, including Python exceptions.

While waiting for the first response byte, a timeout gets two extra attempts by default. A timeout after any bytes have arrived stops the run so a partial response cannot be mistaken for a complete trace. I send the query once so a delayed response cannot leave duplicate replies waiting for the next command. Other connection errors stop the run immediately. I can change the timing with `--timeout-ms 1000 --retries 2 --settle-s 0.1`.

The simulator returns a fixed 21-point trace with a peak of -10 dBm at the center. Changing generator power does **not** change that trace: the two simulated instruments are not physically coupled. This checks command handling, readbacks, acquisition and file saving. It does not model RF gain, loss or instrument accuracy. Physical hardware has not been tested. [Instrument commands and hardware setup](docs/design.md) explain the backend options and what needs checking for a particular instrument.

## How the amplifier calculations work

For a reflection coefficient Γ, return loss is `-20 log10(|Γ|)` and VSWR is `(1 + |Γ|)/(1 - |Γ|)`. Γ is S11 at the input or S22 at the output. Matched-port gain is `20 log10(|S21|)`. This is not a general calculation of gain for arbitrary source and load impedances. All four S-parameters are exported as real/imaginary values, magnitude in dB and phase in degrees.

For P1dB, I average `Pout - Pin` over the low-power baseline, then interpolate the first point where the measured gain has fallen 1 dB. For IIP3, I fit output fundamental and distortion power against input power separately and find where the lines meet. In the ideal case, their slopes are 1 and 3 dB per dB. OIP3 is the output value at that intersection.

For noise figure, `Y = 10^((Phot - Pcold)/10)`, `Te = (Thot - Y Tcold)/(Y - 1)`, and `NF = 10 log10(1 + Te/290)`. Te is the equivalent noise temperature added by the amplifier. The 290 K reference can be changed with `--noise-reference-k`.

[Measurement assumptions, model derivation and error handling](docs/measurement-methods.md) explain the details and the limits of these estimates.

## Tests and limits

The test suite has 111 tests. They run on every push in GitHub Actions for Python 3.11, 3.12 and 3.13, along with a lint and formatting check. The CI step also runs both demos and uploads the reports as artifacts. The coverage gate is 90 percent, and the suite currently measures 96 percent.

Limits I know about:

- The sweep and channel analysis are checked against generated data and the simulator. Physical instruments and real boards have not been tested.
- Channel checks use fixed limits that I set in the command. They are not a standard, and I do not claim they match any particular interface specification.
- Fixture removal is exact for ideal lines and approximate for a fixture with an impedance step. The error numbers are in [the method notes](docs/channel-analysis.md).
- The eye uses an ideal transmitter. A real link will look worse.

## Where this copy lives

This repository was the original home of the project. I later moved the current working copy into my [lifeos-desk-node project](https://github.com/jackaI1/lifeos-desk-node/tree/main/projects/rf-measurement-analysis), and kept this one so older links still work.
