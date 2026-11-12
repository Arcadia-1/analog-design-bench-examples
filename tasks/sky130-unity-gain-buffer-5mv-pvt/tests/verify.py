#!/usr/bin/env python3
"""Electrical signoff for the Sky130 unity-gain buffer.

Three benches run at every point of the complete 5-corner x 3-supply x
3-temperature matrix (135 single-threaded ngspice runs, executed serially).
The nominal tt/1.80 V/27 C point runs first; if it does not pass every check,
the remaining 44 points are skipped and every check fails. Every scored value
must be printed exactly once, finite, between the bench's result markers, by a
run that exited cleanly without simulator errors. The reward is binary: 1 only
when every published requirement passes at every point.
"""

import json
import math
import os
import re
import subprocess
import tempfile
import time
import traceback
from itertools import product
from pathlib import Path

from utils import write_results


HERE = Path(__file__).resolve().parent
BENCHES = HERE / "benches"
DEFAULT_DESIGN = "/app/circuit.spi"
DEFAULT_MODEL = "/opt/sky130/continuous/sky130.lib.spice"
DESIGN = DEFAULT_DESIGN
MODEL = DEFAULT_MODEL
OUTPUT = Path("/logs/verifier")
CORNERS = ("tt", "ss", "ff", "sf", "fs")
SUPPLIES = (1.62, 1.80, 1.98)
TEMPERATURES = (-40, 27, 125)
NOMINAL = ("tt", 1.80, 27)
POINTS = (NOMINAL,) + tuple(point for point in product(CORNERS, SUPPLIES, TEMPERATURES) if point != NOMINAL)

TRACKING_MAX_V = 5e-3
GAIN_MIN_VV = 0.95
BANDWIDTH_MIN_HZ = 100e6
INPUT_CAPACITANCE_MAX_F = 0.5e-12
POWER_MAX_W = 0.5e-3
SETTLING_MAX_S = 10e-9
FINAL_ERROR_MAX_V = 5e-3
OVERSHOOT_MAX_V = 10e-3

BENCH_METRICS = {
    "tracking": ("tracking_error_v",),
    "ac": ("buffer_gain_vv", "buffer_bandwidth_hz", "input_capacitance_f"),
    "settling": (
        "quiescent_dut_power_w",
        "settling_rise_time_s",
        "settling_fall_time_s",
        "settling_rise_final_tracking_error_v",
        "settling_fall_final_tracking_error_v",
        "settling_overshoot_v",
        "settling_undershoot_v",
    ),
}
CHECK_NAMES = (
    "complete_signoff",
    "tracking_5mv",
    "gain_bandwidth_100mhz",
    "input_loading_0p5pf",
    "step_settling",
    "quiescent_power_0p5mw",
)
RESULT_BEGIN = "UGB_RESULTS_BEGIN"
RESULT_END = "UGB_RESULTS_END"
SIMULATOR_ERROR = re.compile(
    r"(?im)^\s*(?:error\b|fatal\b|doanalyses\s*:)|"
    r"\b(?:simulation|analysis|run)\b[^\n]*\baborted\b|timestep too small|"
    r"\bmeas\b[^\n]*\bfailed\b"
)
SCALAR = re.compile(r"\s*([a-z]\w*)\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?)\s*", re.I)


def point_name(point: tuple[str, float, int]) -> str:
    corner, supply, temperature = point
    return f"{corner}/{supply:.2f}V/{temperature:+d}C"


def substitutions(point: tuple[str, float, int]) -> dict[str, str]:
    corner, supply, temperature = point
    return {
        f'.lib "{DEFAULT_MODEL}" tt': f'.lib "{MODEL}" {corner}',
        f'.include "{DEFAULT_DESIGN}"': f'.include "{DESIGN}"',
        ".param supply=1.8": f".param supply={supply:.12g}",
        ".param temperature=27": f".param temperature={temperature}",
    }


def render_deck(bench: str, point: tuple[str, float, int]) -> str:
    source = (BENCHES / f"tb_{bench}.spi").read_text()
    for old, new in substitutions(point).items():
        if source.count(old) != 1:
            raise ValueError(f"ambiguous bench substitution in tb_{bench}.spi: {old}")
        source = source.replace(old, new)
    return source


