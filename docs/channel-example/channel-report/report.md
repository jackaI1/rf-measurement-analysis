# Channel report: Generated four-port channel with crosstalk

This report was generated from one Touchstone file. It is not a calibration certificate.

- Ports: 4
- Frequency range: 0.05 to 20 GHz
- Fixture removed with a thru: no
- Open/short reflection correction: no
- Velocity factor used for distance: 0.66

## Checks

| Check | Worst value | At (GHz) | Limit | Result |
| --- | ---: | ---: | ---: | --- |
| insertion_loss_db | 8.171 dB | 17.9 | 10 | pass |
| return_loss_db | 4.901 dB | 17.9 | 8 | fail |
| near_end_crosstalk_db | -30.05 dB | 0.05 | -30 | pass |
| far_end_crosstalk_db | -36.1 dB | 0.05 | -30 | pass |
| eye_height_fraction | 0.8396 fraction of swing |  | 0.3 | pass |

## Discontinuities

| One-way distance (cm) | Impedance (ohm) | Type |
| ---: | ---: | --- |
| 3.95 | 75.0 | high |
| 10.13 | 41.8 | low |

## Eye

At 10 Gb/s with PRBS-7, the best eye height is 0.840 of the full swing and the eye is open for 0.95 of a unit interval.

## Files

- `summary.json`: every setting, check and eye value.
- `checks.csv`: the same checks as a table.
- `discontinuities.csv`: the impedance features found in the TDR.
- `tdr.csv`: impedance against distance.
- `tdr.png`, `response.png`, `eye.png`: plots.
