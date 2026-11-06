import json
from dataclasses import replace

import numpy as np
import pytest
import pyvisa
from pyvisa.constants import StatusCode
from pyvisa.errors import VisaIOError

from rf_measurement.instruments import SCPI, Bench, simulated_backend
from rf_measurement.sweep import SweepConfig, run_sweep


def config(tmp_path):
    return SweepConfig(frequencies_hz=(1e6, 2e6), powers_dbm=(-20, -10), output=tmp_path)


def test_real_pyvisa_sim_state_and_trace():
    with Bench(simulated_backend()) as bench:
        bench.generator.write("FREQ 2500000")
        bench.generator.write("POW -12")
        assert float(bench.generator.query("FREQ?")) == 2500000
        assert float(bench.generator.query("POW?")) == -12
        assert "SIGNAL-GENERATOR" in bench.generator.query("*IDN?")
        assert "SPECTRUM-ANALYZER" in bench.analyzer.query("*IDN?")
        bench.generator.write("OUTP 1")
        frequency, power, raw = bench.trace(2.5e6, 20000)
        assert len(frequency) == 21
        assert frequency[0] == 2490000
        assert frequency[-1] == 2510000
        assert frequency[np.argmax(power)] == 2500000
        assert max(power) == -10
        assert raw.startswith("-90,")


def test_sweep_saves_cartesian_grid_raw_data_and_readbacks(tmp_path):
    folder = run_sweep(config(tmp_path))
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["mode"] == "simulation"
    assert len(manifest["points"]) == 4
    assert manifest["started_at"].endswith("+00:00")
    points = [json.loads((folder / name).read_text()) for name in manifest["points"]]
    assert [(p["frequency_hz"], p["power_dbm"]) for p in points] == [
        (1e6, -20),
        (1e6, -10),
        (2e6, -20),
        (2e6, -10),
    ]
    for p in points:
        assert p["readback_frequency_hz"] == p["frequency_hz"]
        assert p["readback_power_dbm"] == p["power_dbm"]
        assert p["acquired_at"].endswith("+00:00")
        raw = (folder / p["raw_file"]).read_text()
        values = np.loadtxt(folder / p["csv_file"], delimiter=",", skiprows=1)
        assert values.shape == (21, 2)
        assert values[10, 1] == -10
        assert len(raw.split(",")) == 21
    # Running twice must never overwrite an earlier capture.
    assert run_sweep(config(tmp_path)) != folder


def test_query_retries_delayed_read_without_resending_command(monkeypatch):
    with Bench(simulated_backend()) as bench:
        session = bench.generator.resource
        real_read = session.read_bytes
        writes = []
        original_write = session.write
        attempts = 0

        def write(command):
            writes.append(command)
            return original_write(command)

        def delayed_read(count):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise VisaIOError(StatusCode.error_timeout)
            return real_read(count)

        monkeypatch.setattr(session, "write", write)
        monkeypatch.setattr(session, "read_bytes", delayed_read)
        client = SCPI(session, retries=2, retry_delay_s=0)
        assert "SIGNAL-GENERATOR" in client.query("*IDN?")
        assert attempts == 2
        assert writes == ["*IDN?"]
        assert client.timeout_count == 1


def test_real_sim_timeout_is_bounded():
    with Bench(simulated_backend(), timeout_ms=1, retries=1, retry_delay_s=0) as bench:
        with pytest.raises(VisaIOError) as error:
            bench.analyzer.query("UNSUPPORTED?")
        assert error.value.error_code == StatusCode.error_timeout
        assert bench.analyzer.timeout_count == 2


def test_non_timeout_errors_are_not_retried(monkeypatch):
    with Bench(simulated_backend()) as bench:
        calls = []
        real_read = bench.generator.resource.read

        def fail():
            calls.append(1)
            raise VisaIOError(StatusCode.error_connection_lost)

        monkeypatch.setattr(bench.generator.resource, "read", fail)
        with pytest.raises(VisaIOError):
            bench.generator.query("*IDN?")
        assert len(calls) == 1
        # The injected connection fault did not consume the simulator response.
        # Drain it so later tests do not inherit this deliberately interrupted query.
        real_read()


def test_partial_failure_preserves_data_and_disables_output(tmp_path, monkeypatch):
    trace = Bench.trace
    calls = 0
    off = []
    closed = []
    write = SCPI.write
    close = pyvisa.resources.MessageBasedResource.close

    def fail_second(self, center, span):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise VisaIOError(StatusCode.error_timeout)
        return trace(self, center, span)

    def observe_write(self, command):
        if command == "OUTP 0":
            off.append(command)
        return write(self, command)

    def observe_close(self):
        closed.append(self.resource_name)
        return close(self)

    monkeypatch.setattr(Bench, "trace", fail_second)
    monkeypatch.setattr(SCPI, "write", observe_write)
    monkeypatch.setattr(pyvisa.resources.MessageBasedResource, "close", observe_close)
    with pytest.raises(VisaIOError):
        run_sweep(config(tmp_path))
    (folder,) = tmp_path.iterdir()
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert len(manifest["points"]) == 1
    assert (folder / manifest["points"][0]).exists()
    assert len(off) == 1
    assert len(set(closed)) == 2


@pytest.mark.parametrize("response", ["bad", "-1,nan,-3", "1,2", ",".join(["-10"] * 22)])
def test_trace_rejects_malformed_data(response, monkeypatch):
    with Bench(simulated_backend()) as bench:
        query = bench.analyzer.query
        monkeypatch.setattr(
            bench.analyzer,
            "query",
            lambda command: response if command == "TRAC:DATA? TRACE1" else query(command),
        )
        with pytest.raises(ValueError):
            bench.trace(1e6, 20000)


@pytest.mark.parametrize(
    "changes",
    [
        {"frequencies_hz": ()},
        {"powers_dbm": ()},
        {"frequencies_hz": (float("nan"),)},
        {"frequencies_hz": (-1,)},
        {"powers_dbm": (float("inf"),)},
        {"span_hz": 0},
        {"timeout_ms": 0},
        {"retries": -1},
        {"frequencies_hz": (1,)},
    ],
)
def test_invalid_sweep_has_no_output_side_effects(tmp_path, changes):
    with pytest.raises(ValueError):
        run_sweep(replace(config(tmp_path), **changes))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("timeout_call", [2, 3])
def test_timeout_after_partial_numeric_token_never_accepts_corrupt_trace(monkeypatch, timeout_call):
    with Bench(simulated_backend(), retries=1, retry_delay_s=0) as bench:
        session = bench.analyzer.resource
        session.chunk_size = 2
        original_read = session.visalib.read
        trace_active = False
        trace_reads = 0
        original_write = session.write

        def observe_write(command):
            nonlocal trace_active
            trace_active = command == "TRAC:DATA? TRACE1"
            return original_write(command)

        def interrupt_read(session_id, count):
            nonlocal trace_reads
            if trace_active:
                trace_reads += 1
                # The first small chunk has consumed part of '-90'.
                if trace_reads == timeout_call:
                    raise VisaIOError(StatusCode.error_timeout)
            return original_read(session_id, count)

        monkeypatch.setattr(session, "write", observe_write)
        monkeypatch.setattr(session.visalib, "read", interrupt_read)
        try:
            with pytest.raises(VisaIOError):
                bench.trace(1e6, 20000)
        finally:
            # Drain the deliberately interrupted sim response for later tests.
            monkeypatch.setattr(session.visalib, "read", original_read)
            try:
                session.read()
            except VisaIOError:
                pass
