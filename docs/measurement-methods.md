# Measurement methods and limits

I keep the small-signal network, single-tone compression, two-tone distortion and hot/cold noise measurements separate because each needs a different experiment. A two-port Touchstone file alone cannot reveal large-signal compression, intercept points or noise figure.

## Input conventions

- Touchstone is loaded with `skrf.Network`. Two ports, positive real reference impedances, finite S-parameters and positive, strictly increasing frequencies are required. The reference impedances in the file are preserved; no automatic renormalization is applied.
- CSV headers carry the units. Rows may be unsorted; extraction sorts them. Duplicate input powers or noise frequencies, nonnumeric/missing/nonfinite measurements, and insufficient rows are rejected. Average repeated measurements before importing.
- Powers are referred to the DUT (device under test) connector planes. Correct input/output cable and attenuator losses first. S21 gain assumes matched source and load at the network's reference impedances. Mismatch corrections, stability, available gain and transducer gain for arbitrary terminations are outside this calculation.
- Noise powers must use the same effective noise bandwidth, gain, detector calibration and instrument settings. Supply source temperatures at the DUT input, after any input loss. Do not substitute ENR in dB for a temperature. This implementation does not perform a receiver second-stage correction.
- No uncertainty or confidence interval is inferred. Small differences between hot and cold powers can make the Y-factor result very sensitive to measurement error even when mathematically valid.

## S-parameters and matching

The complex S matrix uses output port as its first index and excited input port as its second. Touchstone two-port ordering is S11, S21, S12, S22; scikit-rf handles this ordering and RI/MA/DB encodings and frequency units. The exported CSV contains each element's real and imaginary parts, magnitude in dB and phase in degrees, plus return loss, VSWR and gain at every frequency.

For magnitude zero, logarithmic magnitude is negative infinity and return loss is positive infinity. A matched port has VSWR 1; full reflection has infinite VSWR. For |Γ| > 1, conventional passive-port VSWR is undefined and the CSV uses NaN rather than a negative ratio. If any frequency has undefined VSWR, its maximum summary is also undefined, with a count of affected points. Nonfinite summary numbers become JSON null; CSV retains these mathematical boundary values. Smith charts plot S11 and S22 using each port's normalized reference impedance.

## Single-tone compression

For the lowest N input powers (default N=5, minimum 3), I check output versus input slope is within 0.1 of 1 and each baseline gain is within 0.2 dB of the mean. I average `Pout - Pin` to obtain G. These checks help catch a baseline already in compression, but a user still needs to choose a genuinely flat region.

The gain loss at each sample is `G - (Pout - Pin)`. I find the first adjacent pair bracketing 1 dB and interpolate linearly in dB. Input P1dB is that interpolated input; output P1dB is `input P1dB + G - 1`. If the sweep never crosses, I raise an error rather than extrapolate. A coarse sweep or noisy early crossing can bias the result. The selected baseline must end before the crossing.

## Two-tone intercept

With equal, nearby input tones f1 and f2, measure a fundamental and one third-order intermodulation product at 2f1-f2 or 2f2-f1. `pin_dbm` is per tone, not combined input power. Do not sum two products or two fundamentals. Use consistent sides throughout a sweep and repeat the analysis for the other side if needed.

I fit `Pfund = a Pin + b` and `Pim3 = c Pin + d`, then calculate `IIP3 = (b-d)/(c-a)` and `OIP3 = a IIP3 + b`. I require at least three unique input powers, slopes within 0.15 of 1 and 0.3 of 3, respectively, maximum residuals of 0.5 dB, IM3 below the fundamental, and an intercept above the fitted inputs. These are explicit screening thresholds, not a guarantee of a calibrated measurement. `--ip3-max-input-dbm` selects the upper end of the fit. Discard noise-floor-limited rows before importing; do not merely fit their apparent floor. The plot shows all samples and extrapolates the selected fits to their intersection.

## Y-factor noise figure

Use calibrated hot and cold source temperatures Th and Tc, with output noise powers Ph and Pc. The gain and bandwidth cancel in the ratio:

```text
Y = Ph/Pc = 10^((Ph_dBm-Pc_dBm)/10)
Y = (Th + Te)/(Tc + Te)
Te = (Th - Y Tc)/(Y - 1)
F = 1 + Te/T0
NF_dB = 10 log10(F), T0 = 290 K by default
```

Th must exceed Tc, both must be positive, Y must exceed 1, and inferred Te must be finite and nonnegative. A result violating these assumptions is rejected. A negative Te within 1e-9 K of zero is clipped to zero to absorb floating-point roundoff for an ideal noiseless device. Extra receiver noise, source mismatch, gain drift and incorrect bandwidths can still bias physically plausible results.

## Synthetic amplifier derivation

I generate an ideal, memoryless voltage response `y = a1*x - a3*x^3`. Input sine amplitude A is normalized so `A²` is power in mW. Let `c = 3a3/(4a1)` and small-signal power gain be G=100 (20 dB).

For one tone, the fundamental amplitude is `a1*A*(1-c*A²)`, so:

```text
Pout_dBm = Pin_dBm + 20 + 20 log10(1 - c*10^(Pin_dBm/10))
c = (1 - 10^(-1/20)) / 10^(-5/10)
```

This puts the analytic input P1dB at -5 dBm and output P1dB at 14 dBm. The fixture stops at 0 dBm, before the cubic model becomes unphysical. Compression samples are 0.25 dB apart with the exact crossing included. The tiny finite-power baseline bias is covered by the 0.01 dB extraction tolerance.

For two equal tones, each fundamental amplitude is `a1*A*(1-3c*A²)` and each IM3 amplitude is `a1*c*A³`. The weak-signal asymptotes therefore meet at `IIP3 = 10 log10(1/c) = 4.635744808 dBm` and `OIP3 = 24.635744808 dBm`. The fixture uses -60 to -35 dBm per tone. At higher powers the fundamental's small compression biases an extrapolated fit even before the slope check fails. At the selected range, fit error stays below 0.03 dB. These synthetic products can be below a real instrument's noise floor: the generator has no detector floor, quantization or random measurement noise.

The small-signal network spans 0.8 to 1.2 GHz with 20 dB gain, |S11|=0.1, |S22|=0.05, |S12|=0.001 and a 1 ns phase delay. It uses 50 ohm reference impedances. These are behavioral coefficients for analysis checks, not a transistor simulation or a claim about a physically built circuit.

The independent thermal model sets `Te = 290*(10^(3/10)-1)` K and output noise `k*(Tsource+Te)*B*G`, with k=1.380649e-23 J/K, B=1 MHz, G=100, Th=870 K and Tc=290 K. This gives 3 dB noise figure by construction. `expected.json` stores analytic parameters, never fitted results. Tests also use hand-authored Touchstone, piecewise compression points, straight two-tone lines and a Y=2 temperature example.

## Output files

`report.md` links five PNGs: Smith chart, S-parameter magnitudes, compression, extrapolated intercept and noise figure. `summary.csv` is a flat summary; `summary.json` records source paths, settings and fit diagnostics. `s_parameters.csv` contains every frequency and all complex S-parameters; `noise_figure.csv` includes Y-factor and equivalent temperature. Reports can be regenerated into the same destination. New artifacts are generated in temporary storage first, so a plotting failure preserves an existing report; the temporary directory is removed on success or failure. Final file replacement is sequential, so a filesystem error during that last step can still leave a partial update. They refuse any destination that would replace one of the four supplied input files, and every plot is closed after saving, including on a save failure.
