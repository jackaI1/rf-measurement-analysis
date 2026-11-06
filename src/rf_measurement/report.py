"""Headless report generation from exactly the same input files used at a bench."""

import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib

matplotlib.use("Agg")
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt

from .analysis import compression, intercept, load_network, network_metrics, noise_figure

ARTIFACTS = (
    "summary.json",
    "summary.csv",
    "s_parameters.csv",
    "noise_figure.csv",
    "report.md",
    "smith.png",
    "s_parameters.png",
    "compression.png",
    "intercept.png",
    "noise_figure.png",
)


def create_report(
    touchstone,
    power,
    two_tone,
    noise,
    output,
    *,
    fit_points=5,
    max_input_dbm=None,
    reference_k=290.0,
    label="User-supplied measurement data",
) -> dict:
    """Generate in scratch space before replacing reports; preserve every input file."""
    output = Path(output)
    inputs = {"touchstone": touchstone, "power": power, "two_tone": two_tone, "noise": noise}
    destinations = {(output / name).resolve() for name in ARTIFACTS}
    if any(Path(path).resolve() in destinations for path in inputs.values()):
        raise ValueError("Report output would overwrite an input; choose a separate folder")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".rf-report-", dir=output.parent) as scratch:
        summary = _write_report(
            touchstone,
            power,
            two_tone,
            noise,
            scratch,
            fit_points=fit_points,
            max_input_dbm=max_input_dbm,
            reference_k=reference_k,
            label=label,
        )
        output.mkdir(parents=True, exist_ok=True)
        for name in ARTIFACTS:
            (Path(scratch) / name).replace(output / name)
    return summary


