"""Command-line entry points for a model demo or real bench exports."""

import argparse
from pathlib import Path

from .channel_report import create_channel_report
from .report import create_report
from .synthetic import generate
from .synthetic_channel import generate_channel_inputs


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
    channel_demo = sub.add_parser(
        "channel-demo", help="Generate a channel with known features and analyze it"
    )
    channel_demo.add_argument("--output", type=Path, default=Path("reports/channel-demo"))
    channel = sub.add_parser("channel", help="Analyze a two- or four-port channel Touchstone")
    channel.add_argument("--touchstone", required=True, type=Path)
    channel.add_argument("--output", required=True, type=Path)
    channel.add_argument("--thru", type=Path, help="Fixture thru to remove from a two-port")
    channel.add_argument("--open", dest="open_path", type=Path, help="Open standard, two-port")
    channel.add_argument("--short", dest="short_path", type=Path, help="Short standard, two-port")
    channel.add_argument("--velocity-factor", type=float, default=0.66)
    channel.add_argument("--bitrate-gbps", type=float, default=10.0)
    channel.add_argument("--max-loss-db", type=float, help="Largest allowed insertion loss")
    channel.add_argument("--min-return-loss-db", type=float, help="Smallest allowed return loss")
    channel.add_argument("--max-crosstalk-db", type=float, help="Largest allowed crosstalk")
    channel.add_argument("--min-eye-height", type=float, help="Smallest allowed eye height")
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
        elif args.command == "channel-demo":
            output = args.output
            inputs = generate_channel_inputs(output / "inputs")
            limits = {"max_loss_db": 10.0, "min_return_loss_db": 8.0, "min_eye_height": 0.3}
            create_channel_report(
                inputs["fixture_lane.s2p"],
                output / "fixture-report",
                thru=inputs["thru.s2p"],
                open_path=inputs["open.s2p"],
                short_path=inputs["short.s2p"],
                label="Generated fixture-measured lane with thru, open and short",
                **limits,
            )
            create_channel_report(
                inputs["channel.s4p"],
                output / "channel-report",
                max_crosstalk_db=-30.0,
                label="Generated four-port channel with crosstalk",
                **limits,
            )
            output = output / "channel-report"
        elif args.command == "channel":
            output = args.output
            create_channel_report(
                args.touchstone,
                output,
                thru=args.thru,
                open_path=args.open_path,
                short_path=args.short_path,
                velocity_factor=args.velocity_factor,
                bitrate_gbps=args.bitrate_gbps,
                max_loss_db=args.max_loss_db,
                min_return_loss_db=args.min_return_loss_db,
                max_crosstalk_db=args.max_crosstalk_db,
                min_eye_height=args.min_eye_height,
                label=args.touchstone.name,
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
