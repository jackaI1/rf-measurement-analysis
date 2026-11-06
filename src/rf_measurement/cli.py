"""Command-line entry points for a model demo or real bench exports."""

import argparse
from pathlib import Path

from .report import create_report
from .synthetic import generate


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Analyze two-port RF measurements and write a report."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Generate synthetic amplifier measurements and analyze them")
    demo.add_argument("--output", type=Path, default=Path("reports/demo"))
    report = sub.add_parser("report", help="Analyze your Touchstone and CSV exports")
    for name in ("touchstone", "power", "two-tone", "noise"):
        report.add_argument("--" + name, required=True, type=Path)
    report.add_argument("--output", required=True, type=Path)
    report.add_argument("--p1-fit-points", type=int, default=5)
    report.add_argument("--ip3-max-input-dbm", type=float)
    report.add_argument("--noise-reference-k", type=float, default=290.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            inputs = args.output / "inputs"
            generate(inputs)
            output = args.output / "report"
            create_report(
                inputs / "amplifier.s2p",
                inputs / "power.csv",
                inputs / "two_tone.csv",
                inputs / "noise.csv",
                output,
                label="Synthetic cubic amplifier, not hardware measurements",
            )
        else:
            output = args.output
            create_report(
                args.touchstone,
                args.power,
                args.two_tone,
                args.noise,
                output,
                fit_points=args.p1_fit_points,
                max_input_dbm=args.ip3_max_input_dbm,
                reference_k=args.noise_reference_k,
            )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"Report written to {output / 'report.md'}")
