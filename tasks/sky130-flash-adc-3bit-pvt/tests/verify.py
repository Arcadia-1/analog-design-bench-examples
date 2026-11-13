#!/usr/bin/env python3
"""Grade the Sky130 3-bit flash ADC: transfer and coherent-sine benches across PVT, under the signoff rules."""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import signoff_ngspice  # ngspice under the signoff rules (see signoff_ngspice.py)
from utils import parse_measures, write_results

OUTPUT = Path("/logs/verifier")
DESIGN = Path("/app/circuit.spi")
BENCHES = Path(__file__).resolve().parent / "benches"
NGSPICE = os.environ.get("NGSPICE", "ngspice")

# ---- public contract (spec/README.md) ----
LSB_V = 0.1
WARMUP = 2                      # conversions 0 and 1 of every bench are not scored
LOW_MAX, HIGH_MIN = 0.2, 0.8    # valid logic levels, as fractions of vdd
SNDR_MIN_DB = 22.0
SFDR_MIN_DB = 24.5
POWER_MAX_W = 1.0e-3
SINE_SAMPLES = 16
SINE_BIN = 1                    # hidden tone: bin 1 of 16 at 50 MS/s = 3.125 MHz

# Hidden transfer sequence (tb_transfer.spi): expected code of every conversion, and whether its input is a
# code center or lies 0.3 LSB from a threshold.
TRANSFER = (
    (0.65, 0), (1.35, 7),
    (0.95, 3), (1.05, 4), (0.97, 3), (0.65, 0), (1.03, 4), (1.35, 7), (0.67, 0), (0.85, 2), (0.73, 1), (1.25, 6),
    (1.27, 6), (0.75, 1), (1.33, 7), (1.15, 5), (0.87, 2), (0.65, 0), (0.93, 3), (1.35, 7), (1.17, 5), (0.85, 2),
    (1.23, 6), (1.05, 4), (0.77, 1), (1.35, 7), (0.83, 2), (0.75, 1), (1.07, 4), (1.35, 7), (0.65, 0), (1.13, 5),
    (0.95, 3),
)

TRANSFER_POINTS = (  # (corner, vdd, temperature); the first one is the gate
    ("tt", 1.80, 27), ("ss", 1.62, -40), ("ss", 1.62, 125), ("ff", 1.98, -40), ("ff", 1.98, 125),
    ("sf", 1.62, -40), ("fs", 1.98, 125),
)
SINE_POINTS = (
    ("tt", 1.80, 27), ("ss", 1.62, -40), ("ff", 1.98, 125), ("sf", 1.98, 125), ("fs", 1.62, -40),
)
CASES = [{"bench": "transfer", "corner": c, "vdd": v, "temp": t} for c, v, t in TRANSFER_POINTS] + \
        [{"bench": "sine", "corner": c, "vdd": v, "temp": t} for c, v, t in SINE_POINTS]
CHECKS = ("code_centers", "thresholds_0p3lsb", "output_timing", "sndr_22db", "sfdr_24p5db", "power_1mw")


def label(case: dict) -> str:
    return f'{case["bench"]} {case["corner"]}/{case["vdd"]:.2f} V/{case["temp"]} C'


def render(text: str, case: dict) -> str:
    vdd = f'{case["vdd"]:.2f}'
    text = text.replace('sky130.lib.spice" tt', f'sky130.lib.spice" {case["corner"]}', 1)
    text = text.replace('.include "/app/circuit.spi"', f'.include "{DESIGN}"', 1)
    text = re.sub(r"(?m)^\.temp 27$", f'.temp {case["temp"]}', text, count=1)
    text = re.sub(r"(?m)^VSUP vdd vss 1\.8$", f"VSUP vdd vss {vdd}", text, count=1)
    return text.replace("PULSE(0 1.8 ", f"PULSE(0 {vdd} ", 1)


