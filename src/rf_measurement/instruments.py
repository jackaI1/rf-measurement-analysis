"""Small SCPI driver: explicit sessions, bounded read retries and RF output cleanup."""

import time
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import pyvisa
from pyvisa.constants import StatusCode
from pyvisa.errors import VisaIOError

GENERATOR = "TCPIP::generator::INSTR"
ANALYZER = "TCPIP::analyzer::INSTR"


def simulated_backend():
    return f"{Path(__file__).with_name('instruments.yaml')}@sim"


class SCPI:
    def __init__(self, resource, retries=2, retry_delay_s=0.05):
        self.resource = resource
        self.retries = retries
        self.retry_delay_s = retry_delay_s
        self.timeout_count = 0

    def write(self, command):
        self.resource.write(command)

    def query(self, command):
        """Retry only before receiving any bytes of this ASCII/newline response.

        A mid-response timeout aborts: PyVISA can discard partially read bytes,
        and retrying then could accept a corrupted suffix as a complete response.
        """
        self.write(command)
        for attempt in range(self.retries + 1):
            try:
                first = self.resource.read_bytes(1)
                break
            except VisaIOError as error:
                if error.error_code != StatusCode.error_timeout:
                    raise
                self.timeout_count += 1
                if attempt == self.retries:
                    raise
                time.sleep(self.retry_delay_s)
        if first == b"\n":
            return ""
        try:
            return first.decode(self.resource.encoding) + self.resource.read()
        except VisaIOError as error:
            if error.error_code == StatusCode.error_timeout:
                self.timeout_count += 1
            raise


class Bench:
    def __init__(
        self,
        backend,
        generator=GENERATOR,
        analyzer=ANALYZER,
        timeout_ms=1000,
        retries=2,
        retry_delay_s=0.05,
    ):
        self.backend = backend
        self.addresses = (generator, analyzer)
        self.timeout_ms = timeout_ms
        self.retries = retries
        self.retry_delay_s = retry_delay_s

    def __enter__(self):
        self.stack = ExitStack()
        try:
            manager = pyvisa.ResourceManager(self.backend)
            self.stack.callback(manager.close)
            clients = []
            for address in self.addresses:
                resource = manager.open_resource(
                    address, read_termination="\n", write_termination="\n", timeout=self.timeout_ms
                )
                self.stack.callback(resource.close)
                clients.append(SCPI(resource, self.retries, self.retry_delay_s))
            self.generator, self.analyzer = clients
            # Register shutdown before any user code can enable output.
            self.stack.callback(self.generator.write, "OUTP 0")
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *exception):
        return self.stack.__exit__(*exception)

    def trace(self, center_hz, span_hz):
        self.analyzer.write(f"FREQ:CENT {center_hz:.6f}")
        self.analyzer.write(f"FREQ:SPAN {span_hz:.6f}")
        self.analyzer.write("INIT:CONT OFF")
        self.analyzer.write("INIT:IMM")
        if self.analyzer.query("*OPC?").strip() != "1":
            raise ValueError("Analyzer did not confirm acquisition completion")
        raw = self.analyzer.query("TRAC:DATA? TRACE1")
        try:
            power = np.array([float(value) for value in raw.split(",")])
        except ValueError as error:
            raise ValueError("Analyzer returned a malformed ASCII trace") from error
        count = int(self.analyzer.query("SWE:POIN?"))
        center = float(self.analyzer.query("FREQ:CENT?"))
        span = float(self.analyzer.query("FREQ:SPAN?"))
        if (
            power.size != count
            or count < 2
            or not np.all(np.isfinite(power))
            or not np.isfinite(center)
            or not np.isfinite(span)
            or span <= 0
        ):
            raise ValueError("Invalid analyzer trace length, samples or frequency axis")
        return np.linspace(center - span / 2, center + span / 2, count), power, raw
