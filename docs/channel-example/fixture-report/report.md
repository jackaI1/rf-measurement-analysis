# Channel report: Generated fixture-measured lane with thru, open and short

This report was generated from one Touchstone file. It is not a calibration certificate.

- Ports: 2
- Frequency range: 0 to 20 GHz
- Fixture removed with a thru: yes
- Open/short reflection correction: yes
- Velocity factor used for distance: 0.66

## Checks

| Check | Worst value | At (GHz) | Limit | Result |
| --- | ---: | ---: | ---: | --- |
| insertion_loss_db | 8.08 dB | 17.9 | 10 | pass |
| return_loss_db | 4.783 dB | 17.9 | 8 | fail |
| eye_height_fraction | 0.8355 fraction of swing |  | 0.3 | pass |

## Discontinuities

| One-way distance (cm) | Impedance (ohm) | Type |
| ---: | ---: | --- |
| 3.71 | 76.0 | high |
| 10.13 | 42.2 | low |

## Eye

At 10 Gb/s with PRBS-7, the best eye height is 0.835 of the full swing and the eye is open for 0.95 of a unit interval.

## Files

- `summary.json`: every setting, check and eye value.
- `checks.csv`: the same checks as a table.
- `discontinuities.csv`: the impedance features found in the TDR.
- `tdr.csv`: impedance against distance.
- `tdr.png`, `response.png`, `eye.png`: plots.