def run_case(case: dict) -> dict:
    """Simulate one bench at one point and reduce it to its metrics; a failed run carries an `error`."""
    with tempfile.TemporaryDirectory(prefix="flash3-") as work:
        deck = Path(work) / f'tb_{case["bench"]}.spi'
        deck.write_text(render((BENCHES / deck.name).read_text(), case))
        started = time.monotonic()
        result = signoff_ngspice.run([NGSPICE, "-b", str(deck)], cwd=work, text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        row = {**case, "label": label(case), "seconds": round(time.monotonic() - started, 1)}
        if result.returncode:
            tail = " ".join((result.stdout or "").strip().splitlines()[-2:])[:300]
            return {**row, "error": f"ngspice exit {result.returncode}: {tail}"}
        measures = parse_measures(result.stdout)
    count = len(TRANSFER) if case["bench"] == "transfer" else WARMUP + SINE_SAMPLES
    vdd = case["vdd"]
    codes, bad_levels = [], []
    for k in range(WARMUP, count):
        code = 0
        for bit, weight in ((2, 4), (1, 2), (0, 1)):
            names = [f"s{k}_b{bit}", f"s{k}_b{bit}_min", f"s{k}_b{bit}_max"]
            if any(n not in measures for n in names):
                return {**row, "error": f"missing measurement {names[0]}"}
            read, low, high = (measures[n] for n in names)
            if read > 0.5 * vdd:
                code += weight
                if low < HIGH_MIN * vdd:
                    bad_levels.append(f"conversion {k} b{bit} fell to {low:.3f} V")
            elif high > LOW_MAX * vdd:
                bad_levels.append(f"conversion {k} b{bit} rose to {high:.3f} V")
        codes.append(code)
    if "power" not in measures:
        return {**row, "error": "missing power measurement"}
    row.update(codes=codes, bad_levels=bad_levels, power_w=measures["power"],
               **{f"power_{pin}_w": measures.get(f"power_{pin}", math.nan)
                  for pin in ("vdd", "vrefp", "vrefn", "clk", "vin")})
    if case["bench"] == "transfer":
        row["errors"] = [(k, TRANSFER[k][0], TRANSFER[k][1], code)
                         for k, code in zip(range(WARMUP, count), codes) if code != TRANSFER[k][1]]
    else:
        row["sndr_db"], row["sfdr_db"] = spectrum(codes)
    return row


def spectrum(codes: list[int]) -> tuple[float, float]:
    """SNDR and SFDR of a coherent record: DC removed, tone bin against every other bin up to Nyquist
    (the Nyquist bin at half weight, as a one-sided spectrum counts it)."""
    n = len(codes)
    mean = sum(codes) / n
    power = []
    for k in range(1, n // 2 + 1):
        re_ = sum((c - mean) * math.cos(2 * math.pi * k * i / n) for i, c in enumerate(codes))
        im_ = sum((c - mean) * math.sin(2 * math.pi * k * i / n) for i, c in enumerate(codes))
        power.append((re_ * re_ + im_ * im_) * (0.5 if k == n // 2 else 1.0))
    signal = power[SINE_BIN - 1]
    others = [p for k, p in enumerate(power, start=1) if k != SINE_BIN]
    if signal <= 0:
        return -math.inf, -math.inf
    noise, spur = sum(others), max(others)
    return (10 * math.log10(signal / noise) if noise > 0 else math.inf,
            10 * math.log10(signal / spur) if spur > 0 else math.inf)


def is_center(volts: float) -> bool:
    return abs((volts - 0.6) / LSB_V % 1 - 0.5) < 1e-6


def score(rows: list[dict]) -> list[tuple[str, bool, str]]:
    failed = [r for r in rows if "error" in r]
    done = [r for r in rows if "error" not in r]
    complete = not failed and len(done) == len(CASES)
    transfer = [r for r in done if r["bench"] == "transfer"]
    sine = [r for r in done if r["bench"] == "sine"]
    note = "" if complete else f" [incomplete: {'; '.join(r['label'] + ': ' + r['error'] for r in failed) or 'runs missing'}]"

    def errors_of(kind):
        out = []
        for r in transfer:
            for k, volts, want, got in r["errors"]:
                if is_center(volts) == (kind == "center"):
                    out.append(f"{r['label']}: conversion {k} vin={volts:.2f} V read {got}, want {want}")
        return out

    center_err, edge_err = errors_of("center"), errors_of("edge")
    level_err = [f"{r['label']}: {msg}" for r in done for msg in r["bad_levels"]]
    sndr = min(sine, key=lambda r: r["sndr_db"], default=None)
    sfdr = min(sine, key=lambda r: r["sfdr_db"], default=None)
    power = max(done, key=lambda r: r["power_w"], default=None)

    def worst(row, key, unit, scale=1.0):
        return f"worst {row[key] * scale:.3f} {unit} at {row['label']}" if row else "no result"

    power_msg = "no result" if not power else (
        worst(power, "power_w", "mW", 1e3) + f" (<= {POWER_MAX_W * 1e3:g}); by pin: "
        + ", ".join(f"{p} {power[f'power_{p}_w'] * 1e3:.3f}" for p in ("vdd", "vrefp", "vrefn", "clk", "vin")))
    checks = [
        ("code_centers", not center_err,
         f"{len(center_err)} wrong: " + "; ".join(center_err[:4]) if center_err else "every code center converts correctly"),
        ("thresholds_0p3lsb", not edge_err,
         f"{len(edge_err)} wrong: " + "; ".join(edge_err[:4]) if edge_err else "every input 0.3 LSB from a threshold converts correctly"),
        ("output_timing", not level_err,
         f"{len(level_err)} violations: " + "; ".join(level_err[:4]) if level_err else "every output valid from 9.5 ns to 19 ns after every edge"),
        ("sndr_22db", bool(sndr) and sndr["sndr_db"] >= SNDR_MIN_DB, worst(sndr, "sndr_db", "dB") + f" (>= {SNDR_MIN_DB:g})"),
        ("sfdr_24p5db", bool(sfdr) and sfdr["sfdr_db"] >= SFDR_MIN_DB, worst(sfdr, "sfdr_db", "dB") + f" (>= {SFDR_MIN_DB:g})"),
        ("power_1mw", bool(power) and power["power_w"] <= POWER_MAX_W, power_msg),
    ]
    # a check passes only when every run it depends on completed
    return [(name, ok and complete, message + note) for name, ok, message in checks]


def main() -> int:
    started = time.monotonic()
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    workers = max(1, min(4, cpus))
    rows: list[dict] = []
    gate_failed = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_case, case) for case in CASES]  # the gate (nominal transfer) is CASES[0]
        gate = futures[0].result()
        rows.append(gate)
        if "error" in gate or gate["errors"]:
            gate_failed = gate.get("error") or f"{len(gate['errors'])} wrong codes"
            for f in futures[1:]:
                f.cancel()
        for f in futures[1:]:
            if not f.cancelled():
                rows.append(f.result())
    if gate_failed:
        checks = [(name, False, f"blocked: nominal transfer run failed ({gate_failed})") for name in CHECKS]
    else:
        checks = score(rows)
    write_results(checks, OUTPUT)
    elapsed = time.monotonic() - started
    (OUTPUT / "summary.json").write_text(json.dumps({"wall_clock_s": round(elapsed, 1), "workers": workers,
                                                     "runs": rows}, indent=1, default=str) + "\n")
    print(f"{len(rows)} ngspice runs, {workers} workers, {elapsed:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