def parse_results(output: str, metrics: tuple[str, ...]) -> dict[str, float]:
    """Read the marked result block; reject missing, extra, duplicate or non-finite values."""
    lines = output.splitlines()
    if lines.count(RESULT_BEGIN) != 1 or lines.count(RESULT_END) != 1:
        raise ValueError("missing or duplicate result markers")
    begin, end = lines.index(RESULT_BEGIN), lines.index(RESULT_END)
    if end < begin:
        raise ValueError("result markers out of order")
    values: dict[str, float] = {}
    for line in lines[begin + 1:end]:
        if not line.strip():
            continue
        match = SCALAR.fullmatch(line)
        if not match:
            raise ValueError(f"unexpected result line: {line.strip()[:120]}")
        key, value = match.group(1).lower(), float(match.group(2))
        if key not in metrics:
            raise ValueError(f"unexpected result {key}")
        if key in values:
            raise ValueError(f"duplicate result {key}")
        if not math.isfinite(value):
            raise ValueError(f"non-finite result {key}")
        values[key] = value
    missing = [metric for metric in metrics if metric not in values]
    if missing:
        raise ValueError("missing results: " + ", ".join(missing))
    return values


def run_bench(bench: str, point: tuple[str, float, int]) -> dict[str, object]:
    """Run one bench with one single-threaded ngspice process; failures become row errors."""
    row: dict[str, object] = {"bench": bench, "point": point, "name": point_name(point), "ok": False}
    started = time.monotonic()
    try:
        runs = OUTPUT / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"{bench}-{point[0]}-", dir=runs))
        row["evidence_dir"] = str(work)
        deck = work / f"tb_{bench}.spi"
        deck.write_text(render_deck(bench, point))
        (work / ".spiceinit").write_text("set num_threads=1\n")
        log_path = work / "ngspice.log"
        with log_path.open("w") as log:
            result = subprocess.run(
                ["ngspice", "-b", str(deck)], cwd=work,
                env={**os.environ, "OMP_NUM_THREADS": "1"}, text=True,
                stdout=log, stderr=subprocess.STDOUT,
            )
        output = log_path.read_text(errors="replace")
        row["returncode"] = result.returncode
        if result.returncode != 0:
            raise ValueError(f"ngspice exited {result.returncode}")
        if SIMULATOR_ERROR.search(output):
            raise ValueError("ngspice reported an analysis or measurement error")
        row.update(parse_results(output, BENCH_METRICS[bench]))
        row["ok"] = True
    except (OSError, ValueError, UnicodeError) as error:
        row["error"] = str(error)
    row["seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps({key: value for key, value in row.items() if key != "point"}), flush=True)
    return row


def run_signoff() -> tuple[list[dict[str, object]], list[tuple[str, bool, str]] | None]:
    """Run the nominal point, then (only if it passes) every other point; serial, no worker pools."""
    nominal = [run_bench(bench, NOMINAL) for bench in BENCH_METRICS]
    nominal_checks = evaluate(nominal, (NOMINAL,))
    if not all(ok for _, ok, _ in nominal_checks):
        return nominal, nominal_checks
    rest = [run_bench(bench, point) for bench in BENCH_METRICS for point in POINTS if point != NOMINAL]
    return nominal + rest, None


def bench_rows(rows: list[dict[str, object]], bench: str,
               points_expected: tuple[tuple[str, float, int], ...]) -> tuple[list[dict[str, object]], str | None]:
    selected = [row for row in rows if row.get("bench") == bench]
    points = [row.get("point") for row in selected]
    if len(selected) != len(points_expected) or set(points) != set(points_expected):
        return selected, f"{bench}: expected one run at each of {len(points_expected)} points"
    errors = [f"{row.get('name')}: {row.get('error', 'invalid run')}" for row in selected if row.get("ok") is not True]
    if errors:
        return selected, f"{bench} blocked: " + "; ".join(errors)
    try:
        for row in selected:
            for metric in BENCH_METRICS[bench]:
                if not math.isfinite(float(row[metric])):
                    raise ValueError(metric)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return selected, f"{bench} blocked: missing or non-finite {error}"
    return selected, None


def worst(rows: list[dict[str, object]], metric: str, pick) -> tuple[float, str]:
    row = pick(rows, key=lambda item: float(item[metric]))
    return float(row[metric]), str(row["name"])


def evaluate(rows: list[dict[str, object]],
             points: tuple[tuple[str, float, int], ...] = POINTS) -> list[tuple[str, bool, str]]:
    groups = {bench: bench_rows(rows, bench, points) for bench in BENCH_METRICS}
    expected = {(bench, point) for bench in BENCH_METRICS for point in points}
    seen = [(row.get("bench"), row.get("point")) for row in rows]
    complete = len(seen) == len(expected) and set(seen) == expected and all(error is None for _, error in groups.values())
    blocked = "; ".join(error for _, error in groups.values() if error) or "duplicate or unexpected runs"
    checks = [("complete_signoff", complete, f"valid runs={sum(row.get('ok') is True for row in rows)}/{len(expected)}"
               + ("" if complete else "; " + blocked))]

    tracking, error = groups["tracking"]
    if error:
        checks.append(("tracking_5mv", False, error))
    else:
        value, at = worst(tracking, "tracking_error_v", max)
        checks.append(("tracking_5mv", value <= TRACKING_MAX_V,
                       f"worst={value * 1e3:.6g}mV at {at} (max {TRACKING_MAX_V * 1e3:g}mV)"))

    ac, error = groups["ac"]
    if error:
        checks.append(("gain_bandwidth_100mhz", False, error))
        checks.append(("input_loading_0p5pf", False, error))
    else:
        gain, gain_at = worst(ac, "buffer_gain_vv", min)
        bandwidth, bandwidth_at = worst(ac, "buffer_bandwidth_hz", min)
        checks.append(("gain_bandwidth_100mhz", gain >= GAIN_MIN_VV and bandwidth >= BANDWIDTH_MIN_HZ,
                       f"worst gain={gain:.6g}V/V at {gain_at} (min {GAIN_MIN_VV:g}); "
                       f"worst bandwidth={bandwidth / 1e6:.6g}MHz at {bandwidth_at} (min {BANDWIDTH_MIN_HZ / 1e6:g}MHz)"))
        capacitance, capacitance_at = worst(ac, "input_capacitance_f", max)
        checks.append(("input_loading_0p5pf", capacitance <= INPUT_CAPACITANCE_MAX_F,
                       f"worst effective input capacitance at 100MHz={capacitance * 1e15:.6g}fF at {capacitance_at} "
                       f"(max {INPUT_CAPACITANCE_MAX_F * 1e15:g}fF)"))

    transient, error = groups["settling"]
    if error:
        checks.append(("step_settling", False, error))
        checks.append(("quiescent_power_0p5mw", False, error))
    else:
        limits = (
            ("settling_rise_time_s", SETTLING_MAX_S, 1e9, "ns"),
            ("settling_fall_time_s", SETTLING_MAX_S, 1e9, "ns"),
            ("settling_rise_final_tracking_error_v", FINAL_ERROR_MAX_V, 1e3, "mV"),
            ("settling_fall_final_tracking_error_v", FINAL_ERROR_MAX_V, 1e3, "mV"),
            ("settling_overshoot_v", OVERSHOOT_MAX_V, 1e3, "mV"),
            ("settling_undershoot_v", OVERSHOOT_MAX_V, 1e3, "mV"),
        )
        passed, parts = True, []
        for metric, limit, scale, unit in limits:
            value, at = worst(transient, metric, max)
            passed = passed and value <= limit
            parts.append(f"{metric}={value * scale:.6g}{unit} at {at} (max {limit * scale:g}{unit})")
        checks.append(("step_settling", passed, "; ".join(parts)))
        power, power_at = worst(transient, "quiescent_dut_power_w", max)
        checks.append(("quiescent_power_0p5mw", 0 <= power <= POWER_MAX_W,
                       f"worst={power * 1e3:.6g}mW at {power_at} (max {POWER_MAX_W * 1e3:g}mW)"))
    return checks


def main() -> None:
    started = time.monotonic()
    rows: list[dict[str, object]] = []
    try:
        rows, nominal_checks = run_signoff()
        if nominal_checks is None:
            checks = evaluate(rows)
        else:
            checks = [(name, False, f"nominal {'PASS' if ok else 'FAIL'}: {message}; "
                       "nominal prerequisite failed, remaining PVT points not run")
                      for name, ok, message in nominal_checks]
    except Exception:  # never crash: any verifier fault is a zero with diagnostics
        diagnostic = traceback.format_exc(limit=4).strip().splitlines()[-1]
        checks = [(name, False, f"verifier error: {diagnostic}") for name in CHECK_NAMES]
    write_results(checks, OUTPUT, strict=True)
    points_run = len({row.get("point") for row in rows})
    print(f"pvt_points_run={points_run}/{len(POINTS)} ngspice_runs={len(rows)} "
          f"wall_clock_s={time.monotonic() - started:.3f}", flush=True)


if __name__ == "__main__":
    main()