def _write_report(
    touchstone,
    power,
    two_tone,
    noise,
    output,
    *,
    fit_points=5,
    max_input_dbm=None,
    reference_k=290.0,
    label="User-supplied measurement data",
) -> dict:
    output = Path(output)
    inputs = {"touchstone": touchstone, "power": power, "two_tone": two_tone, "noise": noise}
    net = load_network(touchstone)
    metrics = network_metrics(net)
    p1 = compression(power, fit_points=fit_points)
    ip3 = intercept(two_tone, max_input_dbm=max_input_dbm)
    nf = noise_figure(noise, reference_k=reference_k)
    power_df = pd.read_csv(power).sort_values("pin_dbm")
    tone_df = pd.read_csv(two_tone).sort_values("pin_dbm")
    summary = {
        "label": label,
        "inputs": {key: str(value) for key, value in inputs.items()},
        "settings": {
            "p1_fit_points": fit_points,
            "ip3_max_input_dbm": max_input_dbm,
            "noise_reference_k": reference_k,
        },
        "network": {
            "frequency_min_hz": float(net.f.min()),
            "frequency_max_hz": float(net.f.max()),
            "points": len(net.f),
            "gain_min_db": float(metrics.gain_db.min()),
            "gain_max_db": float(metrics.gain_db.max()),
            "input_return_loss_min_db": float(metrics.input_return_loss_db.min()),
            "output_return_loss_min_db": float(metrics.output_return_loss_db.min()),
            "input_vswr_max": (
                float("nan") if metrics.input_vswr.isna().any() else float(metrics.input_vswr.max())
            ),
            "input_vswr_undefined_points": int(metrics.input_vswr.isna().sum()),
            "output_vswr_max": (
                float("nan")
                if metrics.output_vswr.isna().any()
                else float(metrics.output_vswr.max())
            ),
            "output_vswr_undefined_points": int(metrics.output_vswr.isna().sum()),
        },
        "compression": asdict(p1),
        "intercept": asdict(ip3),
        "noise": {
            "noise_figure_min_db": float(nf.noise_figure_db.min()),
            "noise_figure_max_db": float(nf.noise_figure_db.max()),
        },
        "notes": [
            "Gain is |S21| in dB with matched reference ports.",
            "VSWR is undefined where reflection magnitude exceeds 1; its maximum is then null.",
            "Two-tone input is per tone; intercept is an extrapolation.",
            "Noise figure assumes calibrated source temperatures and negligible receiver noise.",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)

    # Keep JSON standards compliant even for an ideal network with zero transmission.
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, float) and not np.isfinite(value):
            return None
        return value

    summary = clean(summary)
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    table = []
    for section in ("network", "compression", "intercept", "noise"):
        for metric, value in summary[section].items():
            table.append({"section": section, "metric": metric, "value": value})
    pd.DataFrame(table).to_csv(output / "summary.csv", index=False)
    metrics.to_csv(output / "s_parameters.csv", index=False)
    nf.to_csv(output / "noise_figure.csv", index=False)

    def save(name, draw):
        fig, ax = plt.subplots(figsize=(7, 4.5), layout="constrained")
        try:
            draw(ax)
            fig.savefig(output / f"{name}.png", dpi=140)
        finally:
            plt.close(fig)

    def smith(ax):
        net.plot_s_smith(m=0, n=0, ax=ax, label="Input S11")
        net.plot_s_smith(m=1, n=1, ax=ax, label="Output S22")
        ax.set_title("Port reflections on a Smith chart")

    save("smith", smith)

    def s_plot(ax):
        for parameter in ("s11", "s21", "s12", "s22"):
            ax.plot(net.f / 1e9, metrics[parameter + "_db"], label=parameter.upper())
        ax.set(xlabel="Frequency (GHz)", ylabel="Magnitude (dB)", title="Two-port S-parameters")
        ax.grid(alpha=0.25)
        ax.legend()

    save("s_parameters", s_plot)

    def compression_plot(ax):
        ax.plot(power_df.pin_dbm, power_df.pout_dbm, label="Measured output")
        ax.plot(power_df.pin_dbm, power_df.pin_dbm + p1.gain_db, "--", label="Small-signal line")
        ax.scatter([p1.input_p1db_dbm], [p1.output_p1db_dbm], color="red", label="1 dB compression")
        ax.set(xlabel="Input (dBm)", ylabel="Output (dBm)", title="Single-tone compression")
        ax.grid(alpha=0.25)
        ax.legend()

    save("compression", compression_plot)

    def intercept_plot(ax):
        ax.scatter(tone_df.pin_dbm, tone_df.fundamental_dbm, label="Fundamental samples")
        ax.scatter(tone_df.pin_dbm, tone_df.im3_dbm, label="IM3 samples")
        x = np.array([tone_df.pin_dbm.min(), ip3.iip3_dbm])
        ax.plot(
            x,
            ip3.fundamental_slope * x + ip3.fundamental_intercept_db,
            "--",
            label="Fundamental fit",
        )
        ax.plot(x, ip3.im3_slope * x + ip3.im3_intercept_db, "--", label="IM3 fit")
        ax.scatter([ip3.iip3_dbm], [ip3.oip3_dbm], color="red", label="Extrapolated IP3")
        ax.set(
            xlabel="Input per tone (dBm)",
            ylabel="Output per tone/product (dBm)",
            title="Two-tone intercept (extrapolated)",
        )
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)

    save("intercept", intercept_plot)

    def noise_plot(ax):
        ax.plot(nf.frequency_hz / 1e9, nf.noise_figure_db)
        ax.set(xlabel="Frequency (GHz)", ylabel="Noise figure (dB)", title="Y-factor noise figure")
        ax.grid(alpha=0.25)

    save("noise_figure", noise_plot)

    def vswr_text(port):
        if summary["network"][port + "_vswr_undefined_points"]:
            return "undefined (reflection magnitude exceeds 1)"
        return f"{metrics[port + '_vswr'].max():.4f}"

    lines = [
        "# RF measurement report",
        "",
        label,
        "",
        "Results use the connector-plane powers supplied in the input files.",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Small-signal gain (dB) | {p1.gain_db:.4f} |",
        f"| Minimum input return loss (dB) | {metrics.input_return_loss_db.min():.4f} |",
        f"| Minimum output return loss (dB) | {metrics.output_return_loss_db.min():.4f} |",
        f"| Maximum input VSWR | {vswr_text('input')} |",
        f"| Maximum output VSWR | {vswr_text('output')} |",
        f"| Input P1dB (dBm) | {p1.input_p1db_dbm:.4f} |",
        f"| Output P1dB (dBm) | {p1.output_p1db_dbm:.4f} |",
        f"| IIP3 (dBm per tone) | {ip3.iip3_dbm:.4f} |",
        f"| OIP3 (dBm per tone) | {ip3.oip3_dbm:.4f} |",
        f"| Noise figure range (dB) | {nf.noise_figure_db.min():.4f} to {nf.noise_figure_db.max():.4f} |",
        "",
        "## Assumptions",
        "",
        *["- " + note for note in summary["notes"]],
        "",
        "See summary.json for input paths, fit settings, and fitted slopes.",
        "",
        *[
            f"![{name.replace('_', ' ')}]({name}.png)\n"
            for name in ("smith", "s_parameters", "compression", "intercept", "noise_figure")
        ],
    ]
    (output / "report.md").write_text("\n".join(lines).rstrip() + "\n")
    return summary
