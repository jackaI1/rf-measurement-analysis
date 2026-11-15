# Channel analysis: method notes

This page records how the channel tools in `src/rf_measurement/channel.py` work, the
choices behind them, and the measured error on the generated test files. The plain
explanation is in the main README.

## Commands

| Command | What it does |
| --- | --- |
| `rf-analysis channel-demo --output reports/channel-demo` | Generates a channel, a fixture lane and standards, then analyzes both. |
| `rf-analysis channel --touchstone FILE --output DIR` | Analyzes one two-port or four-port file. |
| `... --thru THRU.s2p --open OPEN.s2p --short SHORT.s2p` | Removes a fixture first (two-port files only). |
| `... --velocity-factor 0.66` | Converts round-trip time to one-way distance. |
| `... --max-loss-db 10 --min-return-loss-db 8 --max-crosstalk-db -30` | Sets the limits that are judged. |
| `... --bitrate-gbps 10 --min-eye-height 0.3` | Sets the PRBS-7 eye rate and its limit. |

Limits left out are reported but not judged.

## Time-domain reflectometry (TDR)

The reflection step is scikit-rf's `Network.step_response` applied to S11, using a Hamming
window. For a reflection coefficient ρ(t) the impedance is `Z = Z0 (1 + ρ) / (1 - ρ)`. The
one-way distance is `d = t * v * c / 2`, where `v` is the velocity factor. A value of 0.66 is
a typical PCB trace; the TDR cannot know it, so it must match the material.

Before the transform the network is placed on a uniform grid starting at 0 Hz. Two
details matter here:

- **DC point.** The 0 Hz value is taken as the magnitude of the first measured point with zero
  phase. A linear extrapolation of the complex value was tried first and gave |S21(0)| of
  about 1.2 for a delayed line, which inflated every step.
- **Resolution.** A 50 MHz to 20 GHz sweep gives a time step of about 25 ps and a window of
  20 ns. Features a few centimetres apart can merge into one result.

`find_discontinuities` finds local peaks and dips of |Z - Z0| larger than 5 ohm, merging peaks
closer than 5 mm. A plateau such as a 75 ohm section gives one result at its highest point.
In the generated demo the 75 ohm section (true middle at 4.0 cm) is reported at 3.95 cm, and
the shunt capacitor (true position 10.0 cm) at 10.13 cm.

## Fixture de-embedding

The thru standard is the fixture connected to itself. The IEEE P370 2x-thru method in
scikit-rf (`IEEEP370_SE_NZC_2xThru`) splits it into two halves, then removes both halves from
the measured DUT.

Open and short standards are passed through the same removal. Their port reflections
should be +1 and -1. The model `measured = gain * true + offset` gives
`gain = (open - short) / 2` and `offset = (open + short) / 2`, and each port's S11 and S22
are corrected with it (`correct_reflection`). Frequencies where the gain is below 0.5 keep
their uncorrected value. This happens at 0 Hz, where the P370 short result is degenerate.

### Measured error on the generated files

Each case compares the de-embedded result with the DUT used to generate the measurement.
Values are maximum absolute error over 1 to 20 GHz (0 Hz is excluded).

| Fixture | Correction | max S11 error | max S21 error | max S22 error |
| --- | --- | ---: | ---: | ---: |
| Ideal 50 ohm line | thru only | 0.0000 | 0.0000 | 0.0000 |
| Ideal 50 ohm line | thru + open/short | 0.0000 | 0.0000 | 0.0000 |
| 50 to 60 ohm step inside the fixture | thru only | 0.080 | 0.031 | 0.141 |
| 50 to 60 ohm step inside the fixture | thru + open/short | 0.110 | 0.031 | 0.110 |
| Demo fixture, 50 to 55 ohm step (`fixture_lane.s2p`) | thru only | 0.040 | 0.016 | 0.060 |
| Demo fixture, 50 to 55 ohm step (`fixture_lane.s2p`) | thru + open/short | 0.049 | 0.016 | 0.048 |

The P370 split is exact for ideal lines. For a fixture with an impedance step it is
approximate, as scikit-rf's documentation says. In the 60 ohm test the two-term open/short
correction helps S22 and makes S11 worse. It only removes errors that look like a gain and
an offset, so it does not fix the P370 residual in general. In the demo the two corrections
are roughly equal. Check a fixture-corrected reflection against a known DUT before trusting
it.

## Checks

- **Insertion loss**: `-20 log10 |S21|`. The judged value is the largest loss over the band.
- **Return loss**: `-20 log10 |S11|` and `-20 log10 |S22|`. The judged value is the smallest.
- **Crosstalk** (four-port files only): ports 1 and 2 are one lane, ports 3 and 4 the
  neighbour. Near-end crosstalk is `|S31|` and far-end is `|S41|`. The judged value is the
  largest.

## Eye diagram

The input is an ideal NRZ PRBS-7 pattern (127-bit period, polynomial x^7 + x^6 + 1). The
channel's S21 is converted to a step response on a time grid made finer by zero-padding the
spectrum eight times. The output at any time is then the sum of the step response over the
symbols, so the eye is not limited to the sample spacing.

Each phase within the unit interval is judged separately. The main cursor is the bit lag
that carries the largest response at that phase, and a delay that is not a whole number of
unit intervals moves it. The eye height is the lowest '1' minus the highest '0' at that
phase, divided by two for the full swing. The eye width is the fraction of the unit
interval where the height is above 1 percent of the swing.

Checked behaviour:

- An ideal line gives a height of 0.95 or more at 5 Gb/s regardless of its length. Earlier
  versions made the result depend on delay, which was a look-back bug.
- A 0.5 m line with 30 Np/m loss closes the eye: height below 0.05.
- With the generated 20 GHz band, the eye is 0.82 at 25 Gb/s. This is the band limit, not
  a fault.

The transmitter is ideal (no rise time, no jitter, no equalization). An eye from this tool is
the channel's effect alone, and a real link will be worse.

## What the tools do not do

- They do not calibrate a VNA. Use the instrument's own SOLT or TRL calibration first.
- They do not extend a sweep. Time resolution and window length come from the frequency
  range in the file, and nothing below the first measured frequency (other than the DC
  extrapolation described above) is known.
- Crosstalk is not de-embedded. Fixture removal applies to two-port files only.
- The demo channel and fixture are generated, so the numbers show that the code works, not
  how a particular board behaves. The return-loss failure in the demo comes from the 0.3 pF
  via capacitance added to make a discontinuity.

## Tests

`tests/test_channel.py` checks the grid, TDR feature locations and velocity scaling,
discontinuity detection, exact fixture removal for ideal lines, the reflection correction,
the limit checks, PRBS-7 properties, eye behaviour, report writing, and the CLI.
