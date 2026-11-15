"""Write a channel report: TDR, limit checks, eye and plots from one Touchstone file."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib

matplotlib.use("Agg")
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt

from .channel import (
    channel_checks,
    deembed_fixture,
    eye_diagram,
    find_discontinuities,
    load_channel,
    tdr,
)

ARTIFACTS = (
    "summary.json",
    "checks.csv",
    "discontinuities.csv",
    "tdr.csv",
    "report.md",
    "tdr.png",
    "eye.png",
    "response.png",
)


def create_channel_report(
    touchstone,
    output,
    *,
    thru=None,
    open_path=None,
    short_path=None,
    velocity_factor=0.66,
    bitrate_gbps=10.0,
    max_loss_db=None,
    min_return_loss_db=None,
    max_crosstalk_db=None,
    min_eye_height=None,
    label="User-supplied channel data",
) -> dict:
    """Analyze one channel file and replace the report files in output.

    Input files are never written. Results go to a scratch folder first, so a failed run
    leaves an existing report intact.
    """
    output = Path(output)
    inputs = {"touchstone": touchstone, "thru": thru, "open": open_path, "short": short_path}
    destinations = {(output / name).resolve() for name in ARTIFACTS}
    if any(Path(p).resolve() in destinations for p in inputs.values() if p is not None):
        raise ValueError("Report output would overwrite an input; choose a separate folder")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".rf-channel-", dir=output.parent) as scratch:
        summary = _write_channel_report(
            touchstone,
            scratch,
            thru=thru,
            open_path=open_path,
            short_path=short_path,
            velocity_factor=velocity_factor,
            bitrate_gbps=bitrate_gbps,
            max_loss_db=max_loss_db,
            min_return_loss_db=min_return_loss_db,
            max_crosstalk_db=max_crosstalk_db,
            min_eye_height=min_eye_height,
            label=label,
        )
        output.mkdir(parents=True, exist_ok=True)
        for name in ARTIFACTS:
            (Path(scratch) / name).replace(output / name)
    return summary


def _write_channel_report(
    touchstone,
    scratch,
    *,
    thru,
    open_path,
    short_path,
    velocity_factor,
    bitrate_gbps,
    max_loss_db,
    min_return_loss_db,
    max_crosstalk_db,
    min_eye_height,
    label,
) -> dict:
    scratch = Path(scratch)
    net = load_channel(touchstone)
    fixture_used = thru is not None
    if fixture_used:
        if (open_path is None) != (short_path is None):
            raise ValueError("Give both open and short standards, or neither")
        standards = {}
        if open_path is not None:
            standards = {"open": load_channel(open_path), "short": load_channel(short_path)}
        thru_net = load_channel(thru)
        net = deembed_fixture(net, thru_net, **standards)
    profile = tdr(net, velocity_factor=velocity_factor)
    z0 = float(np.real(net.z0[0, 0]))
    discontinuities = find_discontinuities(profile, z0)
    checks = channel_checks(
        net,
        max_loss_db=max_loss_db,
        min_return_loss_db=min_return_loss_db,
        max_crosstalk_db=max_crosstalk_db,
    )
    two_port = net if net.nports == 2 else net.subnetwork([0, 1])
    eye = eye_diagram(two_port, bitrate_bps=bitrate_gbps * 1e9)
    eye_row = {
        "check": "eye_height_fraction",
        "worst_value": eye.eye_height_fraction,
        "worst_frequency_ghz": float("nan"),
        "limit": min_eye_height,
        "unit": "fraction of swing",
        "passed": None
        if min_eye_height is None
        else bool(eye.eye_height_fraction >= min_eye_height),
    }
    checks = pd.concat([checks, pd.DataFrame([eye_row])], ignore_index=True)

    checks.to_csv(scratch / "checks.csv", index=False)
    discontinuities.to_csv(scratch / "discontinuities.csv", index=False)
    profile.to_csv(scratch / "tdr.csv", index=False, float_format="%.9g")
    _plot_tdr(profile, z0, discontinuities, scratch / "tdr.png")
    _plot_eye(eye, scratch / "eye.png")
    _plot_response(net, checks, scratch / "response.png")

    judged = checks.dropna(subset=["passed"])
    summary = {
        "label": label,
        "touchstone": Path(touchstone).name,
        "ports": int(net.nports),
        "fixture_deembedded": fixture_used,
        "open_short_correction": open_path is not None,
        "frequency_start_ghz": float(net.f[0] / 1e9),
        "frequency_stop_ghz": float(net.f[-1] / 1e9),
        "velocity_factor": velocity_factor,
        "reference_impedance_ohm": z0,
        "discontinuities": discontinuities.to_dict(orient="records"),
        "checks": checks.to_dict(orient="records"),
        "all_judged_checks_passed": bool(judged["passed"].all()) if len(judged) else None,
        "eye": eye.metrics(),
    }
    (scratch / "summary.json").write_text(json.dumps(summary, indent=2, default=_json_value))
    (scratch / "report.md").write_text(_markdown(summary, checks, discontinuities))
    return summary


def _json_value(value):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    raise TypeError(f"Not JSON serializable: {value!r}")


def _plot_tdr(profile, z0, discontinuities, path):
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(profile["distance_m"] * 100, profile["impedance_ohm"], color="#1f4e79")
    ax.axhline(z0, color="0.5", linestyle="--", linewidth=1, label=f"{z0:g} ohm reference")
    for _, row in discontinuities.iterrows():
        ax.plot(row["distance_m"] * 100, row["impedance_ohm"], "o", color="#b35900")
    ax.set_xlabel("One-way distance (cm)")
    ax.set_ylabel("Impedance (ohm)")
    ax.set_title("Impedance versus distance (TDR)")
    ax.set_xlim(0, min(float(profile["distance_m"].max()), 1.0) * 100)
    ax.legend(loc="best")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_eye(eye, path):
    fig, ax = plt.subplots(figsize=(6, 4))
    # Overlay the most recent bits so the plot stays light and the eye reads clearly.
    for k in range(max(0, eye.traces.shape[1] - 120), eye.traces.shape[1]):
        ax.plot(eye.phase_ui, eye.traces[:, k], color="#1f4e79", alpha=0.12, linewidth=0.8)
    ax.axvline(eye.best_phase_ui, color="#b35900", linewidth=1, linestyle="--")
    ax.set_xlabel("Time within one unit interval (UI)")
    ax.set_ylabel("Normalized output")
    ax.set_title(f"PRBS-7 eye at {eye.bitrate_bps / 1e9:g} Gb/s")
    ax.set_xlim(0, 1)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_response(net, checks, path):
    f = net.f / 1e9
    fig, ax = plt.subplots(figsize=(7, 4))
    with np.errstate(divide="ignore"):
        ax.plot(f, 20 * np.log10(np.abs(net.s[:, 1, 0])), label="S21 insertion (dB)")
        ax.plot(f, 20 * np.log10(np.abs(net.s[:, 0, 0])), label="S11 reflection (dB)")
        if net.nports >= 4:
            ax.plot(f, 20 * np.log10(np.abs(net.s[:, 2, 0])), label="S31 near-end xtalk (dB)")
            ax.plot(f, 20 * np.log10(np.abs(net.s[:, 3, 0])), label="S41 far-end xtalk (dB)")
    for _, row in checks.iterrows():
        if row["check"] == "insertion_loss_db" and pd.notna(row["limit"]):
            ax.axhline(-row["limit"], color="#1f4e79", linestyle=":", linewidth=1)
        if row["check"] == "return_loss_db" and pd.notna(row["limit"]):
            ax.axhline(-row["limit"], color="#b35900", linestyle=":", linewidth=1)
        if row["check"].endswith("crosstalk_db") and pd.notna(row["limit"]):
            ax.axhline(row["limit"], color="#6b2d8f", linestyle=":", linewidth=1)
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("Magnitude (dB)")
    ax.set_title("Insertion loss, return loss and crosstalk (dotted lines are limits)")
    ax.legend(fontsize=8, loc="lower left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _markdown(summary, checks, discontinuities):
    lines = [
        f"# Channel report: {summary['label']}",
        "",
        "This report was generated from one Touchstone file. It is not a calibration certificate.",
        "",
        f"- Ports: {summary['ports']}",
        (
            f"- Frequency range: {summary['frequency_start_ghz']:.3g} to "
            f"{summary['frequency_stop_ghz']:.3g} GHz"
        ),
        f"- Fixture removed with a thru: {'yes' if summary['fixture_deembedded'] else 'no'}",
        (
            "- Open/short reflection correction: "
            f"{'yes' if summary['open_short_correction'] else 'no'}"
        ),
        f"- Velocity factor used for distance: {summary['velocity_factor']:g}",
        "",
        "## Checks",
        "",
        "| Check | Worst value | At (GHz) | Limit | Result |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for _, row in checks.iterrows():
        limit = "not set" if pd.isna(row["limit"]) else f"{row['limit']:g}"
        result = (
            "not judged"
            if pd.isna(row["passed"]) or row["passed"] is None
            else ("pass" if row["passed"] else "fail")
        )
        freq = "" if pd.isna(row["worst_frequency_ghz"]) else f"{row['worst_frequency_ghz']:.3g}"
        lines.append(
            f"| {row['check']} | {row['worst_value']:.4g} {row['unit']} | {freq} | "
            f"{limit} | {result} |"
        )
    lines += ["", "## Discontinuities", ""]
    if discontinuities.empty:
        lines.append("No impedance change larger than the detection threshold was found.")
    else:
        lines += [
            "| One-way distance (cm) | Impedance (ohm) | Type |",
            "| ---: | ---: | --- |",
        ]
        for _, row in discontinuities.iterrows():
            lines.append(
                f"| {row['distance_m'] * 100:.2f} | {row['impedance_ohm']:.1f} | {row['type']} |"
            )
    eye = summary["eye"]
    lines += [
        "",
        "## Eye",
        "",
        (
            f"At {eye['bitrate_gbps']:g} Gb/s with PRBS-7, the best eye height is "
            f"{eye['eye_height_fraction']:.3f} of the full swing and the eye is open for "
            f"{eye['eye_width_ui']:.2f} of a unit interval."
        ),
        "",
        "## Files",
        "",
        "- `summary.json`: every setting, check and eye value.",
        "- `checks.csv`: the same checks as a table.",
        "- `discontinuities.csv`: the impedance features found in the TDR.",
        "- `tdr.csv`: impedance against distance.",
        "- `tdr.png`, `response.png`, `eye.png`: plots.",
        "",
    ]
    return "\n".join(lines)
