"""Acquire each frequency/power combination and retain partial runs after failures."""

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np

from .instruments import ANALYZER, GENERATOR, Bench, simulated_backend


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, data):
    """Replace metadata atomically; a failed acquisition cannot truncate a manifest."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


@dataclass(frozen=True)
class SweepConfig:
    frequencies_hz: tuple = (1e6, 2e6, 3e6)
    powers_dbm: tuple = (-20, -10)
    span_hz: float = 20000
    output: Path = Path("measurements")
    backend: str | None = None
    generator: str = GENERATOR
    analyzer: str = ANALYZER
    timeout_ms: int = 1000
    retries: int = 2
    settle_s: float = 0

    def validate(self):
        if not self.frequencies_hz or not self.powers_dbm:
            raise ValueError("Provide at least one frequency and power")
        if (
            not math.isfinite(self.span_hz)
            or not 1 <= self.span_hz <= 1e9
            or not math.isfinite(self.settle_s)
            or self.settle_s < 0
        ):
            raise ValueError("Invalid span or settling time")
        if (
            not isinstance(self.timeout_ms, int)
            or self.timeout_ms <= 0
            or not isinstance(self.retries, int)
            or self.retries < 0
        ):
            raise ValueError("Timeout must be positive and retries a nonnegative integer")
        for frequency in self.frequencies_hz:
            if (
                not math.isfinite(frequency)
                or not 1 <= frequency <= 6e9
                or frequency < self.span_hz / 2
            ):
                raise ValueError("Frequency must be 1 Hz to 6 GHz with a nonnegative trace axis")
        for power in self.powers_dbm:
            if not math.isfinite(power) or not -120 <= power <= 20:
                raise ValueError("Power must be -120 to +20 dBm")


def run_sweep(config):
    config.validate()
    started = datetime.now(timezone.utc)
    folder = Path(config.output) / f"{started:%Y%m%dT%H%M%S.%fZ}-{uuid4().hex[:8]}"
    folder.mkdir(parents=True)
    backend = config.backend if config.backend is not None else simulated_backend()
    manifest = {
        "schema_version": 1,
        "started_at": started.isoformat(),
        "status": "running",
        "mode": "simulation" if backend.endswith("@sim") else "instrument",
        "generator_address": config.generator,
        "analyzer_address": config.analyzer,
        "frequencies_hz": list(config.frequencies_hz),
        "powers_dbm": list(config.powers_dbm),
        "span_hz": config.span_hz,
        "timeout_ms": config.timeout_ms,
        "retries": config.retries,
        "settle_s": config.settle_s,
        "points": [],
    }
    manifest_path = folder / "manifest.json"
    save_json(manifest_path, manifest)
    bench = None
    try:
        with Bench(
            backend, config.generator, config.analyzer, config.timeout_ms, config.retries
        ) as bench:
            manifest["generator_id"] = bench.generator.query("*IDN?")
            manifest["analyzer_id"] = bench.analyzer.query("*IDN?")
            for frequency in config.frequencies_hz:
                for power in config.powers_dbm:
                    bench.generator.write(f"FREQ {frequency:.6f}")
                    bench.generator.write(f"POW {power:.6f}")
                    frequency_readback = float(bench.generator.query("FREQ?"))
                    power_readback = float(bench.generator.query("POW?"))
                    if not math.isclose(
                        frequency_readback, frequency, abs_tol=1e-6
                    ) or not math.isclose(power_readback, power, abs_tol=1e-6):
                        raise ValueError("Generator settings did not match readback")
                    bench.generator.write("OUTP 1")
                    time.sleep(config.settle_s)
                    axis, trace, raw = bench.trace(frequency, config.span_hz)
                    prefix = f"point-{len(manifest['points']):04d}"
                    raw_name, csv_name = f"{prefix}.txt", f"{prefix}.csv"
                    (folder / raw_name).write_text(raw)
                    np.savetxt(
                        folder / csv_name,
                        np.column_stack((axis, trace)),
                        delimiter=",",
                        header="frequency_hz,power_dbm",
                        comments="",
                    )
                    metadata_name = f"{prefix}.json"
                    save_json(
                        folder / metadata_name,
                        {
                            "acquired_at": utc_now(),
                            "frequency_hz": frequency,
                            "power_dbm": power,
                            "readback_frequency_hz": frequency_readback,
                            "readback_power_dbm": power_readback,
                            "raw_file": raw_name,
                            "csv_file": csv_name,
                            "peak_frequency_hz": float(axis[np.argmax(trace)]),
                            "peak_power_dbm": float(np.max(trace)),
                        },
                    )
                    manifest["points"].append(metadata_name)
                    save_json(manifest_path, manifest)
        manifest["status"] = "complete"
    except BaseException as error:
        manifest["status"] = "failed"
        manifest["error_type"] = type(error).__name__
        raise
    finally:
        manifest["finished_at"] = utc_now()
        if bench is not None and hasattr(bench, "generator"):
            manifest["read_timeouts"] = bench.generator.timeout_count + bench.analyzer.timeout_count
        save_json(manifest_path, manifest)
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--frequencies",
        type=float,
        nargs="+",
        default=[1e6, 2e6, 3e6],
        help="Generator frequencies in Hz",
    )
    parser.add_argument("--powers", type=float, nargs="+", default=[-20, -10], help="dBm")
    parser.add_argument("--span", type=float, default=20000, help="Analyzer span in Hz")
    parser.add_argument("--output", type=Path, default=Path("measurements"))
    parser.add_argument("--backend", help="VISA backend, default bundled @sim; hardware: @ni")
    parser.add_argument("--generator", default=GENERATOR)
    parser.add_argument("--analyzer", default=ANALYZER)
    parser.add_argument("--timeout-ms", type=int, default=1000)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--settle-s", type=float, default=0)
    args = parser.parse_args()
    try:
        folder = run_sweep(
            SweepConfig(
                tuple(args.frequencies),
                tuple(args.powers),
                args.span,
                args.output,
                args.backend,
                args.generator,
                args.analyzer,
                args.timeout_ms,
                args.retries,
                args.settle_s,
            )
        )
    except ValueError as error:
        parser.error(str(error))
    print(folder)


if __name__ == "__main__":
    main()
